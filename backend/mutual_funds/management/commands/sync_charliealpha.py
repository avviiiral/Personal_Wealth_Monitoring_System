"""Synchronize selected CharlieAlpha endpoint families into PWMS."""
import re

from django.core.management.base import BaseCommand, CommandError

from mutual_funds.models import AMFIMasterScheme, MFSchemeIdentityMap
from mutual_funds.master_models import MFSourceRun
from mutual_funds.services.charliealpha import CharlieAlphaService, SOURCE, parse_date
from mutual_funds.services.charliealpha_extended import CharlieAlphaExtendedService


class Command(BaseCommand):
    help = (
        "Ingest CharlieAlpha MF/ETF, NPS, SIF, GIFT City, instrument, corporate-action "
        "and benchmark endpoints into the configured PWMS database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            choices=("categories", "schemes", "search", "nav", "nav-detail", "nav-history", "metrics", "ter", "aum", "holdings", "holdings-periods", "holdings-history", "exposure", "benchmarks", "compare", "overlap", "nps", "sif", "gift", "instrument-search", "instrument-detail", "corporate-actions", "provider-all", "all"),
            default="all",
            help="Data domain to synchronize (default: all).",
        )
        parser.add_argument(
            "--scheme-code", action="append", dest="scheme_codes", default=[],
            help="Six-digit AMFI scheme code or 12-character ISIN; repeat to select multiple schemes.",
        )
        parser.add_argument(
            "--category", default="",
            help="CharlieAlpha category slug (for schemes/metrics) or AMFI category filter.",
        )
        parser.add_argument(
            "--compare-code", action="append", dest="compare_codes", default=[],
            help="Scheme code for --domain compare/overlap; supply 2 to 20.",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Optional upper bound for per-scheme calls; 0 means no artificial cap.",
        )
        parser.add_argument("--search-query", default="", help="Free-text search for MF, SIF, GIFT City or instrument records.")
        parser.add_argument("--identifier", default="", help="Stock/security identifier for fund exposure, or 12-character ISIN for instrument-detail.")
        parser.add_argument("--min-pct", default="", help="Optional minimum portfolio percentage for stock exposure.")
        parser.add_argument("--period-limit", type=int, default=4, help="Maximum historical holdings periods per scheme; 0 means all available.")
        parser.add_argument("--include-nav-history", action="store_true", help="Also fetch historical NAV for the selected/limited scheme universe during --domain all.")
        parser.add_argument("--horizon", choices=("1y", "3y", "5y", "10y"), default=None, help="Optional category-metrics horizon filter.")
        parser.add_argument("--benchmark-range", choices=("ALL", "1M", "6M", "1Y", "3Y", "5Y", "10Y", "15Y", "20Y", "25Y"), default="ALL", help="Relative benchmark history window.")
        parser.add_argument("--benchmark-from", default="", help="Custom benchmark start date (YYYY-MM-DD); requires --benchmark-to.")
        parser.add_argument("--benchmark-to", default="", help="Custom benchmark end date (YYYY-MM-DD); requires --benchmark-from.")
        parser.add_argument("--include-pairwise", action="store_true", help="Request optional pairwise correlation and holdings matrices.")
        parser.add_argument("--dry-run", action="store_true", help="Validate and fetch without changing scheme data.")
        parser.add_argument(
            "--skip-holdings-changes", action="store_true",
            help="Do not request the provider's previous-period holdings change summary.",
        )

        parser.add_argument("--nps-horizon", default="", help="Optional NPS metrics horizon supported by CharlieAlpha (for example 3y).")
        parser.add_argument("--instrument-type", default="", help="Optional instrument type filter for instrument search.")
        parser.add_argument("--corporate-code", default="", help="Optional AMFI code, ticker, BSE scrip code or ISIN for corporate actions.")
        parser.add_argument("--corporate-status", choices=("all", "applied", "upcoming"), default="all", help="Corporate-action status filter.")
        parser.add_argument("--action-type", choices=("ALL", "SPLIT", "BONUS"), default="ALL", help="Corporate-action type filter.")
        parser.add_argument("--corporate-from", default="", help="Corporate-action start date (YYYY-MM-DD); requires --corporate-to.")
        parser.add_argument("--corporate-to", default="", help="Corporate-action end date (YYYY-MM-DD); requires --corporate-from.")
        parser.add_argument("--gift-plan", default="", help="Optional GIFT City NAV-history plan variant.")
        parser.add_argument("--gift-from", default="", help="Optional GIFT City NAV-history start date (YYYY-MM-DD).")
        parser.add_argument("--gift-to", default="", help="Optional GIFT City NAV-history end date (YYYY-MM-DD).")

    def handle(self, *args, **options):
        domain = options["domain"]
        codes = list(dict.fromkeys(str(v).strip() for v in options["scheme_codes"] if str(v).strip()))
        category = options["category"].strip()
        limit = options["limit"]
        if limit < 0:
            raise CommandError("--limit cannot be negative.")
        if options["period_limit"] < 0:
            raise CommandError("--period-limit cannot be negative.")
        if bool(options["benchmark_from"]) != bool(options["benchmark_to"]):
            raise CommandError("--benchmark-from and --benchmark-to must be supplied together.")
        if bool(options["corporate_from"]) != bool(options["corporate_to"]):
            raise CommandError("--corporate-from and --corporate-to must be supplied together.")
        for option_name in ("gift_from", "gift_to", "corporate_from", "corporate_to"):
            value = options[option_name]
            if value and (
                not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
                or parse_date(value) is None
            ):
                raise CommandError("--" + option_name.replace("_", "-") + " must use YYYY-MM-DD.")
        if domain == "instrument-search" and limit > 100:
            raise CommandError("--limit for instrument-search cannot exceed 100.")
        if domain == "corporate-actions" and limit > 200:
            raise CommandError("--limit for corporate-actions cannot exceed 200.")
        if codes and limit and len(codes) > limit:
            codes = codes[:limit]

        service = CharlieAlphaService(dry_run=options["dry_run"])
        result = {}
        extended = CharlieAlphaExtendedService(dry_run=options["dry_run"])
        directory_codes: list[str] = []

        if domain == "categories":
            result["categories"] = service.sync_categories()

        if domain in {"schemes", "all"}:
            result["schemes"] = service.sync_schemes(category=category)
            directory_codes = result["schemes"].get("linked_scheme_codes", [])

        if domain == "search":
            if not options["search_query"].strip():
                raise CommandError("--domain search requires --search-query.")
            result["search"] = service.search_schemes(options["search_query"])

        if domain in {"nav", "all"}:
            result["nav"] = service.sync_navall()

        if domain == "nav-detail" or (domain == "all" and codes):
            detail_codes = codes or directory_codes or self._linked_scheme_codes(category)
            if limit:
                detail_codes = detail_codes[:limit]
            result["nav_detail"] = service.sync_latest_nav_details(detail_codes)

        if domain == "nav-history" or (domain == "all" and options["include_nav_history"]):
            history_codes = codes or directory_codes or self._linked_scheme_codes(category)
            if limit:
                history_codes = history_codes[:limit]
            if not history_codes:
                raise CommandError("No CharlieAlpha-linked schemes available for NAV history. Run --domain schemes first.")
            result["nav_history"] = service.sync_nav_history(history_codes)

        if domain == "metrics":
            result["metrics"] = service.sync_metrics(
                scheme_codes=codes or None,
                category_slugs=[category] if category else None,
                horizon=options["horizon"],
            )

        if domain == "all":
            result["metrics"] = service.sync_metrics(
                category_slugs=[category] if category else None,
                horizon=options["horizon"],
            )

        if domain in {"ter", "aum", "holdings", "all"}:
            selected_codes = codes or directory_codes or self._linked_scheme_codes(category)
            if limit:
                selected_codes = selected_codes[:limit]
            if not selected_codes:
                raise CommandError(
                    "No CharlieAlpha-linked AMFI schemes are available. Run --domain schemes first "
                    "or specify --scheme-code explicitly."
                )
            if domain in {"ter", "all"}:
                result["ter"] = service.sync_ter(selected_codes, limit)
            if domain in {"aum", "all"}:
                result["aum"] = service.sync_aum(selected_codes, limit)
            if domain in {"holdings", "all"}:
                result["holdings"] = service.sync_holdings(
                    selected_codes,
                    include_changes=not options["skip_holdings_changes"],
                    limit=limit or None,
                )

        if domain in {"holdings-periods", "all"}:
            result["holdings_periods"] = service.sync_holdings_periods(codes or None)

        if domain == "holdings-history":
            history_codes = codes or self._linked_scheme_codes(category)
            if limit:
                history_codes = history_codes[:limit]
            if not history_codes:
                raise CommandError("No CharlieAlpha-linked schemes available for holdings history. Run --domain schemes first.")
            result["holdings_history"] = service.sync_holdings_history(
                history_codes, period_limit=options["period_limit"],
            )

        if domain == "exposure":
            if not options["identifier"].strip():
                raise CommandError("--domain exposure requires --identifier.")
            result["exposure"] = service.sync_fund_exposure(
                options["identifier"], options["min_pct"] or None,
            )

        if domain in {"benchmarks", "all"}:
            result["benchmarks"] = service.sync_benchmarks(
                limit=limit or None,
                range_name=options["benchmark_range"],
                from_date=options["benchmark_from"] or None,
                to_date=options["benchmark_to"] or None,
            )


        if domain == "nps":
            result["nps"] = extended.sync_nps(limit=limit, horizon=options["nps_horizon"])

        if domain == "sif":
            result["sif"] = extended.sync_sif(limit=limit, search_query=options["search_query"])

        if domain == "gift":
            result["gift"] = extended.sync_gift(
                limit=limit,
                search_query=options["search_query"],
                plan=options["gift_plan"],
                from_date=options["gift_from"],
                to_date=options["gift_to"],
            )

        if domain == "instrument-search":
            if not options["search_query"].strip():
                raise CommandError("--domain instrument-search requires --search-query.")
            result["instrument_search"] = extended.search_instruments(
                options["search_query"],
                instrument_type=options["instrument_type"],
                limit=limit or 100,
            )

        if domain == "instrument-detail":
            isin = options["identifier"].strip().upper()
            if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
                raise CommandError("--domain instrument-detail requires a valid 12-character ISIN.")
            result["instrument_detail"] = extended.instrument_details(isin)

        if domain == "corporate-actions":
            result["corporate_actions"] = extended.corporate_actions(
                code=options["corporate_code"],
                status_filter=options["corporate_status"],
                action_type=options["action_type"],
                from_date=options["corporate_from"],
                to_date=options["corporate_to"],
                limit=limit or 50,
            )

        if domain == "provider-all":
            result["nps"] = extended.sync_nps(limit=limit, horizon=options["nps_horizon"])
            result["sif"] = extended.sync_sif(limit=limit, search_query=options["search_query"])
            result["gift"] = extended.sync_gift(
                limit=limit,
                search_query=options["search_query"],
                plan=options["gift_plan"],
                from_date=options["gift_from"],
                to_date=options["gift_to"],
            )
            if options["search_query"].strip():
                result["instrument_search"] = extended.search_instruments(
                    options["search_query"],
                    instrument_type=options["instrument_type"],
                    limit=limit or 100,
                )
            result["corporate_actions"] = extended.corporate_actions(
                code=options["corporate_code"],
                status_filter=options["corporate_status"],
                action_type=options["action_type"],
                from_date=options["corporate_from"],
                to_date=options["corporate_to"],
                limit=min(limit or 50, 200),
            )

        if domain in {"compare", "overlap"}:
            compare_codes = list(dict.fromkeys(
                str(v).strip() for v in (options["compare_codes"] or codes) if str(v).strip()
            ))
            if not 2 <= len(compare_codes) <= 20:
                raise CommandError(
                    "--domain compare/overlap requires 2 to 20 codes via --compare-code "
                    "(or repeat --scheme-code)."
                )
            result[domain] = service.compare(
                compare_codes,
                overlap_only=(domain == "overlap"),
                include_pairwise=True if options["include_pairwise"] else None,
            )

        self.stdout.write(self.style.SUCCESS("CharlieAlpha sync finished."))
        self.stdout.write("Domain: " + domain)
        for name, values in result.items():
            self.stdout.write(name + ": " + str(values))
        self.stdout.write(
            "Totals: fetched={fetched} inserted={inserted} updated={updated} skipped={skipped} "
            "failed={failed} unmatched={unmatched}".format(**service.stats)
        )
        if extended.stats["requests"]:
            self.stdout.write(
                "Extended provider totals: requests={requests} snapshots_saved={snapshots_saved} "
                "records_normalized={records_normalized} failed={failed}".format(**extended.stats)
            )
        if service.errors:
            self.stdout.write(self.style.WARNING("Reported issues (first 100):"))
            for error in service.errors[:100]:
                self.stdout.write(str(error))

    @staticmethod
    def _linked_scheme_codes(category: str = "") -> list[str]:
        if category:
            runs = MFSourceRun.objects.filter(
                source=SOURCE,
                command="schemes",
                status__in=(MFSourceRun.Status.SUCCESS, MFSourceRun.Status.PARTIAL),
            ).order_by("-started_at")[:100]
            for run in runs:
                metadata = run.metadata or {}
                if metadata.get("category_slug") == category:
                    codes = metadata.get("linked_scheme_codes", [])
                    return list(
                        AMFIMasterScheme.objects.filter(
                            is_active=True,
                            scheme_code__in=codes,
                        ).order_by("scheme_code").values_list("scheme_code", flat=True)
                    )
            return []

        queryset = AMFIMasterScheme.objects.filter(
            is_active=True,
            identity_maps__source="CHARLIEALPHA",
            identity_maps__status=MFSchemeIdentityMap.STATUS_ACTIVE,
        )
        return list(queryset.order_by("scheme_code").values_list("scheme_code", flat=True).distinct())
