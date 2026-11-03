import logging
import re

import requests
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from config.pwms_config import section
from corporate_actions.models import CorporateAction, CorporateActionStatus, CorporateActionType
from corporate_actions.services import reconcile_action_for_all_users
from corporate_actions.providers.exchange import ConfiguredCorporateActionsProvider
from corporate_actions.providers.nse import NseCorporateActionsProvider, normalize_nse_symbol
from filing_intelligence.models import Filing
from investments.models import Asset, AssetCategory, AssetUnderlyingHolding, Holding
from mutual_funds.models import MutualFundTransaction
from mutual_funds.underlying_models import MutualFundUnderlying
from users.models import FamilyMembership

logger = logging.getLogger(__name__)
DATE_VALUE = (
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}[-./\s]+[A-Za-z]{3,9}[-./\s]+\d{4})"
)
DATE_PATTERNS = (
    ("ex_date", re.compile(r"(?i)\b(ex\s*[- ]?date|ex\s*[- ]?bonus\s*(?:basis\s*)?(?:from\s*)?date)\s*[:-]?\s*" + DATE_VALUE)),
    ("ex_date", re.compile(r"(?i)\bex\s*[- ]?bonus\s+basis\s+from\s+" + DATE_VALUE)),
    ("record_date", re.compile(r"(?i)\b(record\s*[- ]?date)(?:\s+for\s+the\s+purpose\s+of\s+[^:=;,.]+)?\s*(?:is|:|-)?\s*" + DATE_VALUE)),
    ("payment_date", re.compile(r"(?i)\b(payment\s*[- ]?date)\s*[:-]?\s*" + DATE_VALUE)),
)

