from datetime import date
from django.core.management.base import BaseCommand, CommandError
from investments.services.historical_identity_discovery import HistoricalIdentityDiscoveryService


class Command(BaseCommand):
    help = "Discover security identifiers from official exchange/depository evidence."

    def add_arguments(self, parser):
        parser.add_argument("--since", type=date.fromisoformat, default=date(2000, 1, 1))
        parser.add_argument("--until", type=date.fromisoformat, default=date.today())
        parser.add_argument("--exchange", choices=["NSE", "BSE", "NSDL", "CDSL"], default="")
        parser.add_argument("--asset-id", type=int)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force-refresh", action="store_true")

    def handle(self, *args, **options):
        if options["since"] > options["until"]:
            raise CommandError("--since must be on or before --until")
        stats = HistoricalIdentityDiscoveryService.run(
            since=options["since"], until=options["until"], exchange=options["exchange"],
            asset_id=options["asset_id"], dry_run=options["dry_run"],
            force_refresh=options["force_refresh"])
        self.stdout.write(f"Security identity discovery: {stats}")
        if stats["failures"]:
            self.stderr.write(self.style.WARNING("One or more providers failed; inspect structured logs/checkpoints."))
