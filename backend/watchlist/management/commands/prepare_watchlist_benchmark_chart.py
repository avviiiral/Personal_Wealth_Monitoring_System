from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mutual_funds.models import AMFIMasterNAV
from mutual_funds.services.amfi import AMFIService
from watchlist.models import InvestmentProduct, ProductType, BenchmarkMasterPoint
from watchlist.services.benchmark import BenchmarkPerformanceService


class Command(BaseCommand):
    help = (
        "Populate shared AMFI NAV and benchmark master history required by "
        "Watch List benchmark charts. Chart API requests remain read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument("--product-id", type=int, required=True)
        parser.add_argument(
            "--period",
            choices=tuple(BenchmarkPerformanceService.PERIOD_DAYS.keys()),
            default="3Y",
        )

    def handle(self, *args, **options):
        product_id = options["product_id"]
        period = options["period"]

        product = InvestmentProduct.objects.select_related(
            "mutual_fund", "pms"
        ).filter(id=product_id).first()
        if product is None:
            raise CommandError(f"InvestmentProduct {product_id} was not found.")

        benchmark = BenchmarkPerformanceService._product_benchmark(product)
        if not benchmark:
            raise CommandError("Product has no configured benchmark.")

        today = timezone.now().date()
        days = BenchmarkPerformanceService.PERIOD_DAYS[period]
        start = today - timedelta(days=days + 31)

        if product.product_type == ProductType.MUTUAL_FUND:
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

        benchmark_count = BenchmarkMasterPoint.objects.filter(
            benchmark=benchmark,
            source="MASTER",
            date__gte=start,
            date__lte=today,
        ).count()
        expected_benchmark = max(5, int((today - start).days * 0.5))

        if benchmark_count < expected_benchmark:
            self.stdout.write(
                f"Preparing {benchmark} master history from {start} to {today}..."
            )
            if benchmark == "BSE 500":
                points = BenchmarkPerformanceService._bse_series(start)
            elif benchmark == "Nifty 50":
                points = BenchmarkPerformanceService._nifty_tri_series(start, today)
            else:
                points = []
            saved = BenchmarkPerformanceService.save_benchmark_master(
                benchmark,
                points,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{benchmark} master preparation complete; {saved} rows saved."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{benchmark} master already contains sufficient history."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Watch List {period} chart data is ready for product {product.id}."
            )
        )