def _parse_date(raw):
    value = raw.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    normalized = re.sub(r"[-./\s]+", " ", value)
    for fmt in ("%d %m %Y", "%Y %m %d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            pass
    return None

def classify_exchange_action(purpose):
    text = (purpose or "").lower()
    rules = (
        (CorporateActionType.STOCK_SPLIT, ("stock split", "sub-division", "subdivision", "face value split")),
        (CorporateActionType.BONUS, ("bonus",)),
        (CorporateActionType.CASH_DIVIDEND, ("dividend", "idcw")),
        (CorporateActionType.RIGHTS_ISSUE, ("rights",)),
        (CorporateActionType.MERGER, ("merger", "amalgamation")),
        (CorporateActionType.DEMERGER, ("demerger", "spin-off", "spin off")),
        (CorporateActionType.BUYBACK, ("buyback", "buy-back")),
        (CorporateActionType.REVERSE_SPLIT, ("consolidation of shares", "reverse split")),
    )
    matches = [kind for kind, terms in rules if any(term in text for term in terms)]
    return matches[0] if len(matches) == 1 else None

def build_symbol_isin_map(assets):
    """Map an NSE symbol only when held assets resolve it to one unambiguous ISIN."""
    symbol_isins = {}
    for asset in assets:
        symbol = normalize_nse_symbol(asset.symbol)
        isin = (asset.isin or "").strip().upper()
        if symbol and isin:
            symbol_isins.setdefault(symbol, set()).add(isin)
    return {
        symbol: next(iter(isins))
        for symbol, isins in symbol_isins.items()
        if len(isins) == 1
    }


def classify_filing(filing):
    subject = (filing.subject or "").strip().lower()
    details = (filing.details or "").strip().lower()
    filing_type = (filing.filing_type or "").strip().lower()
    text = " ".join([filing_type, subject, details])
    lifecycle = (
        "monitoring agency report", "allotment of securities", "allotment of shares",
        "extinguishment of shares", "copy of newspaper publication", "newspaper publication",
        "communication sent to shareholders", "email communication sent to shareholders",
        "post buy-back", "post buyback", "completion of buy-back", "completion of buyback",
    )
    if any(marker in subject or marker in filing_type for marker in lifecycle):
        return None
    # A standalone record-date notice supplements an existing action; it is not a new action.
    if re.search(r"^record\s*date$", subject):
        return None
    rules = (
        (CorporateActionType.STOCK_SPLIT, ("stock split", "sub-division of equity shares", "subdivision of equity shares", "split of equity shares"), ("approved", "proposed", "recommend", "subject to", "ratio", "record date", "ex-date", "ex date")),
        (CorporateActionType.BONUS, ("bonus issue", "issue of bonus shares", "issuance of bonus shares", "issuance of bonus share", "bonus equity shares", "bonus shares"), ("approved", "proposed", "recommend", "subject to", "ratio", "proportion", "record date", "ex-date", "ex date")),
        (CorporateActionType.CASH_DIVIDEND, ("dividend", "idcw"), ("declared", "declaration", "recommended", "recommendation", "interim dividend", "final dividend", "dividend per share", "payment date")),
        (CorporateActionType.RIGHTS_ISSUE, ("rights issue", "rights entitlement", "issue of shares on rights basis"), ("approve", "approved", "proposed", "offer", "issue price", "record date", "rights entitlement", "rights issue committee")),
        (CorporateActionType.MERGER, ("amalgamation", "merger"), ("scheme", "appointed date", "effective date", "approval", "approved", "order", "tribunal")),
        (CorporateActionType.DEMERGER, ("demerger", "spin-off", "spin off"), ("scheme", "appointed date", "effective date", "approval", "approved", "record date")),
        (CorporateActionType.BUYBACK, ("buyback", "buy-back"), ("board approved", "approved", "proposed", "tender offer", "record date", "buyback price", "buy-back price")),
        (CorporateActionType.REVERSE_SPLIT, ("consolidation of shares", "reverse stock split", "reverse split"), ("approved", "proposed", "ratio", "record date", "ex-date", "ex date")),
    )
    matches = [kind for kind, keywords, evidence in rules if any(k in text for k in keywords) and any(e in text for e in evidence)]
    return matches[0] if len(matches) == 1 else None

def extract_event_dates(filing):
    text = " ".join([filing.subject or "", filing.details or ""])
    result = {}
    for field, pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = _parse_date(match.group(match.lastindex))
            if parsed:
                if field in result and result[field] != parsed:
                    result.pop(field)
                    break
                result[field] = parsed
    return result

def _parse_cli_date(value, option_name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option_name} must use YYYY-MM-DD.") from exc

class Command(BaseCommand):
    help = "Ingest structured exchange corporate actions and match them to portfolio exposures."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=None)
        parser.add_argument("--since", type=str, default=None)
        parser.add_argument("--until", type=str, default=None)
        parser.add_argument("--source", choices=("nse", "feed", "filings", "both"), default="nse")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cfg = section("corporate_actions")
        if not cfg.get("enabled", True):
            self.stdout.write("Corporate action monitoring is disabled.")
            return
        since = _parse_cli_date(options.get("since"), "--since")
        until = _parse_cli_date(options.get("until"), "--until")
        if since and until and since > until:
            raise CommandError("--since cannot be later than --until.")
        if (since or until) and options.get("hours"):
            raise CommandError("Use either --hours or --since/--until, not both.")
        if options["source"] in {"feed", "both"} and not cfg.get("feed_url") and options["source"] == "feed":
            raise CommandError("No authorised corporate-actions feed configured. Set corporate_actions.feed_url.")

        hours = max(1, options["hours"] or int(cfg.get("lookback_hours", 168)))
        range_start = since or (timezone.localdate() - timedelta(days=max(1, (hours + 23) // 24)))
        range_end = until or timezone.localdate()
        source_records = []
        nse_fetch_errors = 0
        nse_records = []
        nse_symbols = set()
        if options["source"] in {"nse", "both"}:
            nse_range_start = since or (timezone.localdate() - timedelta(days=int(cfg.get("nse_history_days", 365))))
            held_assets = list(Asset.objects.filter(
                is_active=True,
                category=AssetCategory.STOCK,
                symbol__isnull=False,
                symbol__gt="",
                holding__quantity__gt=0,
            ).select_related("holding"))
            nse_symbols = {
                normalize_nse_symbol(asset.symbol)
                for asset in held_assets
                if asset.symbol and asset.symbol.strip()
            }
            symbol_isin_map = build_symbol_isin_map(held_assets)
            if nse_symbols:
                nse_provider = NseCorporateActionsProvider(
                    timeout_seconds=cfg.get("nse_timeout_seconds", 20),
                    max_response_bytes=cfg.get("nse_max_response_bytes", 5000000),
                    request_delay_seconds=cfg.get("nse_request_delay_seconds", 0.25),
                )
                for symbol in sorted(nse_symbols):
                    try:
                        nse_records.extend(nse_provider.fetch_symbol(symbol, nse_range_start, range_end))
                    except (requests.RequestException, ValueError) as exc:
                        logger.warning("NSE corporate-actions fetch failed for %s: %s", symbol, exc)
                        nse_fetch_errors += 1

        if options["source"] in {"feed", "both"} and cfg.get("feed_url"):
            provider = ConfiguredCorporateActionsProvider(
                cfg["feed_url"], timeout_seconds=cfg.get("feed_timeout_seconds", 30),
                max_response_bytes=cfg.get("feed_max_response_bytes", 10000000),
            )
            source_records = provider.fetch(range_start, range_end)

        filing_qs = Filing.objects.all()
        if since:
            filing_qs = filing_qs.filter(published_at__gte=timezone.make_aware(datetime.combine(since, time.min)))
        if until:
            filing_qs = filing_qs.filter(published_at__lt=timezone.make_aware(datetime.combine(until + timedelta(days=1), time.min)))
        if not since and not until:
            filing_qs = filing_qs.filter(published_at__gte=timezone.now() - timedelta(hours=hours))
        filings = list(filing_qs.order_by("-published_at")[:int(cfg.get("max_events_per_run", 500))]) if options["source"] in {"filings", "both"} else []

        User = get_user_model()
        created = updated = matched = skipped = 0

        def persist_event(event, source_name, source_reference, metadata):
            nonlocal created, updated, matched, skipped
            if (not event.get("action_type") or not event.get("isin") or not event.get("ex_date")
                    or event.get("status") in {"CANCELLED", "CANCELED", "DELETED"}):
                skipped += 1
                return
            isin, ex_date = event["isin"].upper(), event["ex_date"]
            defaults = {
                "security_name": event.get("company_name") or event.get("symbol") or isin,
                "isin": isin, "symbol": (event.get("symbol") or "").upper(),
                "exchange": event.get("exchange") or "", "ex_date": ex_date,
                "record_date": event.get("record_date"), "payment_date": event.get("payment_date"),
                "cash_per_unit": event.get("dividend_per_share"),
                "source": source_name, "source_reference": source_reference or "",
                "status": CorporateActionStatus.PENDING,
                "notes": "Imported from exchange corporate-actions data. Review purpose and entitlement details before confirmation.",
                "metadata": metadata,
            }
            if options["dry_run"]:
                created += 1
                return
            direct_assets = Asset.objects.filter(is_active=True, isin__iexact=isin)
            family_ids = set(direct_assets.exclude(family_id__isnull=True).values_list("family_id", flat=True))
            pms_family_ids = set(AssetUnderlyingHolding.objects.filter(isin__iexact=isin).values_list("family_id", flat=True))
            mf_scheme_ids = MutualFundUnderlying.objects.filter(isin__iexact=isin).values_list("scheme_id", flat=True)
            mf_owner_ids = MutualFundTransaction.objects.filter(scheme_id__in=mf_scheme_ids).exclude(owner__isnull=True).values_list("owner_id", flat=True)
            direct_owner_ids = direct_assets.exclude(owner__isnull=True).values_list("owner_id", flat=True)
            member_ids = FamilyMembership.objects.filter(family_group_id__in=family_ids | pms_family_ids).values_list("profile__user_id", flat=True)
            owners = set(direct_owner_ids) | set(mf_owner_ids) | set(member_ids)
            for owner in User.objects.filter(id__in=owners, is_active=True):
                with transaction.atomic():
                    action, was_created = CorporateAction.objects.update_or_create(
                        owner=owner, isin=isin, action_type=event["action_type"], ex_date=ex_date, defaults=defaults
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                    stats = reconcile_action_for_all_users(action)
                    matched += sum(stats.get(key, 0) for key in ("direct", "mutual_fund", "pms"))

        for record in nse_records:
            action_type = classify_exchange_action(record.get("purpose"))
            isin = (record.get("isin") or symbol_isin_map.get(record.get("symbol", "").upper()) or "").upper()
            if not isin:
                logger.warning(
                    "Skipping NSE corporate action for %s: held asset symbol does not resolve to one unique ISIN.",
                    record.get("symbol"),
                )
            event = {**record, "isin": isin, "action_type": action_type}
            persist_event(
                event, "NSE_CORPORATE_ACTIONS", record.get("source_url", ""),
                {"provider": "nse_corporate_actions_api", "purpose": record.get("purpose"),
                 "series": record.get("series"), "face_value": str(record.get("face_value")) if record.get("face_value") not in (None, "") else None,
                 "book_closure_start": record.get("book_closure_start").isoformat() if record.get("book_closure_start") else None,
                 "book_closure_end": record.get("book_closure_end").isoformat() if record.get("book_closure_end") else None,
                 "raw": record.get("raw", {}), "event_dates_verified": bool(record.get("ex_date"))},
            )

        for record in source_records:
            action_type = classify_exchange_action(record.get("purpose") or record.get("action_type"))
            event = {**record, "action_type": action_type}
            persist_event(
                event, f"{record['exchange']}_CORPORATE_ACTIONS_FEED", record.get("source_url", ""),
                {"provider": "configured_exchange_feed", "event_id": record.get("event_id"),
                 "purpose": record.get("purpose"), "ratio": record.get("ratio"),
                 "face_value": str(record.get("face_value")) if record.get("face_value") is not None else None,
                 "dividend_per_share": str(record.get("dividend_per_share")) if record.get("dividend_per_share") is not None else None,
                 "book_closure_start": record.get("book_closure_start").isoformat() if record.get("book_closure_start") else None,
                 "book_closure_end": record.get("book_closure_end").isoformat() if record.get("book_closure_end") else None,
                 "event_status": record.get("status"), "event_dates_verified": True},
            )
        for filing in filings:
            action_type, dates = classify_filing(filing), extract_event_dates(filing)
            if not action_type or not filing.isin or "ex_date" not in dates:
                skipped += 1
                continue
            event = {"action_type": action_type, "isin": filing.isin.upper(), "symbol": (filing.symbol or "").upper(),
                     "company_name": filing.company_name, "exchange": filing.exchange, "ex_date": dates["ex_date"],
                     "record_date": dates.get("record_date"), "payment_date": dates.get("payment_date"),
                     "dividend_per_share": None, "status": ""}
            persist_event(event, f"{filing.exchange}_FILING", filing.filing_url,
                          {"filing_id": filing.id, "announcement_date": filing.published_at.isoformat(),
                           "filing_subject": filing.subject, "filing_type": filing.filing_type,
                           "event_dates_verified": True})

        scanned = len(nse_records) + len(source_records) + len(filings)
        self.stdout.write(self.style.SUCCESS(
            f"Corporate action scan: scanned={scanned}, nse_symbols={len(nse_symbols)}, nse_scanned={len(nse_records)}, nse_fetch_errors={nse_fetch_errors}, "
            f"feed_scanned={len(source_records)}, filings_scanned={len(filings)}, created={created}, updated={updated}, "
            f"exposures={matched}, skipped={skipped}, dry_run={options['dry_run']}"
        ))
