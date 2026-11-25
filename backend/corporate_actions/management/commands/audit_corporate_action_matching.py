"""Read-only: do NSE corporate actions match your holdings, and why not?

Fetches NSE corporate actions for every held stock (exactly as the nightly
scan does), then classifies each event against the matching rules without
writing anything:

* how many events the current rules match (exact bare symbol or exact
  company name, because the provider ISIN is ignored);
* why the rest do not match;
* how many would match with corporate_actions.nse_identity_matching=true.

Also writes data/reports/corporate_action_matching.csv. Needs access to
www.nseindia.com, like the scan itself.
"""
import csv
import io
from collections import Counter
from datetime import timedelta

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from config.pwms_config import section
from config.scheduling.files import atomic_write_text, reports_dir
from corporate_actions.identity import resolve_nse_symbol_isin
from corporate_actions.management.commands.monitor_corporate_actions import classify_exchange_action
from corporate_actions.providers.nse import NseCorporateActionsProvider, normalize_nse_symbol
from investments.models import Asset, AssetCategory


class Command(BaseCommand):
    help = "Read-only report of how NSE corporate actions match holdings. Changes nothing."

    def handle(self, *args, **options):
        cfg = section("corporate_actions")
        held = list(
            Asset.objects.filter(
                is_active=True, category=AssetCategory.STOCK, symbol__isnull=False, symbol__gt="",
                holding__quantity__gt=0,
            )
        )
        held_isins = {(asset.isin or "").strip().upper() for asset in held if asset.isin}
        stocks = Asset.objects.filter(is_active=True, category=AssetCategory.STOCK)
        symbols = sorted({normalize_nse_symbol(asset.symbol) for asset in held if asset.symbol})
        since = timezone.localdate() - timedelta(days=int(cfg.get("nse_history_days", 365)))
        until = timezone.localdate()
        provider = NseCorporateActionsProvider(
            timeout_seconds=cfg.get("nse_timeout_seconds", 20),
            max_response_bytes=cfg.get("nse_max_response_bytes", 5000000),
            request_delay_seconds=cfg.get("nse_request_delay_seconds", 0.25),
        )
        events, fetch_errors = [], 0
        for symbol in symbols:
            try:
                events.extend(provider.fetch_symbol(symbol, since, until))
            except (requests.RequestException, ValueError) as exc:
                fetch_errors += 1
                self.stderr.write(f"NSE fetch failed for {symbol}: {exc}")

        reasons = Counter()
        new_rules = Counter()
        matched_now = 0
        rows = []
        for event in events:
            symbol = (event.get("symbol") or "").strip().upper()
            company = event.get("company_name") or ""
            provider_isin = (event.get("provider_isin") or event.get("isin") or "").strip().upper()
            bare_match = stocks.filter(symbol__iexact=symbol).exists()
            name_match = bool(company) and stocks.filter(name__iexact=company).exists()
            suffix_only = not bare_match and stocks.filter(symbol__iexact=f"{symbol}.NS").exists()
            # Current rules (flag off): the provider ISIN is ignored; only an
            # exact bare symbol or exact company name finds a candidate.
            current = bare_match or name_match
            event_reasons = []
            if current:
                matched_now += 1
            else:
                if provider_isin:
                    event_reasons.append("provider ISIN dropped")
                else:
                    event_reasons.append("no provider ISIN")
                if suffix_only:
                    event_reasons.append("symbol stored with .NS suffix")
                elif not bare_match:
                    event_reasons.append("no holding with this symbol")
                if company and not name_match:
                    event_reasons.append("company name differs from asset name")
                if not classify_exchange_action(event.get("purpose")):
                    event_reasons.append("purpose not classified")
                reasons.update(event_reasons)

            if provider_isin:
                new_isin, method = provider_isin, "SOURCE_ISIN"
            else:
                new_isin, method = resolve_nse_symbol_isin(symbol)
            would_match = bool(new_isin and new_isin in held_isins)
            new_rules[method] += 1
            if would_match:
                new_rules["would_match"] += 1
            elif new_isin and (bare_match or suffix_only):
                # Same symbol is held, but under a different ISIN: typically
                # the ISIN changed after a face-value split. The scan fails
                # closed (manual identity review) instead of guessing.
                new_rules["isin_differs"] += 1
            rows.append({
                "symbol": symbol, "company": company, "ex_date": event.get("ex_date"),
                "purpose": event.get("purpose"), "provider_isin": provider_isin,
                "matched_now": current, "why_not": "; ".join(event_reasons),
                "new_rule_isin": new_isin or "", "new_rule_method": method, "would_match": would_match,
            })

        out = self.stdout
        out.write(f"Held stocks with a symbol: {len(held)} ({len(symbols)} NSE symbols, fetch errors: {fetch_errors})")
        out.write(f"NSE events: {len(events)}")
        out.write(f"matched holdings now: {matched_now}")
        out.write(f"not matched: {len(events) - matched_now}")
        for reason, count in reasons.most_common():
            out.write(f"  {reason}: {count}")
        out.write(
            f"would match with nse_identity_matching=true: {new_rules['would_match']} "
            f"(provider ISIN: {new_rules['SOURCE_ISIN']}, Security Master symbol: {new_rules['SYMBOL_SECURITY_MASTER']}, "
            f"EQUITY_L symbol: {new_rules['SYMBOL_EQUITY_L']}, ambiguous: {new_rules['AMBIGUOUS']}, "
            f"not found: {new_rules['NOT_FOUND']})"
        )
        if new_rules["isin_differs"]:
            out.write(
                f"  held under a different ISIN (ISIN changed after a split?): {new_rules['isin_differs']} "
                "- these stay in manual identity review; update the asset's ISIN if NSE's is current"
            )

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]) if rows else ["symbol"])
        writer.writeheader()
        writer.writerows(rows)
        path = reports_dir() / "corporate_action_matching.csv"
        try:
            atomic_write_text(path, buffer.getvalue())
            out.write(f"Details: {path}")
        except OSError as exc:
            self.stderr.write(f"Could not write {path}: {exc}")
