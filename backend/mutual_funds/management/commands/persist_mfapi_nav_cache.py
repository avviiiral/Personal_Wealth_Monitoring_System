from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.sebi_mf_analytics import (
    get_cached_nav_coverage,
    load_growth_universe,
    persist_cached_nav_histories,
)


class Command(BaseCommand):
    help = (
        "Persist locally cached MFapi historical NAV observations into "
        "AMFIMasterNAV. This command never makes network requests."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--plans",
            choices=("direct", "both"),
            default="both",
            help="Persist Direct Growth only or both Direct and Regular Growth.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of NAV rows per database upsert batch.",
        )
        parser.add_argument(
            "--max-schemes",
            type=int,
            default=None,
            help="Optional scheme limit for a controlled trial.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report local cache coverage without writing to the database.",
        )

    def handle(self, *args, **options):
        try:
            schemes = load_growth_universe(
                plans=options["plans"],
                max_schemes=options["max_schemes"],
            )
            coverage = get_cached_nav_coverage(schemes)
            self.stdout.write(
                f"Local cache coverage: {coverage['schemes_with_history']}/"
                f"{coverage['schemes_requested']} ({coverage['coverage_pct']}%)"
            )
            self.stdout.write(
                f"Schemes without valid history cache: {coverage['schemes_without_history']}"
            )

            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("Dry run: no database rows written."))
                return

            result = persist_cached_nav_histories(
                schemes,
                batch_size=options["batch_size"],
            )
        except Exception as exc:
            raise CommandError(f"MFapi NAV cache persistence failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("MFapi cache persistence completed."))
        self.stdout.write(f"Schemes requested: {result['schemes_requested']}")
        self.stdout.write(f"Schemes with valid cache: {result['schemes_with_cache']}")
        self.stdout.write(f"Schemes skipped: {result['schemes_skipped']}")
        self.stdout.write(f"NAV rows upserted: {result['nav_rows_upserted']}")
        self.stdout.write(
            "Source label: mfapi.in. Existing AMFI and AMFI_NAVALL observations are preserved."
        )
        self.stdout.write(
            "No MFapi or IndianAPI requests were made; only local JSON cache files were read."
        )
