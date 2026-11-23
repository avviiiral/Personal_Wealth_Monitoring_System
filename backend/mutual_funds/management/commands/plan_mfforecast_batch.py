"""Plan a resumable, bounded MF Forecast batch across the AMFI scheme universe."""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import MFExternalPortfolioSnapshot, MFSourceRun


SOURCE = "MFFORECAST_API"
IMPORT_COMMAND = "sync_mfforecast_holdings"
MAX_BATCH_SIZE = 50


def latest_scheme_outcomes():
    """Return the latest persisted live-import outcome for each AMFI scheme code."""
    latest = {}
    runs = (
        MFSourceRun.objects.filter(source=SOURCE, command=IMPORT_COMMAND)
        .order_by("-started_at")
        .values_list("metadata", flat=True)
    )
    for metadata in runs.iterator():
        if not isinstance(metadata, dict):
            continue
        outcomes = metadata.get("scheme_outcomes")
        if not isinstance(outcomes, list):
            continue
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            scheme_code = str(outcome.get("scheme_code") or "").strip()
            if scheme_code and scheme_code not in latest:
                latest[scheme_code] = outcome
    return latest


class Command(BaseCommand):
    help = (
        "Plan a fair, resumable MF Forecast batch across active Direct-Growth "
        "schemes. Makes no API requests and writes no database records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--amc",
            action="append",
            dest="amcs",
            default=[],
            help="Optional AMC substring filter. Repeat to include multiple AMCs. Defaults to all AMCs.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum scheme requests to plan (1-50; default: 50).",
        )
        parser.add_argument(
            "--retry-not-found",
            action="store_true",
            help="Include schemes whose latest live request returned HTTP 404.",
        )
        parser.add_argument(
            "--retry-incomplete",
            action="store_true",
            help="Include schemes whose latest live response was structurally incomplete.",
        )
        parser.add_argument(
            "--retry-errors",
            action="store_true",
            help="Include schemes whose latest live request failed.",
        )
        parser.add_argument(
            "--include-previously-synced",
            action="store_true",
            help="Include schemes with prior MF Forecast snapshots or recorded outcomes.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= MAX_BATCH_SIZE:
            raise CommandError(f"--limit must be between 1 and {MAX_BATCH_SIZE}.")

        amcs = list(dict.fromkeys(
            value.strip()
            for value in options["amcs"]
            if value and value.strip()
        ))
        if options["amcs"] and not amcs:
            raise CommandError("Supply at least one non-empty --amc value.")

        candidates_qs = AMFIMasterScheme.objects.filter(
            is_active=True,
            is_direct_plan_growth=True,
        )
        if amcs:
            amc_filter = Q()
            for value in amcs:
                amc_filter |= Q(amc_name__icontains=value)
            candidates_qs = candidates_qs.filter(amc_filter)

        candidates = list(
            candidates_qs.order_by("amc_name", "scheme_code")
        )
        if not candidates:
            self.stdout.write(self.style.WARNING(
                "No active Direct-Growth schemes matched the requested universe/filter."
            ))
            return

        outcomes = latest_scheme_outcomes()
        snapshot_scheme_ids = set(
            MFExternalPortfolioSnapshot.objects.filter(
                provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
            ).values_list("scheme_id", flat=True).distinct()
        )

        excluded = {
            "imported": 0,
            "not_found": 0,
            "incomplete": 0,
            "errors": 0,
            "legacy_snapshots": 0,
        }
        eligible = []
        include_previous = options["include_previously_synced"]

        for scheme in candidates:
            if include_previous:
                eligible.append(scheme)
                continue

            outcome = outcomes.get(scheme.scheme_code)
            status = str(outcome.get("status") or "").upper() if outcome else ""

            if status == "IMPORTED":
                excluded["imported"] += 1
            elif status == "NOT_FOUND" and not options["retry_not_found"]:
                excluded["not_found"] += 1
            elif status == "INCOMPLETE" and not options["retry_incomplete"]:
                excluded["incomplete"] += 1
            elif status == "ERROR" and not options["retry_errors"]:
                excluded["errors"] += 1
            elif not outcome and scheme.id in snapshot_scheme_ids:
                # Existing snapshots created before outcome tracking are treated
                # as already processed. Explicit retry switches only apply to
                # statuses that this version can identify reliably.
                excluded["legacy_snapshots"] += 1
            else:
                eligible.append(scheme)

        # Select round-robin by AMC so early batches do not get dominated by
        # the first AMC in scheme-code ordering. Ordering is deterministic.
        by_amc = defaultdict(list)
        for scheme in eligible:
            by_amc[scheme.amc_name or "AMC not recorded"].append(scheme)
        amc_names = sorted(by_amc, key=str.casefold)
        positions = {name: 0 for name in amc_names}
        selected = []
        while len(selected) < limit:
            added = False
            for amc_name in amc_names:
                position = positions[amc_name]
                if position >= len(by_amc[amc_name]):
                    continue
                selected.append(by_amc[amc_name][position])
                positions[amc_name] += 1
                added = True
                if len(selected) >= limit:
                    break
            if not added:
                break

        self.stdout.write(
            f"Eligible universe={len(candidates)} | available after outcome filters={len(eligible)} | "
            f"selected={len(selected)} | AMCs selected={len({s.amc_name for s in selected})}"
        )
        self.stdout.write(
            "Skipped: "
            f"imported={excluded['imported']} "
            f"not_found={excluded['not_found']} "
            f"incomplete={excluded['incomplete']} "
            f"errors={excluded['errors']} "
            f"legacy_snapshots={excluded['legacy_snapshots']}"
        )

        if not selected:
            self.stdout.write(self.style.WARNING(
                "No new schemes are available in this batch. Use an explicit --retry-* "
                "switch only when you intend to recheck those provider outcomes."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Planned {len(selected)} scheme(s). No API requests were made and no database records were written."
        ))
        for scheme in selected:
            self.stdout.write(
                f"{scheme.amc_name} | {scheme.scheme_code} | "
                f"{scheme.scheme_name} | category={scheme.level_3_category or 'not classified'}"
            )

        args = " ".join(
            f"--scheme-code {scheme.scheme_code}" for scheme in selected
        )
        self.stdout.write("")
        self.stdout.write("Run the bounded API dry-run first:")
        self.stdout.write(f"python manage.py {IMPORT_COMMAND} {args} --dry-run")
        self.stdout.write("")
        self.stdout.write("After reviewing the batch, run the live import:")
        self.stdout.write(f"python manage.py {IMPORT_COMMAND} {args}")


