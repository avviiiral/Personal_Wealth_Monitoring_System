from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db.models import Count

from mutual_funds.models import AMFIMasterNAV
from mutual_funds.services.amfi import AMFIService
from watchlist.models import InvestmentProduct, ProductType, BenchmarkMasterPoint, WatchListEntry
from watchlist.services.benchmark import BenchmarkPerformanceService


class Command(BaseCommand):
    help = (
        "Populate shared AMFI NAV and benchmark master history required by "
        "Watch List benchmark charts. Chart API requests remain read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument("--product-id", type=int, required=False)
        parser.add_argument(
            "--period",
            choices=tuple(BenchmarkPerformanceService.PERIOD_DAYS.keys()),
            default="5Y",
        )

    def handle(self, *args, **options):
        product_id = options["product_id"]
        period = options["period"]

        product = None
        if product_id is not None:
            product = InvestmentProduct.objects.select_related(
                "mutual_fund", "pms"
            ).filter(id=product_id).first()
            if product is None:
                raise CommandError(f"InvestmentProduct {product_id} was not found.")
            BenchmarkPerformanceService._product_benchmark(product)

        today = timezone.now().date()
        days = BenchmarkPerformanceService.PERIOD_DAYS[period]
        # Prepare the shared benchmark master for the full supported chart
        # range, regardless of the currently selected product period. This
        # prevents switching from 1Y/3Y to 5Y from exposing a partial series.
        benchmark_start = today - timedelta(
            days=BenchmarkPerformanceService.PERIOD_DAYS["5Y"] + 31
        )
        start = today - timedelta(days=days + 31)

        if product is None:
            # Historical charts are displayed for the Watch List, so scope the
            # AMFI backfill to currently watchlisted Mutual Fund schemes.
            # AMFI returns the full universe for each request, but scheme_codes
            # keeps only relevant schemes in our master. One request covers all
            # selected schemes for a 90-day window, so a 5Y backfill is about
            # 21 requests rather than one request per fund.
            watchlisted_scheme_codes = set(
                InvestmentProduct.objects.filter(
                    id__in=WatchListEntry.objects.values("product_id"),
                    product_type=ProductType.MUTUAL_FUND,
                    is_active=True,
                )
                .exclude(mutual_fund__scheme_code__isnull=True)
                .exclude(mutual_fund__scheme_code="")
                .values_list("mutual_fund__scheme_code", flat=True)
            )

            if watchlisted_scheme_codes:
                self.stdout.write(
                    f"Preparing historical AMFI NAV for "
                    f"{len(watchlisted_scheme_codes)} watchlisted Mutual Fund schemes "
                    f"from {benchmark_start} to {today}..."
                )

                cursor = benchmark_start
                imported_windows = 0
                while cursor <= today:
                    window_end = min(cursor + timedelta(days=89), today)
                    existing_codes = set(
                        AMFIMasterNAV.objects.filter(
                            scheme__scheme_code__in=watchlisted_scheme_codes,
                            source="AMFI",
                            date__gte=cursor,
                            date__lte=window_end,
                        )
                        .values("scheme__scheme_code")
                        .annotate(observation_count=Count("id"))
                        .filter(observation_count__gte=2)
                        .values_list("scheme__scheme_code", flat=True)
                    )
                    missing_codes = watchlisted_scheme_codes - existing_codes

                    if missing_codes:
                        result = AMFIService.import_historical_master_navs(
                            cursor,
                            window_end,
                            scheme_codes=watchlisted_scheme_codes,
                        )
                        imported_windows += 1
                        self.stdout.write(
                            f"  AMFI {cursor} -> {window_end}: "
                            f"{result['nav_records']} watchlist NAV rows imported"
                        )
                    else:
                        self.stdout.write(
                            f"  AMFI {cursor} -> {window_end}: already populated"
                        )

                    cursor = window_end + timedelta(days=1)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"AMFI historical NAV preparation complete; "
                        f"{imported_windows} windows refreshed for "
                        f"{len(watchlisted_scheme_codes)} watchlisted schemes."
                    )
                )
            else:
                self.stdout.write(
                    "No active Mutual Fund products are currently watchlisted; "
                    "skipping AMFI historical NAV backfill."
                )

        if product is not None and product.product_type == ProductType.MUTUAL_FUND:
            inception_date = getattr(product.mutual_fund, "inception_date", None)
            if inception_date:
                start = max(start, inception_date)

            scheme_code = BenchmarkPerformanceService._fund_scheme_code(product)
            if not scheme_code:
                raise CommandError("Mutual Fund has no AMFI scheme code.")

            self.stdout.write(
                f"Preparing AMFI master history for scheme {scheme_code} "
                f"from {start} to {today}..."
            )

            cursor = start
            imported_windows = 0
            while cursor <= today:
                window_end = min(cursor + timedelta(days=89), today)
                count = AMFIMasterNAV.objects.filter(
                    scheme__scheme_code=scheme_code,
                    source="AMFI",
                    date__gte=cursor,
                    date__lte=window_end,
                ).count()
                expected = max(5, int((window_end - cursor).days * 0.5))
                if count < expected:
                    result = AMFIService.import_historical_master_navs(
                        cursor,
                        window_end,
                        scheme_codes={scheme_code},
                    )
                    imported_windows += 1
                    self.stdout.write(
                        f"  AMFI {cursor} -> {window_end}: "
                        f"{result['nav_records']} rows imported"
                    )
                cursor = window_end + timedelta(days=1)

            self.stdout.write(
                self.style.SUCCESS(
                    f"AMFI master preparation complete; "
                    f"{imported_windows} historical windows refreshed."
                )
            )

        # Benchmark master history is shared across every Mutual Fund and
        # PMS product. Always prepare BOTH supported benchmarks so opening
        # the comparison chart never depends on which product was prepared
        # first. Product-specific preparation is only needed for AMFI NAV data.
        benchmark_names = ["Nifty 50", "BSE 500"]
        for benchmark_name in benchmark_names:
            benchmark_count = BenchmarkMasterPoint.objects.filter(
                benchmark=benchmark_name,
                source="MASTER",
                date__gte=benchmark_start,
                date__lte=today,
            ).count()
            expected_benchmark = max(
                5, int((today - benchmark_start).days * 0.5)
            )

            if benchmark_count < expected_benchmark:
                self.stdout.write(
                    f"Preparing {benchmark_name} master history from "
                    f"{benchmark_start} to {today}..."
                )
                if benchmark_name == "BSE 500":
                    points = BenchmarkPerformanceService._bse_series(benchmark_start)
                else:
                    points = BenchmarkPerformanceService._nifty_tri_series(
                        benchmark_start, today
                    )
                saved = BenchmarkPerformanceService.save_benchmark_master(
                    benchmark_name,
                    points,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{benchmark_name} master preparation complete; "
                        f"{saved} rows saved."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{benchmark_name} master already contains sufficient history."
                    )
                )

        if product is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Watch List {period} chart data is ready for product {product.id}."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Shared Watch List Nifty 50 and BSE 500 benchmark data is ready "
                    "for all Mutual Fund and PMS products."
                )
            )
