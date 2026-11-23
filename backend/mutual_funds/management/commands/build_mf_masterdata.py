"""Build a consolidated, source-attributed mutual-fund masterdata snapshot."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.core.management.base import BaseCommand, CommandError

from mutual_funds.models import AMFIMasterScheme, AMFISchemeAAUM, MFSourceRun
from mutual_funds.master_models import MFMasterData
from mutual_funds.services.masterdata import (
    finish_source_run, start_source_run, upsert_masterdata_snapshot,
)
from mutual_funds.services.masterdata_analytics import calculate_scheme_masterdata_metrics


IDENTITY_FIELDS = {
    "scheme_code", "isin_growth", "isin_idcw", "amc", "scheme_name", "plan",
    "option", "sebi_category_level_1", "sebi_category_level_2", "sebi_category_level_3",
}
AUM_FIELDS = {"aum_cr", "aum_type", "aum_as_of", "aum_source"}
SYSTEM_FIELDS = {"id", "scheme", "as_of_date", "updated_at", "fetched_at"}
DERIVED_FIELDS = {
    "return_1m_pct", "return_3m_pct", "return_6m_pct", "return_1y_pct",
    "return_3y_pct", "return_5y_pct", "return_10y_pct", "since_inception_cagr_pct",
    "std_dev_pct", "sharpe", "sortino", "beta", "alpha_pct", "treynor",
    "r_squared", "tracking_error_pct", "information_ratio", "max_drawdown_pct",
    "upside_capture_pct", "downside_capture_pct", "risk_free_rate_used_pct",
    "ratio_window_years", "large_cap_pct", "mid_cap_pct", "small_cap_pct",
    "other_cap_pct", "number_of_holdings", "top10_concentration_pct",
    "ytm_pct", "credit_rating_split", "sector_allocation", "equity_pct",
    "debt_pct", "cash_pct", "others_pct",
}


def _decimal(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            return None
        return result.quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _quarter_end(quarter_start):
    quarter_month = ((quarter_start.month - 1) // 3 + 1) * 3
    return date(
        quarter_start.year,
        quarter_month,
        monthrange(quarter_start.year, quarter_month)[1],
    )


def _quarter_report_url(quarter_start, label):
    return "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?" + urlencode({
        "dtAUM": quarter_start.strftime("%d-%b-%Y"),
        "mf": "0",
        "qt": label,
        "rpt": "scwise",
    })


def _is_empty(value):
    return value is None or value == "" or value == {} or value == []


def _inherit_latest_disclosures(snapshot, scheme, as_of_date):
    """Carry forward disclosed facts and their original field-level lineage.

    Calculated fields and AUM are deliberately excluded: they are rebuilt from
    dated source observations for each snapshot date.
    """
    previous = (
        MFMasterData.objects.filter(scheme=scheme, as_of_date__lt=as_of_date)
        .order_by("-as_of_date")
        .first()
    )
    if not previous:
        return
    for field in MFMasterData._meta.fields:
        name = field.name
        if name in SYSTEM_FIELDS or name in IDENTITY_FIELDS or name in AUM_FIELDS or name in DERIVED_FIELDS:
            continue
        old_value = getattr(previous, name)
        if _is_empty(getattr(snapshot, name)) and not _is_empty(old_value):
            setattr(snapshot, name, old_value)
    lineage = dict(previous.field_sources or {})
    lineage.update(snapshot.field_sources or {})
    snapshot.field_sources = lineage


def _choose_aum(scheme, as_of_date):
    """Select the freshest valid AUM observation, preserving its type/date."""
    quarter_observation = None
    quarters = AMFISchemeAAUM.objects.filter(
        scheme=scheme,
        quarter_start__lte=as_of_date,
    ).order_by("-quarter_start")
    for quarter in quarters:
        observed_on = _quarter_end(quarter.quarter_start)
        if observed_on <= as_of_date:
            quarter_observation = {
                "aum_cr": quarter.average_aum_cr,
                "aum_type": MFMasterData.AUM_QUARTERLY_AVG,
                "aum_as_of": observed_on,
                "aum_source": quarter.source,
                "source_url": _quarter_report_url(quarter.quarter_start, quarter.quarter_label),
                "as_of_date": quarter.quarter_start,
            }
            break

    month_end = (
        MFMasterData.objects.filter(
            scheme=scheme,
            as_of_date__lte=as_of_date,
            aum_type=MFMasterData.AUM_MONTH_END,
            aum_as_of__lte=as_of_date,
        )
        .exclude(aum_cr__isnull=True)
        .order_by("-aum_as_of", "-as_of_date")
        .first()
    )
    # Month-end is the more precise point-in-time observation when dates tie.
    if month_end and (
        quarter_observation is None
        or month_end.aum_as_of >= quarter_observation["aum_as_of"]
    ):
        lineage = (month_end.field_sources or {}).get("aum_cr", {})
        return {
            "aum_cr": month_end.aum_cr,
            "aum_type": MFMasterData.AUM_MONTH_END,
            "aum_as_of": month_end.aum_as_of,
            "aum_source": month_end.aum_source or lineage.get("source", ""),
            "source_url": lineage.get("source_url", ""),
            "as_of_date": month_end.as_of_date,
        }
    return quarter_observation


def _identity_values(scheme):
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


class Command(BaseCommand):
    help = "Build consolidated global mutual-fund masterdata snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", required=True, help="Snapshot date YYYY-MM-DD.")
        parser.add_argument("--amc", default="", help="Filter by AMC name fragment.")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--risk-free-rate", type=float, default=6.5,
            help="Annual risk-free rate in percent, default 6.5.",
        )
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

        run = None
        if not options["dry_run"]:
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
                identity = _identity_values(scheme)
                _, created = MFMasterData.objects.get_or_create(
                    scheme=scheme,
                    as_of_date=as_of_date,
                    defaults=identity,
                )
                if created:
                    totals["created"] += 1
                else:
                    totals["updated"] += 1

                # Record lineage for the current AMFI identity and mapped SEBI taxonomy.
                upsert_masterdata_snapshot(
                    scheme,
                    as_of_date=as_of_date,
                    values=identity,
                    source="AMFI_NAVALL_AND_SEBI_TAXONOMY",
                    source_url="https://portal.amfiindia.com/spages/NAVAll.txt",
                    field_names=list(identity),
                )
                snapshot = MFMasterData.objects.get(scheme=scheme, as_of_date=as_of_date)
                _inherit_latest_disclosures(snapshot, scheme, as_of_date)
                totals["with_ter"] += int(
                    snapshot.ter_direct_pct is not None or snapshot.ter_regular_pct is not None
                )

                # Clear prior calculated values on a same-date rebuild. Missing
                # current inputs must stay null rather than retain stale ratios.
                for field in DERIVED_FIELDS:
                    current = getattr(snapshot, field)
                    setattr(snapshot, field, {} if isinstance(current, dict) else None)
                snapshot.data_quality_flags = []
                snapshot.save()

                aum = _choose_aum(scheme, as_of_date)
                if aum:
                    upsert_masterdata_snapshot(
                        scheme,
                        as_of_date=as_of_date,
                        values={key: aum[key] for key in AUM_FIELDS},
                        source=aum["aum_source"] or "AMFI_SCHEME_AAUM",
                        source_url=aum["source_url"],
                        field_names=list(AUM_FIELDS),
                    )
                    totals["with_aum"] += 1

                metrics = calculate_scheme_masterdata_metrics(
                    scheme,
                    as_of_date,
                    risk_free_rate=risk_free_rate,
                    ratio_window_years=options["ratio_window_years"],
                )
                calculated = {
                    key: (_decimal(value) if key in DECIMAL_FIELDS else value)
                    for key, value in metrics["values"].items()
                    if key in DERIVED_FIELDS and value is not None
                }

                nav_fields = {
                    "return_1m_pct", "return_3m_pct", "return_6m_pct",
                    "return_1y_pct", "return_3y_pct", "return_5y_pct",
                    "return_10y_pct", "since_inception_cagr_pct",
                    "std_dev_pct", "sharpe", "sortino", "max_drawdown_pct",
                    "risk_free_rate_used_pct",
                }
                nav_values = {key: calculated[key] for key in nav_fields if key in calculated}
                if nav_values:
                    upsert_masterdata_snapshot(
                        scheme,
                        as_of_date=as_of_date,
                        values=nav_values,
                        source="PWMS_CALCULATED_NAV",
                        source_url=metrics["source_urls"].get("nav", ""),
                        field_names=list(nav_values),
                        quality_flags=metrics["data_quality_flags"],
                    )

                benchmark_fields = {
                    "beta", "alpha_pct", "treynor", "r_squared",
                    "tracking_error_pct", "information_ratio",
                    "upside_capture_pct", "downside_capture_pct", "ratio_window_years",
                }
                benchmark_values = {
                    key: calculated[key] for key in benchmark_fields if key in calculated
                }
                if benchmark_values:
                    upsert_masterdata_snapshot(
                        scheme,
                        as_of_date=as_of_date,
                        values=benchmark_values,
                        source="PWMS_CALCULATED_BENCHMARK",
                        source_url=metrics["source_urls"].get("benchmark", ""),
                        field_names=list(benchmark_values),
                        quality_flags=metrics["data_quality_flags"],
                    )
                totals["with_benchmark_ratios"] += int(calculated.get("beta") is not None)

                holdings_fields = {
                    "number_of_holdings", "top10_concentration_pct",
                    "large_cap_pct", "mid_cap_pct", "small_cap_pct", "other_cap_pct",
                    "equity_pct", "debt_pct", "cash_pct", "others_pct",
                    "credit_rating_split", "sector_allocation", "ytm_pct",
                }
                holdings_values = {
                    key: calculated[key] for key in holdings_fields if key in calculated
                }
                if holdings_values:
                    upsert_masterdata_snapshot(
                        scheme,
                        as_of_date=as_of_date,
                        values=holdings_values,
                        source="PWMS_CALCULATED_HOLDINGS",
                        source_url=metrics["source_urls"].get("portfolio", ""),
                        field_names=list(holdings_values),
                        quality_flags=metrics["data_quality_flags"],
                    )
                    totals["with_portfolio"] += int(
                        calculated.get("number_of_holdings") is not None
                    )

                final = MFMasterData.objects.get(scheme=scheme, as_of_date=as_of_date)
                flags = set(final.data_quality_flags or [])
                flags.update(metrics["data_quality_flags"])
                final.data_quality_flags = sorted(flags)
                final.save(update_fields=["data_quality_flags", "updated_at"])

                for field in MFMasterData._meta.fields:
                    if field.name in SYSTEM_FIELDS:
                        continue
                    if not _is_empty(getattr(final, field.name)):
                        coverage[field.name] = coverage.get(field.name, 0) + 1
            except Exception as exc:
                totals["errors"] += 1
                if len(errors) < 100:
                    errors.append({
                        "scheme_code": scheme.scheme_code,
                        "scheme_name": scheme.scheme_name,
                        "error": str(exc)[:1000],
                    })

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(
                f"DRY RUN: schemes={totals['schemes']} with AUM={totals['with_aum']} "
                f"with TER={totals['with_ter']} with portfolio={totals['with_portfolio']} "
                f"with benchmark ratios={totals['with_benchmark_ratios']}. "
                "No MFMasterData or MFSourceRun records were written."
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
            self.stdout.write(self.style.SUCCESS(
                f"Built {totals['schemes']} scheme snapshots for {as_of_date}: "
                f"created={totals['created']} updated={totals['updated']} "
                f"errors={totals['errors']}."
            ))

        self.stdout.write("Field coverage (populated / schemes processed):")
        for field in sorted(coverage):
            self.stdout.write(f"  {field}: {coverage[field]} / {totals['schemes']}")
