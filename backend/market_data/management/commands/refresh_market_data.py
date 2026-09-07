from django.core.management.base import BaseCommand

from investments.models import Asset
from market_data.services.market_data_manager import MarketDataManager


class Command(BaseCommand):
    help = (
        "Refresh market data and holdings for all active "
        "Stock and ETF assets."
    )

    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("PWMS MARKET DATA REFRESH")
        self.stdout.write("=" * 60)
        self.stdout.write("")

        assets = Asset.objects.filter(
            is_active=True,
            category__in=["STOCK", "ETF"],
        ).order_by("name")

        total = assets.count()

        self.stdout.write(
            f"Found {total} active Stock/ETF assets."
        )
        self.stdout.write("")

        successful = 0
        failed = 0
        skipped = 0

        for index, asset in enumerate(assets, start=1):
            self.stdout.write(
                f"[{index}/{total}] {asset.name}"
            )

            self.stdout.write(
                f"      Category: {asset.category}"
            )

            self.stdout.write(
                f"      Input Symbol: {asset.symbol or 'N/A'}"
            )

            try:
                result = MarketDataManager.fetch_and_rebuild(
                    asset,
                    period="1y",
                )

                if result.get("skipped"):
                    skipped += 1

                    self.stdout.write(
                        self.style.WARNING(
                            "      Status: SKIPPED"
                        )
                    )

                    self.stdout.write(
                        f"      Reason: "
                        f"{result.get('reason', 'Unknown')}"
                    )

                elif result.get("success"):
                    successful += 1

                    self.stdout.write(
                        self.style.SUCCESS(
                            "      Status: SUCCESS"
                        )
                    )

                    self.stdout.write(
                        f"      Yahoo Symbol: "
                        f"{result.get('symbol')}"
                    )

                    self.stdout.write(
                        f"      Records: "
                        f"{result.get('records', 0)}"
                    )

                    self.stdout.write(
                        f"      Current Price: "
                        f"{result.get('current_price')}"
                    )

                    self.stdout.write(
                        f"      Current Value: "
                        f"{result.get('current_value')}"
                    )

                else:
                    failed += 1

                    self.stdout.write(
                        self.style.ERROR(
                            "      Status: FAILED"
                        )
                    )

                    self.stdout.write(
                        f"      Error: "
                        f"{result.get('error', 'Unknown error')}"
                    )

            except Exception as exc:
                failed += 1

                self.stdout.write(
                    self.style.ERROR(
                        "      Status: FAILED"
                    )
                )

                self.stdout.write(
                    f"      Error: {exc}"
                )

            self.stdout.write("")

        self.stdout.write("=" * 60)
        self.stdout.write("REFRESH COMPLETED")
        self.stdout.write("=" * 60)

        self.stdout.write(
            f"Successful: {successful}"
        )

        self.stdout.write(
            f"Failed:     {failed}"
        )

        self.stdout.write(
            f"Skipped:    {skipped}"
        )

        self.stdout.write(
            f"Total:      {total}"
        )

        self.stdout.write("=" * 60)
        self.stdout.write("")