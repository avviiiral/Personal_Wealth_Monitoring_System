"""Build consolidated, source-attributed mutual-fund masterdata snapshots."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from mutual_funds.models import AMFIMasterScheme, AMFISchemeAAUM, MFSourceRun
from mutual_funds.master_models import MFMasterData
from mutual_funds.services.masterdata import finish_source_run, start_source_run
from mutual_funds.services.masterdata_analytics import calculate_scheme_masterdata_metrics


IDENTITY_FIELDS = {
    "scheme_code", "isin_growth", "isin_idcw", "amc", "scheme_name", "plan",
    "option", "sebi_category_level_1", "sebi_category_level_2", "sebi_category_level_3",
}
AUM_FIELDS = {"aum_cr", "aum_type", "aum_as_of", "aum_source"}
SYSTEM_FIELDS = {"id", "scheme", "as_of_date", "updated_at", "fetched_at"}
NAV_FIELDS = {
    "return_1m_pct", "return_3m_pct", "return_6m_pct", "return_1y_pct",
    "return_3y_pct", "return_5y_pct", "return_10y_pct",
    "since_inception_cagr_pct", "std_dev_pct", "sharpe", "sortino",
    "max_drawdown_pct", "risk_free_rate_used_pct",
}
BENCHMARK_FIELDS = {
    "beta", "alpha_pct", "treynor", "r_squared", "tracking_error_pct",
    "information_ratio", "upside_capture_pct", "downside_capture_pct",
    "ratio_window_years",
}
HOLDINGS_FIELDS = {
    "number_of_holdings", "top10_concentration_pct", "large_cap_pct",
    "mid_cap_pct", "small_cap_pct", "other_cap_pct", "equity_pct",
    "debt_pct", "cash_pct", "others_pct", "credit_rating_split",
    "sector_allocation", "ytm_pct",
}
DERIVED_FIELDS = NAV_FIELDS | BENCHMARK_FIELDS | HOLDINGS_FIELDS
DECIMAL_FIELDS = {
    field.name for field in MFMasterData._meta.fields
    if field.get_internal_type() == "DecimalField"
}


def _decimal(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            return None
        return result.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _empty(value):
    return value is None or value == "" or value == {} or value == []


def _quarter_end(quarter_start):
    month = ((quarter_start.month - 1) // 3 + 1) * 3
    return date(quarter_start.year, month, monthrange(quarter_start.year, month)[1])


def _quarter_url(quarter_start, quarter_label):
    return "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?" + urlencode({
        "dtAUM": quarter_start.strftime("%d-%b-%Y"),
        "mf": "0",
        "qt": quarter_label,
        "rpt": "scwise",
    })


def _identity(scheme):
    return {
        "scheme_code": scheme.scheme_code,
        "isin_growth": scheme.isin_growth or "",
        "isin_idcw": scheme.isin_dividend or "",
        "amc": scheme.amc_name or "",
        "scheme_name": scheme.scheme_name,
        "plan": scheme.plan or "",
        "option": scheme.option or "",
        "sebi_category_level_1": scheme.level_1_branch or "",
        "sebi_category_level_2": scheme.level_2_group or "",
        "sebi_category_level_3": scheme.level_3_category or "",
    }


def _latest_aum(scheme, as_of_date):
    """Select the latest dated AUM observation; keep quarterly and month-end distinct."""
    quarterly = None
    for row in AMFISchemeAAUM.objects.filter(
        scheme=scheme, quarter_start__lte=as_of_date
    ).order_by("-quarter_start"):
        observed_on = _quarter_end(row.quarter_start)
        if observed_on <= as_of_date:
            quarterly = {
                "aum_cr": row.average_aum_cr,
                "aum_type": "QUARTERLY_AVG",
                "aum_as_of": observed_on,
                "aum_source": row.source or "AMFI_SCHEME_AAUM",
                "source_url": _quarter_url(row.quarter_start, row.quarter_label),
                "observation_date": observed_on,
            }
            break

    month_end = (
        MFMasterData.objects.filter(
            scheme=scheme, as_of_date__lte=as_of_date,
            aum_type="MONTH_END", aum_as_of__lte=as_of_date,
        ).exclude(aum_cr__isnull=True)
        .order_by("-aum_as_of", "-as_of_date").first()
    )
    if month_end and (quarterly is None or month_end.aum_as_of >= quarterly["aum_as_of"]):
        source_meta = (month_end.field_sources or {}).get("aum_cr", {})
        return {
            "aum_cr": month_end.aum_cr,
            "aum_type": "MONTH_END",
            "aum_as_of": month_end.aum_as_of,
            "aum_source": month_end.aum_source or source_meta.get("source", ""),
            "source_url": source_meta.get("source_url", ""),
            "observation_date": month_end.aum_as_of,
        }
    return quarterly


def _carry_disclosures(snapshot, scheme, as_of_date):
    previous = (
        MFMasterData.objects.filter(scheme=scheme, as_of_date__lt=as_of_date)
        .order_by("-as_of_date").first()
    )
    if not previous:
        return

    for field in MFMasterData._meta.fields:
        name = field.name
        if name in SYSTEM_FIELDS | IDENTITY_FIELDS | AUM_FIELDS | DERIVED_FIELDS | {
            "field_sources", "data_quality_flags",
        }:
            continue
        old_value = getattr(previous, name)
        if _empty(getattr(snapshot, name)) and not _empty(old_value):
            setattr(snapshot, name, old_value)

    previous_lineage = {
        key: value for key, value in (previous.field_sources or {}).items()
        if key not in IDENTITY_FIELDS | AUM_FIELDS | DERIVED_FIELDS
    }
    previous_lineage.update(snapshot.field_sources or {})
    snapshot.field_sources = previous_lineage


def _apply_fields(snapshot, values, *, fields, source, source_url, as_of_date, fetched_at):
    lineage = dict(snapshot.field_sources or {})
    for field in fields:
        value = values.get(field)
        if field in DECIMAL_FIELDS:
            value = _decimal(value)
        setattr(snapshot, field, value)
        lineage[field] = {
            "source": source,
            "source_url": source_url or "",
            "as_of_date": as_of_date.isoformat(),
            "fetched_at": fetched_at.isoformat(),
        }
    snapshot.field_sources = lineage


class Command(BaseCommand):
    help = "Build consolidated global mutual-fund masterdata snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", required=True, help="Snapshot date YYYY-MM-DD.")
        parser.add_argument("--amc", default="", help="Optional AMC name filter.")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--risk-free-rate", type=float, default=6.5)
        parser.add_argument("--ratio-window-years", type=int, default=3)

    def handle(self, *args, **options):
        try:
            as_of_date = date.fromisoformat(options["as_of"])
        except ValueError as exc:
            raise CommandError("--as-of must use YYYY-MM-DD.") from exc
        if not 0 <= options["risk_free_rate"] <= 100:
            raise CommandError("--risk-free-rate must be between 0 and 100.")
        if options["ratio_window_years"] <= 0:
            raise CommandError("--ratio-window-years must be a positive integer.")
        if options["limit"] < 0:
            raise CommandError("--limit cannot be negative.")

        schemes = AMFIMasterScheme.objects.filter(is_active=True).order_by("scheme_name")
        if options["amc"]:
            schemes = schemes.filter(amc_name__icontains=options["amc"])
        if options["limit"]:
            schemes = schemes[:options["limit"]]

        dry_run = options["dry_run"]
        run = None
        if not dry_run:
            run = start_source_run(
                source="PWMS_MASTERDATA_BUILD",
                command="build_mf_masterdata",
                as_of_date=as_of_date,
            )

        totals = {
            "schemes": 0, "created": 0, "updated": 0, "errors": 0,
            "with_aum": 0, "with_ter": 0, "with_portfolio": 0,
            "with_benchmark_ratios": 0,
        }
        coverage = {}
        errors = []
        risk_free_rate = options["risk_free_rate"] / 100.0

        for scheme in schemes.iterator(chunk_size=500):
            totals["schemes"] += 1
            try:
                if dry_run:
                    latest = (
                        MFMasterData.objects.filter(
                            scheme=scheme, as_of_date__lte=as_of_date
                        ).order_by("-as_of_date").first()
                    )
                    aum = _latest_aum(scheme, as_of_date)
                    metrics = calculate_scheme_masterdata_metrics(
                        scheme, as_of_date,
                        risk_free_rate=risk_free_rate,
                        ratio_window_years=options["ratio_window_years"],
                    )
                    totals["with_aum"] += int(aum is not None)
                    totals["with_ter"] += int(
                        latest is not None
                        and (latest.ter_direct_pct is not None or latest.ter_regular_pct is not None)
                    )
                    totals["with_portfolio"] += int(
                        metrics["values"].get("number_of_holdings") is not None
                    )
                    totals["with_benchmark_ratios"] += int(
                        metrics["values"].get("beta") is not None
                    )
                    available = set(_identity(scheme))
                    if latest:
                        for field in MFMasterData._meta.fields:
                            if field.name not in SYSTEM_FIELDS and not _empty(getattr(latest, field.name)):
                                available.add(field.name)
                    if aum:
                        available.update(AUM_FIELDS)
                    available.update(
                        field for field, value in metrics["values"].items()
                        if value is not None
                    )
                    for field in available:
                        coverage[field] = coverage.get(field, 0) + 1
                    continue

                with transaction.atomic():
                    identity = _identity(scheme)
                    # Select AUM before clearing this date's previously built snapshot.
                    aum = _latest_aum(scheme, as_of_date)
                    snapshot, created = MFMasterData.objects.get_or_create(
                        scheme=scheme, as_of_date=as_of_date, defaults=identity,
                    )
                    if created:
                        totals["created"] += 1
                    else:
                        totals["updated"] += 1
                        for field, value in identity.items():
                            setattr(snapshot, field, value)

                    _carry_disclosures(snapshot, scheme, as_of_date)
                    now = timezone.now()
                    snapshot.fetched_at = now
                    lineage = dict(snapshot.field_sources or {})
                    for field in identity:
                        source = "AMFI_NAVALL"
                        source_url = "https://portal.amfiindia.com/spages/NAVAll.txt"
                        if field.startswith("sebi_category_"):
                            source = "AMFI_NAVALL_SEBI_TAXONOMY"
                            source_url += "; https://www.sebi.gov.in/sebi_data/attachdocs/mar-2026/1774024028162.pdf"
                        lineage[field] = {
                            "source": source,
                            "source_url": source_url,
                            "as_of_date": (scheme.amfi_source_date or as_of_date).isoformat(),
                            "fetched_at": now.isoformat(),
                        }
                    snapshot.field_sources = lineage

                    totals["with_ter"] += int(
                        snapshot.ter_direct_pct is not None or snapshot.ter_regular_pct is not None
                    )

                    # Missing observations must clear stale derived values/lineage.
                    for field in DERIVED_FIELDS | AUM_FIELDS:
                        current = getattr(snapshot, field)
                        setattr(
                            snapshot, field,
                            {} if isinstance(current, dict)
                            else "" if field in {"aum_type", "aum_source"}
                            else None,
                        )
                    lineage = dict(snapshot.field_sources or {})
                    for field in DERIVED_FIELDS | AUM_FIELDS:
                        lineage.pop(field, None)
                    snapshot.field_sources = lineage
                    snapshot.data_quality_flags = []
                    snapshot.save()

                    if aum:
                        _apply_fields(
                            snapshot, aum, fields=AUM_FIELDS,
                            source=aum["aum_source"] or "AMFI_SCHEME_AAUM",
                            source_url=aum["source_url"],
                            as_of_date=aum["observation_date"],
                            fetched_at=now,
                        )
                        totals["with_aum"] += 1

                    metrics = calculate_scheme_masterdata_metrics(
                        scheme, as_of_date,
                        risk_free_rate=risk_free_rate,
                        ratio_window_years=options["ratio_window_years"],
                    )
                    values = metrics["values"]
                    _apply_fields(
                        snapshot, values, fields=NAV_FIELDS,
                        source="PWMS_CALCULATED_NAV",
                        source_url=metrics["source_urls"].get("nav", ""),
                        as_of_date=as_of_date, fetched_at=now,
                    )
                    _apply_fields(
                        snapshot, values, fields=BENCHMARK_FIELDS,
                        source="PWMS_CALCULATED_BENCHMARK",
                        source_url=metrics["source_urls"].get("benchmark", ""),
                        as_of_date=as_of_date, fetched_at=now,
                    )
                    _apply_fields(
                        snapshot, values, fields=HOLDINGS_FIELDS,
                        source="PWMS_CALCULATED_HOLDINGS",
                        source_url=metrics["source_urls"].get("portfolio", ""),
                        as_of_date=as_of_date, fetched_at=now,
                    )
                    totals["with_portfolio"] += int(values.get("number_of_holdings") is not None)
                    totals["with_benchmark_ratios"] += int(values.get("beta") is not None)
                    snapshot.data_quality_flags = sorted(set(metrics["data_quality_flags"]))
                    snapshot.save()

                    for field in MFMasterData._meta.fields:
                        if field.name not in SYSTEM_FIELDS and not _empty(getattr(snapshot, field.name)):
                            coverage[field.name] = coverage.get(field.name, 0) + 1
            except Exception as exc:
                totals["errors"] += 1
                if len(errors) < 100:
                    errors.append({
                        "scheme_code": scheme.scheme_code,
                        "scheme_name": scheme.scheme_name,
                        "error": str(exc)[:1000],
                    })

        summary = (
            f"schemes={totals['schemes']} created={totals['created']} "
            f"updated={totals['updated']} errors={totals['errors']} "
            f"AUM={totals['with_aum']} TER={totals['with_ter']} "
            f"portfolio={totals['with_portfolio']} benchmark_ratios={totals['with_benchmark_ratios']}"
        )
        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"DRY RUN: {summary}. No masterdata or source-run records were written."
            ))
        else:
            finish_source_run(
                run,
                status=MFSourceRun.Status.PARTIAL if totals["errors"] else MFSourceRun.Status.SUCCESS,
                parsed=totals["schemes"],
                inserted=totals["created"],
                updated=totals["updated"],
                unmatched=totals["errors"],
                errors=errors,
                metadata=totals,
            )
            self.stdout.write(self.style.SUCCESS(f"Masterdata build completed: {summary}."))

        self.stdout.write("Field coverage (populated / schemes processed):")
        for field in sorted(coverage):
            self.stdout.write(f"  {field}: {coverage[field]} / {totals['schemes']}")
