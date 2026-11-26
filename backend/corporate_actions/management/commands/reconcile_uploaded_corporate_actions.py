"""Discover corporate actions affecting transactions that are already uploaded.

Discovery and reconciliation only. This command reads the transaction ledger,
finds every security that appears in it (including securities that are no
longer held), looks up corporate-action history for those securities, and for
each affected user records the event, the per-position proposal and a
notification on the Corporate Actions page. It never creates an adjustment
transaction and never changes a holding: the user accepts or rejects each
proposal on the Corporate Actions page.

``--dry-run`` is read-only: it performs every calculation, writes nothing to
the database and runs inside a transaction that is always rolled back.

Examples::

    python manage.py reconcile_uploaded_corporate_actions --dry-run
    python manage.py reconcile_uploaded_corporate_actions --since 2023-01-01 --until 2026-11-26 --dry-run
    python manage.py reconcile_uploaded_corporate_actions --since 2023-01-01 --until 2026-11-26
    python manage.py reconcile_uploaded_corporate_actions --source db --family-id 3 --report-json report.json
"""
import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta

import requests
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from config.pwms_config import section
from corporate_actions import reconciliation as ledger
from corporate_actions.audit import build_event_audit
from corporate_actions.management.commands.monitor_corporate_actions import (
    classify_exchange_action,
    classify_filing,
    extract_event_dates,
)
from corporate_actions.models import (
    CorporateAction,
    CorporateActionReconciliation,
    CorporateActionStatus,
)
from corporate_actions.providers.exchange import ConfiguredCorporateActionsProvider
from corporate_actions.providers.nse import NseCorporateActionsProvider, normalize_nse_symbol
from corporate_actions.services import (
    _indirect_exposures,
    discover_exposures_and_reconcile,
    discover_symbol_name_candidates,
    upsert_corporate_action,
)
from filing_intelligence.models import Filing
from investments.models import Asset, AssetCategory, Transaction
from users.models import FamilyMembership

logger = logging.getLogger(__name__)

SECURITY_CATEGORIES = (AssetCategory.STOCK, AssetCategory.ETF, AssetCategory.OTHER)
TERM_FIELDS = (
    "old_units", "new_units", "cash_per_unit", "resulting_isin", "resulting_symbol",
    "resulting_security_name", "cost_allocation_ratio", "cash_in_lieu_price",
)


class _DryRunRollback(Exception):
    """Raised to roll back everything a dry run might have touched."""


def _parse_date(value, option):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD.") from exc


