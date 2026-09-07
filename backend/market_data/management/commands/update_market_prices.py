from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from investments.models import Asset
from market_data.services.market_data_manager import (
    MarketDataManager,
)
from market_data.services.security_resolver import (
    SecurityResolver,
)


class Command(BaseCommand):

    help = (
        "Automatically fetch latest market prices "
        "for all STOCK and ETF assets."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--user-id",
            type=int,
            required=False,
            help=(
                "Only update STOCK and ETF assets "
                "belonging to this user."
            ),
        )

    def _refresh_security_master_if_needed(self, assets):
        """
        Auto-refresh security_master.xlsx when any STOCK/ETF asset's
        ISIN isn't in it yet.

        This is what lets newly added securities get picked up
        without anyone manually deleting/regenerating the workbook -
        the generator (NSE download + Yahoo search/validation) only
        runs when there's actually something new to resolve, instead
        of on every single command run.
        """

        known_isins = SecurityResolver.known_isins()

        missing_isins = {
            SecurityResolver.clean_isin(asset.isin)
            for asset in assets
            if asset.isin
            and SecurityResolver.clean_isin(asset.isin)
                not in known_isins
        }

        if not missing_isins:
            return

        self.stdout.write(
            self.style.NOTICE(
                f"{len(missing_isins)} asset(s) not found in "
                "security_master.xlsx - refreshing it before "
                "fetching prices..."
            )
        )

        try:

            from market_data.services.security_master_generator import (
                SecurityMasterGenerator,
            )

            result = SecurityMasterGenerator.generate()

            SecurityResolver.reload_security_master()

            self.stdout.write(
                self.style.SUCCESS(
                    "Security master refreshed: "
                    f"{result['resolved']} resolved, "
                    f"{result['unresolved']} unresolved, "
                    f"{result['non_yahoo']} non-Yahoo assets."
                )
            )

        except Exception as exc:

            self.stdout.write(
                self.style.WARNING(
                    "Security master refresh failed: "
                    f"{exc}. Continuing with existing mappings."
                )
            )

    def handle(self, *args, **options):

        user_id = options.get("user_id")

        assets = Asset.objects.filter(
            category__in=[
                "STOCK",
                "ETF",
            ]
        )

        if user_id:

            assets = assets.filter(
                owner_id=user_id
            )

        self._refresh_security_master_if_needed(assets)

        total = assets.count()

        self.stdout.write(
            self.style.NOTICE(
                f"Updating market prices for "
                f"{total} assets..."
            )
        )

        updated = 0
        skipped = 0
        failed = 0
        total_records = 0

        for asset in assets:

            self.stdout.write(
                f"Updating: {asset.name} "
                f"({asset.symbol})"
            )

            result = (
                MarketDataManager
                .fetch_and_rebuild(
                    asset=asset,
                )
            )

            if result.get("success"):

                updated += 1

                records = result.get(
                    "records",
                    0,
                )

                total_records += records

                if result.get("skipped"):

                    skipped += 1

                    self.stdout.write(
                        self.style.WARNING(
                            f"  Skipped: "
                            f"{result.get('reason')}"
                        )
                    )

                else:

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Updated "
                            f"{records} price records."
                        )
                    )

            else:

                if result.get("skipped"):

                    skipped += 1

                    self.stdout.write(
                        self.style.WARNING(
                            f"  Skipped: "
                            f"{result.get('reason')}"
                        )
                    )

                else:

                    failed += 1

                    self.stdout.write(
                        self.style.ERROR(
                            f"  Failed: "
                            f"{result.get('error')}"
                        )
                    )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "Market price update completed."
            )
        )

        self.stdout.write(
            f"Assets processed: {total}"
        )

        self.stdout.write(
            f"Assets updated: {updated}"
        )

        self.stdout.write(
            f"Assets skipped: {skipped}"
        )

        self.stdout.write(
            f"Assets failed: {failed}"
        )

        self.stdout.write(
            f"Price records saved: {total_records}"
        )