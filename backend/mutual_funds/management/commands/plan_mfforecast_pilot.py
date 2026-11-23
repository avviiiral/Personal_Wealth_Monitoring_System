"""Plan a small cross-AMC MF Forecast holdings validation pilot.

This command reads the local AMFI master table only. It never calls MF Forecast
and never writes database records.
"""
from django.core.management.base import BaseCommand, CommandError

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import MFExternalPortfolioSnapshot


DEFAULT_AMC_FILTERS = [
    "HDFC",
    "SBI",
    "ICICI Prudential",
    "Axis",
    "Nippon India",
]


class Command(BaseCommand):
    help = (
        "Select one not-yet-synced Direct-Growth scheme per AMC and print "
        "a bounded MF Forecast dry-run command. Makes no API requests."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--amc",
            action="append",
            dest="amcs",
            default=[],
            help=(
                "AMC substring to include. Repeat for multiple AMCs. "
                "Defaults to HDFC, SBI, ICICI Prudential, Axis and Nippon India."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="Maximum AMCs to select (1-10; default: 5).",
        )
        parser.add_argument(
            "--include-previously-synced",
            action="store_true",
            help="Allow schemes with an existing MF Forecast snapshot.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 10:
            raise CommandError("--limit must be between 1 and 10.")

        amcs = list(dict.fromkeys(
            value.strip()
            for value in (options["amcs"] or DEFAULT_AMC_FILTERS)
            if value and value.strip()
        ))[:limit]
        if not amcs:
            raise CommandError("Supply at least one non-empty --amc value.")

        schemes_with_snapshots = set()
        if not options["include_previously_synced"]:
            schemes_with_snapshots = set(
                MFExternalPortfolioSnapshot.objects.filter(
                    provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
                ).values_list("scheme_id", flat=True).distinct()
            )

        selected = []
        used_categories = set()
        missing_amcs = []

        for amc_filter in amcs:
            candidates_qs = AMFIMasterScheme.objects.filter(
                is_active=True,
                is_direct_plan_growth=True,
                sebi_universe_active=True,
                amc_name__icontains=amc_filter,
            )
            if schemes_with_snapshots:
                candidates_qs = candidates_qs.exclude(id__in=schemes_with_snapshots)

            candidates = list(
                candidates_qs.order_by("level_3_category", "scheme_name")[:500]
            )

            # If the SEBI active-universe flag is not populated for this AMC,
            # fall back to the explicit AMFI Direct-Growth flag only.
            if not candidates:
                fallback_qs = AMFIMasterScheme.objects.filter(
                    is_active=True,
                    is_direct_plan_growth=True,
                    amc_name__icontains=amc_filter,
                )
                if schemes_with_snapshots:
                    fallback_qs = fallback_qs.exclude(id__in=schemes_with_snapshots)
                candidates = list(
                    fallback_qs.order_by("level_3_category", "scheme_name")[:500]
                )

            if not candidates:
                missing_amcs.append(amc_filter)
                continue

            # Prefer a category not already represented in the pilot. This
            # creates a more useful cross-AMC and cross-category smoke test.
            chosen = next(
                (
                    row for row in candidates
                    if row.level_3_category
                    and row.level_3_category.casefold() not in used_categories
                ),
                candidates[0],
            )
            if chosen.level_3_category:
                used_categories.add(chosen.level_3_category.casefold())
            selected.append(chosen)

        if not selected:
            raise CommandError(
                "No eligible, unsynced Direct-Growth schemes were found for the requested AMCs. "
                "Check AMFI masterdata flags or rerun with --include-previously-synced."
            )

        self.stdout.write(self.style.SUCCESS(
            f"Selected {len(selected)} scheme(s) across {len(selected)} AMC(s). "
            "No API requests were made and no database records were written."
        ))
        for scheme in selected:
            self.stdout.write(
                f"{scheme.amc_name} | {scheme.scheme_code} | "
                f"{scheme.scheme_name} | category={scheme.level_3_category or 'not classified'}"
            )

        if missing_amcs:
            self.stdout.write(self.style.WARNING(
                "No eligible Direct-Growth scheme found for: " + ", ".join(missing_amcs)
            ))

        args = " ".join(
            f"--scheme-code {scheme.scheme_code}" for scheme in selected
        )
        self.stdout.write("")
        self.stdout.write("Run this first (API dry-run, no database writes):")
        self.stdout.write(f"python manage.py sync_mfforecast_holdings {args} --dry-run")
        self.stdout.write("")
        self.stdout.write("After reviewing every scheme's output, run the same command without --dry-run.")
