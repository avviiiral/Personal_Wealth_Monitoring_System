"""Audit persisted MF Forecast holdings against each scheme's latest snapshot."""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum
from django.utils import timezone

from mutual_funds.master_models import MFExternalPortfolioSnapshot, MFMasterHolding
from mutual_funds.models import AMFIMasterScheme


class Command(BaseCommand):
    help = (
        "Read-only reconciliation of persisted MF Forecast snapshots and holdings "
        "for explicit schemes or one AMC."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--scheme-code",
            action="append",
            dest="scheme_codes",
            default=[],
            help="AMFI scheme code. Repeat to audit multiple schemes.",
        )
        parser.add_argument("--amc", default="", help="AMC name substring filter.")
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum schemes to audit (1-50; default: 50).",
        )

    def handle(self, *args, **options):
        scheme_codes = list(dict.fromkeys(
            str(code).strip()
            for code in options["scheme_codes"]
            if str(code).strip()
        ))
        amc = options["amc"].strip()
        limit = options["limit"]
        if not 1 <= limit <= 50:
            raise CommandError("--limit must be between 1 and 50.")
        if not scheme_codes and not amc:
            raise CommandError("Specify at least one --scheme-code or an --amc filter.")
        if len(scheme_codes) > limit:
            raise CommandError("The number of --scheme-code values cannot exceed --limit.")

        snapshots_qs = MFExternalPortfolioSnapshot.objects.filter(
            provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
            scheme__is_active=True,
        ).select_related("scheme")
        if scheme_codes:
            snapshots_qs = snapshots_qs.filter(scheme__scheme_code__in=scheme_codes)
        if amc:
            snapshots_qs = snapshots_qs.filter(scheme__amc_name__icontains=amc)

        # Pick the newest provider response for each AMFI scheme.
        latest = []
        seen_scheme_ids = set()
        for snapshot in snapshots_qs.order_by("-observed_at", "scheme_id"):
            if snapshot.scheme_id in seen_scheme_ids:
                continue
            latest.append(snapshot)
            seen_scheme_ids.add(snapshot.scheme_id)
            if len(latest) >= limit:
                break

        found_codes = {item.scheme.scheme_code for item in latest}
        missing_codes = sorted(set(scheme_codes) - found_codes)
        if not latest:
            raise CommandError("No MF Forecast snapshots matched the requested selector.")

        self.stdout.write(
            "MF Forecast persisted-holdings audit (read-only; no API requests or writes)"
        )
        failed = warned = passed = 0

        for snapshot in latest:
            portfolio_date = (
                snapshot.provider_as_of_date
                or timezone.localdate(snapshot.observed_at)
            )
            date_basis = (
                MFMasterHolding.DATE_BASIS_DISCLOSURE
                if snapshot.provider_as_of_date
                else MFMasterHolding.DATE_BASIS_FETCHED_AT
            )
            holdings = MFMasterHolding.objects.filter(
                scheme=snapshot.scheme,
                source="MFFORECAST_API",
                portfolio_date=portfolio_date,
                portfolio_date_basis=date_basis,
            )
            stored_count = holdings.count()
            stored_weight = holdings.aggregate(total=Sum("pct_of_nav"))["total"] or Decimal("0")
            expected_count = snapshot.holdings_count
            expected_weight = snapshot.total_weight_pct or Decimal("0")
            count_matches = stored_count == expected_count
            weight_matches = abs(stored_weight - expected_weight) <= Decimal("0.0001")
            quality_flags = list(snapshot.quality_flags or [])

            if not count_matches or not weight_matches:
                status = "FAIL"
                failed += 1
            elif quality_flags:
                status = "WARN"
                warned += 1
            else:
                status = "PASS"
                passed += 1

            self.stdout.write(
                f"{status} | {snapshot.scheme.scheme_code} | {snapshot.scheme.amc_name} | "
                f"{snapshot.scheme.scheme_name}"
            )
            self.stdout.write(
                f"  date={portfolio_date} basis={date_basis} "
                f"rows_snapshot={expected_count} rows_stored={stored_count} "
                f"weight_snapshot={expected_weight} weight_stored={stored_weight}"
            )
            if quality_flags:
                self.stdout.write("  quality_flags=" + ", ".join(quality_flags))

            for group in holdings.values("instrument_type").annotate(
                count=Count("id"),
                weight=Sum("pct_of_nav"),
            ).order_by("instrument_type"):
                self.stdout.write(
                    f"  {group['instrument_type']}: "
                    f"count={group['count']} weight={group['weight'] or Decimal('0')}"
                )

        if missing_codes:
            self.stdout.write(
                self.style.WARNING(
                    "No MF Forecast snapshot found for scheme code(s): "
                    + ", ".join(missing_codes)
                )
            )

        self.stdout.write(
            f"Audit totals: schemes={len(latest)} pass={passed} warn={warned} fail={failed}."
        )
