from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from filing_intelligence.models import Filing
from filing_intelligence.services.matching import (
    FILING_MATCHABLE_CATEGORIES,
    match_assets,
    match_user_watchlists,
    users_for_holding,
)
from investments.models import Holding, Asset


class Command(BaseCommand):
    help = (
        "Diagnose stored corporate filings against active STOCK/ETF assets, "
        "holdings, watchlists, and existing Portfolio News alerts."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Inspect filings published within this many hours (default: 24).",
        )
        parser.add_argument(
            "--exchange",
            action="append",
            choices=["nse", "bse"],
            help="Limit diagnostics to an exchange. May be supplied more than once.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Maximum unmatched examples to print (default: 10).",
        )

    def handle(self, *args, **options):
        hours = max(1, options["hours"])
        limit = max(0, options["limit"])
        exchanges = [value.upper() for value in (options.get("exchange") or [])]
        since = timezone.now() - timedelta(hours=hours)

        queryset = Filing.objects.filter(published_at__gte=since)
        if exchanges:
            queryset = queryset.filter(exchange__in=exchanges)

        filings = list(queryset.select_related("security").order_by("-published_at", "-id"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Corporate filing diagnostics: last {hours} hour(s)"
                + (f", exchanges={','.join(exchanges)}" if exchanges else "")
            )
        )
        self.stdout.write(f"Stored filings inspected: {len(filings)}")

        if not filings:
            self.stdout.write("No stored filings found in the requested window.")
            return

        counters = {
            "with_isin": 0,
            "with_symbol": 0,
            "with_bse_code": 0,
            "matched_isin": 0,
            "matched_symbol": 0,
            "matched_bse_code": 0,
            "matched_company_name": 0,
            "unmatched": 0,
            "holding_matches": 0,
            "watchlist_matches": 0,
            "existing_alerts": 0,
        }
        unmatched_examples = []

        for filing in filings:
            if filing.isin:
                counters["with_isin"] += 1
            if filing.symbol:
                counters["with_symbol"] += 1
            if filing.bse_code:
                counters["with_bse_code"] += 1

            assets, method = match_assets(filing)
            if method == "ISIN":
                counters["matched_isin"] += 1
            elif method == "EXCHANGE_SYMBOL":
                counters["matched_symbol"] += 1
            elif method == "BSE_CODE":
                counters["matched_bse_code"] += 1
            elif method == "COMPANY_NAME":
                counters["matched_company_name"] += 1
            else:
                counters["unmatched"] += 1
                if len(unmatched_examples) < limit:
                    unmatched_examples.append(
                        self._unmatched_detail(filing)
                    )

            holdings = list(
                Holding.objects.filter(
                    asset__in=assets,
                    quantity__gt=0,
                ).select_related("owner", "family", "asset")
            )
            holding_user_ids = set()
            for holding in holdings:
                holding_user_ids.update(
                    user.id for user in users_for_holding(holding)
                )

            counters["holding_matches"] += len(holding_user_ids)
            watchlists = match_user_watchlists(filing)
            counters["watchlist_matches"] += len(
                {entry.user_id for entry in watchlists}
            )

            counters["existing_alerts"] += filing.portfolio_news_alerts.count()

        self.stdout.write("")
        self.stdout.write("Identifier coverage:")
        self.stdout.write(f"  filings with ISIN:       {counters['with_isin']}")
        self.stdout.write(f"  filings with symbol:     {counters['with_symbol']}")
        self.stdout.write(f"  filings with BSE code:   {counters['with_bse_code']}")

        self.stdout.write("")
        self.stdout.write("Portfolio matching:")
        self.stdout.write(f"  ISIN matches:            {counters['matched_isin']}")
        self.stdout.write(f"  symbol matches:          {counters['matched_symbol']}")
        self.stdout.write(f"  BSE code matches:        {counters['matched_bse_code']}")
        self.stdout.write(f"  company-name matches:    {counters['matched_company_name']}")
        self.stdout.write(f"  unmatched:               {counters['unmatched']}")

        self.stdout.write("")
        self.stdout.write("Downstream coverage:")
        self.stdout.write(
            f"  holding-user matches:    {counters['holding_matches']}"
        )
        self.stdout.write(
            f"  unique watchlist users:  {counters['watchlist_matches']}"
        )
        self.stdout.write(
            f"  existing filing alerts:  {counters['existing_alerts']}"
        )

        if unmatched_examples:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"Unmatched examples (up to {limit}):"
                )
            )
            for example in unmatched_examples:
                self.stdout.write(
                    "  "
                    f"{example['exchange']} {example['symbol'] or example['company_name']} "
                    f"| ISIN={example['isin'] or '-'} "
                    f"| symbol_candidates={example['symbol_candidates']} "
                    f"| isin_candidates={example['isin_candidates']} "
                    f"| bse_candidates={example['bse_candidates']} "
                    f"| name_candidates={example['name_candidates']} "
                    f"| reason={example['reason']}"
                )

        self.stdout.write("")
        self.stdout.write(
            "Matching scope: active assets in categories "
            + ", ".join(category.value for category in FILING_MATCHABLE_CATEGORIES)
            + "."
        )
        self.stdout.write(
            "No database records are modified by this diagnostic command."
        )

    @staticmethod
    def _unmatched_detail(filing):
        assets = Asset.objects.filter(
            is_active=True,
            category__in=FILING_MATCHABLE_CATEGORIES,
        ).select_related("security_master")

        isin_candidates = (
            assets.filter(
                Q(isin__iexact=filing.isin)
                | Q(security_master__isin__iexact=filing.isin)
            ).count()
            if filing.isin
            else 0
        )
        symbol_candidates = (
            assets.filter(symbol__iexact=filing.symbol).count()
            if filing.symbol
            else 0
        )
        bse_candidates = (
            assets.filter(symbol__iexact=filing.bse_code).count()
            if filing.bse_code
            else 0
        )
        name_candidates = (
            assets.filter(
                Q(name__iexact=filing.company_name)
                | Q(security_master__asset_name__iexact=filing.company_name)
            ).count()
            if filing.company_name
            else 0
        )

        missing = []
        if not filing.isin:
            missing.append("filing ISIN")
        if not filing.symbol:
            missing.append("filing symbol")
        if not filing.bse_code:
            missing.append("BSE code")

        if missing:
            reason = "missing " + ", ".join(missing)
        else:
            reason = "no active STOCK/ETF identifier matched"

        return {
            "exchange": filing.exchange,
            "symbol": filing.symbol,
            "company_name": filing.company_name,
            "isin": filing.isin,
            "symbol_candidates": symbol_candidates,
            "isin_candidates": isin_candidates,
            "bse_candidates": bse_candidates,
            "name_candidates": name_candidates,
            "reason": reason,
        }
