from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.sebi_mf_analytics import (
    download_amfi_navall,
    parse_amfi_navall_feed,
    persist_sebi_universe,
)


class Command(BaseCommand):
    help = (
        "Ingest the public AMFI NAVAll scheme master and persist raw category headers, "
        "plan/option identity and the current SEBI 3-level classification."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=60,
            help="AMFI request timeout in seconds.",
        )

    def handle(self, *args, **options):
        try:
            source_text = download_amfi_navall(timeout=max(5, options["timeout"]))
            records = parse_amfi_navall_feed(source_text)
            if not records:
                raise ValueError(
                    "The AMFI feed contained no valid scheme/NAV rows. "
                    "The previously persisted universe was not changed."
                )
            result = persist_sebi_universe(records)
        except Exception as exc:
            raise CommandError(f"SEBI mutual-fund universe ingestion failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("AMFI / SEBI universe ingestion completed."))
        for label, key in (
            ("Scheme rows observed", "schemes_seen"),
            ("Growth-plan schemes (Direct + Regular)", "growth_schemes"),
            ("Direct Plan - Growth schemes", "direct_growth_schemes"),
            ("Unmapped categories", "unmapped_schemes"),
            ("Discontinued Solution Oriented schemes", "discontinued_solution_oriented"),
        ):
            self.stdout.write(f"{label}: {result[key]}")
        self.stdout.write(
            "Analytics defaults to Direct Plan - Growth. Use --plans both on "
            "build_sebi_mf_analytics to compare Direct and Regular Growth separately."
        )