class Command(BaseCommand):
    help = (
        "Discover corporate actions affecting already-uploaded transactions and create pending "
        "reconciliation notifications. Never creates adjustment transactions."
    )

    def add_arguments(self, parser):
        parser.add_argument("--since", type=str, default=None,
                            help="Earliest ex-date to examine (YYYY-MM-DD). Default: first transaction date.")
        parser.add_argument("--until", type=str, default=None,
                            help="Latest ex-date to examine (YYYY-MM-DD). Default: today.")
        parser.add_argument("--owner-id", type=int, action="append", default=[],
                            help="Only reconcile for this user (repeatable).")
        parser.add_argument("--family-id", type=int, action="append", default=[],
                            help="Only examine transactions of this family (repeatable).")
        parser.add_argument("--asset-id", type=int, action="append", default=[],
                            help="Only examine this asset (repeatable). Other Asset rows with the same ISIN are included.")
        parser.add_argument("--source", choices=("db", "nse", "feed", "filings", "all"), default="all",
                            help="Where to look for events: stored events (db), NSE, the configured feed, "
                                 "exchange filings, or all of them (default).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Calculate and report only; write nothing to the database.")
        parser.add_argument("--report-json", type=str, default=None,
                            help="Write a machine-readable report to this path.")

    # ------------------------------------------------------------------ scope

    def _transactions(self, options):
        rows = Transaction.objects.filter(asset__is_active=True, asset__category__in=SECURITY_CATEGORIES)
        if options["family_id"]:
            rows = rows.filter(family_id__in=options["family_id"])
        if options["owner_id"]:
            family_ids = list(FamilyMembership.objects.filter(
                profile__user_id__in=options["owner_id"]
            ).values_list("family_group_id", flat=True))
            rows = rows.filter(Q(family_id__in=family_ids) | Q(family__isnull=True, owner_id__in=options["owner_id"]))
        if options["asset_id"]:
            selected = Asset.objects.filter(id__in=options["asset_id"])
            isins = [isin.strip().upper() for isin in selected.exclude(isin__isnull=True).exclude(isin="").values_list("isin", flat=True)]
            asset_q = Q(asset_id__in=options["asset_id"])
            for isin in isins:
                asset_q |= Q(asset__isin__iexact=isin)
            rows = rows.filter(asset_q)
        return rows

    def _securities(self, transactions):
        """Distinct securities in the ledger, keyed by ISIN, else by bare symbol."""
        securities = {}
        rows = transactions.values(
            "asset_id", "asset__isin", "asset__symbol", "asset__name", "family_id", "owner_id", "transaction_date",
        ).order_by()
        for row in rows.iterator():
            isin = (row["asset__isin"] or "").strip().upper()
            symbol = normalize_nse_symbol(row["asset__symbol"]) if not str(row["asset__symbol"] or "").upper().endswith(".BO") else ""
            key = f"ISIN:{isin}" if isin else (f"SYMBOL:{symbol}" if symbol else None)
            if key is None:
                continue
            security = securities.setdefault(key, {
                "key": key, "isin": isin, "symbols": set(), "names": set(), "asset_ids": set(),
                "family_ids": set(), "personal_owner_ids": set(), "first_date": row["transaction_date"],
                "symbol_isins": defaultdict(set),
            })
            if symbol:
                security["symbols"].add(symbol)
                if isin:
                    security["symbol_isins"][symbol].add(isin)
            if row["asset__name"]:
                security["names"].add(row["asset__name"].strip())
            security["asset_ids"].add(row["asset_id"])
            if row["family_id"]:
                security["family_ids"].add(row["family_id"])
            elif row["owner_id"]:
                security["personal_owner_ids"].add(row["owner_id"])
            if row["transaction_date"] < security["first_date"]:
                security["first_date"] = row["transaction_date"]
        return securities

    def _owners_for(self, security, owner_filter):
        member_ids = set(FamilyMembership.objects.filter(
            family_group_id__in=security["family_ids"]
        ).values_list("profile__user_id", flat=True))
        owner_ids = member_ids | set(security["personal_owner_ids"])
        if owner_filter:
            owner_ids &= set(owner_filter)
        return list(get_user_model().objects.filter(id__in=owner_ids, is_active=True).order_by("id"))

    # ----------------------------------------------------------------- events

    def _db_events(self, securities, since, until):
        isins = {s["isin"] for s in securities.values() if s["isin"]}
        # Stored events without an ISIN (identity approval pending) are matched by symbol.
        symbols = {sym for s in securities.values() for sym in s["symbols"]}
        query = Q(isin__in=isins) if isins else Q(pk__in=[])
        if symbols:
            query |= Q(isin="", symbol__in=symbols)
        actions = CorporateAction.objects.filter(query, ex_date__gte=since, ex_date__lte=until)
        grouped = defaultdict(list)
        for action in actions:
            grouped[action.event_identity].append(action)
        events = []
        for copies in grouped.values():
            # Canonical copy: verified first, then the most complete terms, then the latest.
            copies.sort(key=lambda item: (
                item.status == CorporateActionStatus.CONFIRMED,
                sum(getattr(item, name) not in (None, "") for name in TERM_FIELDS),
                item.updated_at,
            ), reverse=True)
            canonical = copies[0]
            if canonical.status == CorporateActionStatus.REJECTED:
                continue
            events.append({
                "action_type": canonical.action_type, "isin": (canonical.isin or "").upper(),
                "symbol": (canonical.symbol or "").upper(), "exchange": canonical.exchange,
                "company_name": canonical.security_name, "ex_date": canonical.ex_date,
                "record_date": canonical.record_date, "payment_date": canonical.payment_date,
                **{name: getattr(canonical, name) for name in TERM_FIELDS},
                "source": canonical.source or "DATABASE", "source_reference": canonical.source_reference,
                "metadata": {"discovered_from_action_id": canonical.id, "identity_match_method": "STORED_EVENT"},
                "identity_method": "STORED_EVENT", "review_reasons": [],
            })
        return events

    def _provider_event(self, record, source_name, verified_isin, metadata):
        action_type = classify_exchange_action(record.get("purpose") or record.get("action_type"))
        audit_event = {**record, "action_type": action_type, "isin": verified_isin,
                       "source_isin": verified_isin, "resolved_isin": verified_isin}
        audit = build_event_audit(audit_event, source_name, record.get("source_url", ""), metadata)
        reasons = [reason for reason in audit.get("review_reasons", [])
                   if not reason.startswith("Source ISIN unavailable;")]
        return {
            "action_type": action_type, "isin": verified_isin,
            "symbol": (record.get("symbol") or "").upper(), "exchange": record.get("exchange") or "",
            "company_name": record.get("company_name") or record.get("symbol") or "",
            "ex_date": record.get("ex_date"), "record_date": record.get("record_date"),
            "payment_date": record.get("payment_date"),
            "old_units": audit.get("old_units"), "new_units": audit.get("new_units"),
            "cash_per_unit": audit.get("dividend_per_share"),
            "resulting_isin": "", "resulting_symbol": "", "resulting_security_name": "",
            "cost_allocation_ratio": None, "cash_in_lieu_price": None,
            "source": source_name, "source_reference": record.get("source_url", "") or "",
            "metadata": {**metadata, "identity_match_method": "VERIFIED_PROVIDER_ISIN" if verified_isin else ""},
            "identity_method": "VERIFIED_PROVIDER_ISIN" if verified_isin else "",
            "review_reasons": reasons,
            "cancelled": str(record.get("status") or "").upper() in {"CANCELLED", "CANCELED", "DELETED"},
        }

    def _nse_events(self, securities, since, until, cfg, stats):
        provider = NseCorporateActionsProvider(
            timeout_seconds=cfg.get("nse_timeout_seconds", 20),
            max_response_bytes=cfg.get("nse_max_response_bytes", 5000000),
            request_delay_seconds=cfg.get("nse_request_delay_seconds", 0.25),
        )
        events = []
        symbol_isins = defaultdict(set)
        for security in securities.values():
            for symbol, isins in security["symbol_isins"].items():
                symbol_isins[symbol].update(isins)
            for symbol in security["symbols"]:
                symbol_isins.setdefault(symbol, set())
        for symbol in sorted(symbol_isins):
            security_since = since
            try:
                records = provider.fetch_symbol(symbol, security_since, until)
            except (requests.RequestException, ValueError) as exc:
                logger.warning("NSE corporate-actions fetch failed for %s: %s", symbol, exc)
                stats["errors"] += 1
                stats["error_details"].append(f"NSE fetch failed for {symbol}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - an unexpected provider failure must not stop the run
                logger.exception("Unexpected NSE corporate-actions failure for %s", symbol)
                stats["errors"] += 1
                stats["error_details"].append(f"NSE fetch failed for {symbol}: {type(exc).__name__}: {exc}")
                continue
            held_isins = symbol_isins[symbol]
            for record in records:
                try:
                    self._append_nse_event(events, record, symbol, held_isins, stats)
                except Exception as exc:  # noqa: BLE001 - report the record and continue
                    logger.exception("Could not read NSE corporate-action record for %s", symbol)
                    stats["errors"] += 1
                    stats["error_details"].append(f"NSE record for {symbol} {record.get('ex_date')}: {exc}")
        return events

    def _append_nse_event(self, events, record, symbol, held_isins, stats):
        """Add one NSE record as an event, or report its identity conflict."""
        provider_isin = str(record.get("provider_isin") or record.get("isin") or "").strip().upper()
        # Verified: NSE's own ISIN for the event equals the one ISIN of the
        # held Asset whose symbol was queried. Otherwise identity approval.
        verified = provider_isin if (provider_isin and held_isins == {provider_isin}) else ""
        if provider_isin and held_isins and provider_isin not in held_isins:
            stats["ambiguous_identities"] += 1
            stats["ambiguous_details"].append(
                f"NSE {symbol} {record.get('ex_date')}: provider ISIN {provider_isin} differs from held "
                f"ISIN(s) {', '.join(sorted(held_isins))}"
            )
            return
        events.append(self._provider_event(record, "NSE_CORPORATE_ACTIONS", verified, {
            "provider": "nse_corporate_actions_api", "purpose": record.get("purpose"),
            "provider_isin": provider_isin or None, "raw": record.get("raw", {}),
            "event_dates_verified": bool(record.get("ex_date")),
        }))

    def _feed_events(self, securities, since, until, cfg, stats):
        if not cfg.get("feed_url"):
            return []
        try:
            records = ConfiguredCorporateActionsProvider(
                cfg["feed_url"], timeout_seconds=cfg.get("feed_timeout_seconds", 30),
                max_response_bytes=cfg.get("feed_max_response_bytes", 10000000),
            ).fetch(since, until)
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Corporate-actions feed fetch failed: %s", exc)
            stats["errors"] += 1
            stats["error_details"].append(f"Feed fetch failed: {exc}")
            return []
        except Exception as exc:  # noqa: BLE001 - an unexpected provider failure must not stop the run
            logger.exception("Unexpected corporate-actions feed failure")
            stats["errors"] += 1
            stats["error_details"].append(f"Feed fetch failed: {type(exc).__name__}: {exc}")
            return []
        isins = {s["isin"] for s in securities.values() if s["isin"]}
        events = []
        for record in records:
            isin = (record.get("isin") or "").upper()
            if isin not in isins:
                continue
            try:
                events.append(self._provider_event(record, f"{record['exchange']}_CORPORATE_ACTIONS_FEED", isin, {
                    "provider": "configured_exchange_feed", "event_id": record.get("event_id"),
                    "purpose": record.get("purpose"), "ratio": record.get("ratio"), "event_dates_verified": True,
                }))
            except Exception as exc:  # noqa: BLE001 - report the record and continue
                logger.exception("Could not read feed corporate-action record for %s", isin)
                stats["errors"] += 1
                stats["error_details"].append(f"Feed record for {isin} {record.get('ex_date')}: {exc}")
        return events

    def _filing_events(self, securities, since, until, stats):
        isins = {s["isin"] for s in securities.values() if s["isin"]}
        if not isins:
            return []
        filings = Filing.objects.filter(
            isin__in=isins,
            published_at__gte=timezone.make_aware(datetime.combine(since - timedelta(days=120), time.min)),
            published_at__lt=timezone.make_aware(datetime.combine(until + timedelta(days=1), time.min)),
        ).order_by("published_at")
        events = []
        for filing in filings:
            try:
                self._append_filing_event(events, filing, since, until)
            except Exception as exc:  # noqa: BLE001 - report the filing and continue
                logger.exception("Could not read filing %s", filing.id)
                stats["errors"] += 1
                stats["error_details"].append(f"Filing #{filing.id}: {exc}")
        return events

    def _append_filing_event(self, events, filing, since, until):
        """Add one exchange filing as an event when it is a classified, dated action."""
        action_type, dates = classify_filing(filing), extract_event_dates(filing)
        if not action_type or "ex_date" not in dates:
            return
        if not (since <= dates["ex_date"] <= until):
            return
        record = {"symbol": filing.symbol, "company_name": filing.company_name, "exchange": filing.exchange,
                  "ex_date": dates["ex_date"], "record_date": dates.get("record_date"),
                  "payment_date": dates.get("payment_date"), "purpose": f"{filing.subject} {filing.details}",
                  "source_url": filing.filing_url, "status": ""}
        event = self._provider_event(record, f"{filing.exchange}_FILING", filing.isin.upper(), {
            "filing_id": filing.id, "filing_subject": filing.subject, "event_dates_verified": True,
        })
        event["action_type"] = action_type
        events.append(event)

    # -------------------------------------------------------------- reconcile

    @staticmethod
    def _defaults(event):
        return {
            "security_name": (event["company_name"] or event["symbol"] or event["isin"])[:255],
            "isin": event["isin"], "symbol": event["symbol"], "exchange": event["exchange"] or "",
            "ex_date": event["ex_date"], "record_date": event["record_date"], "payment_date": event["payment_date"],
            **{name: event.get(name) for name in TERM_FIELDS if event.get(name) not in (None, "")},
            "source": event["source"][:40], "source_reference": (event["source_reference"] or "")[:1000],
            "status": CorporateActionStatus.PENDING,
            "notes": (
                "Discovered for already-uploaded transactions by reconcile_uploaded_corporate_actions. "
                "Review the terms; nothing is booked until you accept."
            ),
            "metadata": event["metadata"],
        }

    def _reconcile_event(self, event, security, owners, dry_run, stats, report_rows):
        lookup = (
            {"isin": event["isin"], "action_type": event["action_type"], "ex_date": event["ex_date"]}
            if event["isin"] else
            {"isin": "", "symbol": event["symbol"], "action_type": event["action_type"], "ex_date": event["ex_date"]}
        )
        defaults = self._defaults(event)
        for owner in owners:
            stored = CorporateAction.objects.filter(owner=owner, **lookup).first()
            existing = CorporateActionReconciliation.objects.filter(action=stored, owner=owner).first() if stored else None
            if existing is not None and ledger.is_terminal(existing):
                stats["terminal_unchanged"] += 1
                report_rows.append({"owner_id": owner.id, "event": _event_key(event), "status": existing.status,
                                    "note": "Already decided; not reopened."})
                continue
            probe = stored or CorporateAction(owner=owner, **{**defaults, **lookup})
            if not event["isin"]:
                candidates, method = discover_symbol_name_candidates(action=probe, user=owner)
                if not candidates:
                    continue
                stats["ambiguous_identities"] += 1
                stats["ambiguous_details"].append(
                    f"{event['symbol']} {event['action_type']} {event['ex_date']}: no verified ISIN; "
                    f"{len(candidates)} exact {method.lower()} candidate(s) need identity approval (user {owner.id})"
                )
                report_rows.append({"owner_id": owner.id, "event": _event_key(event), "status": "PENDING_USER_APPROVAL",
                                    "candidates": [asset.id for asset in candidates]})
                if not dry_run:
                    with db_transaction.atomic():
                        action, created = upsert_corporate_action(owner=owner, lookup=lookup, defaults=defaults)
                        result = discover_exposures_and_reconcile(action)
                    stats["events_created" if created else "events_updated"] += 1
                    stats["new_notifications"] += result.get("notifications", 0)
                continue

            plan = ledger.build_plan(probe, owner)
            family_ids = ledger.owner_family_ids(owner)
            mf, pms = _indirect_exposures(probe, owner, family_ids, persist=False)
            relevant = [p for p in plan.positions if p.status != "NOT_ELIGIBLE"]
            stats["existing_transactions_checked"] += plan.transactions_checked
            if not relevant and not (mf or pms) and stored is None:
                continue
            counts = {status: plan.count(status) for status in ("PENDING", "ALREADY_RECORDED", "MANUAL_REVIEW")}
            stats["eligible_positions"] += len(relevant)
            stats["pending_positions"] += counts["PENDING"]
            stats["already_recorded"] += counts["ALREADY_RECORDED"]
            stats["manual_review"] += counts["MANUAL_REVIEW"]
            status, _ = ledger.plan_status(plan, has_indirect=bool(mf or pms))
            report_rows.append({
                "owner_id": owner.id, "event": _event_key(event), "status": status,
                "positions": [
                    {"family_id": p.family.id if p.family else None, "family_member": p.family_name,
                     "portfolio": p.portfolio, "asset_class": p.asset_class, "sub_class": p.sub_class,
                     "asset_name": p.asset_name, "underlying": p.underlying, "status": p.status,
                     "eligible_quantity": str(p.eligible_quantity),
                     "quantity_delta": str(p.result.quantity_delta) if p.result and p.result.quantity_delta is not None else None,
                     "matched_transaction_ids": [tx.id for tx in p.matched], "reason": p.reason}
                    for p in plan.positions
                ],
            })
            if dry_run:
                if ledger.should_notify(plan, status, has_indirect=bool(mf or pms)) and existing is None:
                    stats["notifications_that_would_be_created"] += 1
                continue
            with db_transaction.atomic():
                action, created = upsert_corporate_action(owner=owner, lookup=lookup, defaults=defaults)
                result = discover_exposures_and_reconcile(action)
            stats["events_created" if created else "events_updated"] += 1
            stats["new_notifications"] += result.get("notifications", 0)

    # ----------------------------------------------------------------- handle

    def handle(self, *args, **options):
        cfg = section("corporate_actions")
        since = _parse_date(options.get("since"), "--since")
        until = _parse_date(options.get("until"), "--until") or timezone.localdate()
        if since and since > until:
            raise CommandError("--since cannot be later than --until.")
        dry_run = bool(options.get("dry_run"))
        stats = defaultdict(int)
        stats["error_details"] = []
        stats["ambiguous_details"] = []
        stats["insufficient_details"] = []
        report_rows = []

        try:
            if dry_run:
                # Belt and braces: the dry run only reads, and anything it might
                # touch is rolled back.
                try:
                    with db_transaction.atomic():
                        self._run(options, cfg, since, until, dry_run, stats, report_rows)
                        raise _DryRunRollback()
                except _DryRunRollback:
                    pass
            else:
                # No long-lived transaction across network fetches; every owner's
                # event is persisted in its own atomic block.
                self._run(options, cfg, since, until, dry_run, stats, report_rows)
        except DatabaseError as exc:
            # Failures inside one event are isolated in _run; this is the ledger
            # read itself (or the database going away). Nothing was booked.
            logger.exception("Corporate-action discovery could not read the database")
            raise CommandError(
                f"Could not read the transaction ledger: {exc}. No transaction or holding was changed."
            ) from exc

        summary = {key: stats[key] for key in (
            "securities_examined", "transactions_examined", "corporate_actions_discovered",
            "existing_transactions_checked", "eligible_positions", "pending_positions", "already_recorded",
            "new_notifications", "notifications_that_would_be_created", "ambiguous_identities",
            "manual_review", "insufficient_data", "terminal_unchanged", "events_created", "events_updated", "errors",
        )}
        if options.get("report_json"):
            report = {
                "schema_version": 1,
                "generated_at": timezone.now().isoformat(),
                "dry_run": dry_run,
                "source": options["source"],
                "date_range": {"since": stats.get("since_used"), "until": until.isoformat()},
                "filters": {name: options[name] for name in ("owner_id", "family_id", "asset_id")},
                "summary": summary,
                "ambiguous": stats["ambiguous_details"],
                "insufficient_data": stats["insufficient_details"],
                "errors": stats["error_details"],
                "reconciliations": report_rows,
            }
            try:
                with open(options["report_json"], "w", encoding="utf-8") as handle:
                    json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
            except OSError as exc:
                raise CommandError(f"Could not write report {options['report_json']!r}: {exc}") from exc

        for line in stats["ambiguous_details"][:20]:
            self.stdout.write(f"  ambiguous: {line}")
        for line in stats["insufficient_details"][:20]:
            self.stdout.write(f"  insufficient data: {line}")
        for line in stats["error_details"][:20]:
            self.stderr.write(f"  error: {line}")
        self.stdout.write(self.style.SUCCESS(
            "Uploaded-transaction corporate-action reconciliation: "
            + ", ".join(f"{key}={value}" for key, value in summary.items())
            + f", dry_run={dry_run}"
        ))

    def _run(self, options, cfg, since, until, dry_run, stats, report_rows):
        transactions = self._transactions(options)
        stats["transactions_examined"] = transactions.count()
        securities = self._securities(transactions)
        stats["securities_examined"] = len(securities)
        if not securities:
            return
        earliest = min(s["first_date"] for s in securities.values())
        since = since or earliest
        stats["since_used"] = since.isoformat()
        source = options["source"]
        events = []
        if source in ("db", "all"):
            events.extend(self._db_events(securities, since, until))
        if source in ("nse", "all"):
            events.extend(self._nse_events(securities, since, until, cfg, stats))
        if source in ("feed", "all"):
            events.extend(self._feed_events(securities, since, until, cfg, stats))
        if source in ("filings", "all"):
            events.extend(self._filing_events(securities, since, until, stats))

        by_isin = {s["isin"]: s for s in securities.values() if s["isin"]}
        by_symbol = {}
        for security in securities.values():
            for symbol in security["symbols"]:
                by_symbol.setdefault(symbol, security)

        unique = {}
        for event in events:
            if not event.get("action_type") or not event.get("ex_date"):
                stats["insufficient_data"] += 1
                stats["insufficient_details"].append(
                    f"{event.get('symbol') or event.get('isin')} {event.get('ex_date')}: purpose not classified or ex-date missing"
                )
                continue
            if event.get("cancelled"):
                continue
            if not (since <= event["ex_date"] <= until):
                # Never trust a provider to honour the requested range.
                continue
            key = _event_key(event)
            if key in unique:
                # Prefer the copy with more terms (stored events carry user-entered terms).
                current = unique[key]
                if sum(event.get(n) not in (None, "") for n in TERM_FIELDS) > sum(current.get(n) not in (None, "") for n in TERM_FIELDS):
                    unique[key] = event
                continue
            unique[key] = event
        # A symbol-only copy of an event that is also known with a verified ISIN
        # is the same event: keep the ISIN copy only.
        isin_events = {
            (event["isin"], event["action_type"], event["ex_date"])
            for event in unique.values() if event["isin"]
        }
        for key in [key for key, event in unique.items() if not event["isin"]]:
            event = unique[key]
            security = by_symbol.get(event["symbol"])
            if security and security["isin"] and (security["isin"], event["action_type"], event["ex_date"]) in isin_events:
                del unique[key]
        stats["corporate_actions_discovered"] = len(unique)

        owner_filter = options["owner_id"]
        for key, event in sorted(unique.items(), key=lambda item: (item[1]["ex_date"], item[0])):
            if event["review_reasons"]:
                stats["insufficient_details"].append(f"{key}: {'; '.join(event['review_reasons'])}")
            security = by_isin.get(event["isin"]) if event["isin"] else by_symbol.get(event["symbol"])
            if security is None:
                continue
            owners = self._owners_for(security, owner_filter)
            try:
                with db_transaction.atomic():
                    self._reconcile_event(event, security, owners, dry_run, stats, report_rows)
            except Exception as exc:  # noqa: BLE001 - report and continue with the next event
                logger.exception("Could not reconcile corporate action %s", key)
                stats["errors"] += 1
                stats["error_details"].append(f"{key}: {exc}")


def _event_key(event):
    identity = event.get("isin") or f"SYMBOL:{event.get('symbol')}"
    ex_date = event.get("ex_date")
    return f"{identity}|{event.get('action_type')}|{ex_date.isoformat() if hasattr(ex_date, 'isoformat') else ex_date}"
