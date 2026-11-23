"""Import one or more scheme holdings snapshots from MF Forecast."""
import os
from urllib.parse import urlparse

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from mutual_funds.models import AMFIMasterScheme, MFSourceRun
from mutual_funds.master_models import MFExternalPortfolioSnapshot, MFMasterHolding
from mutual_funds.services.masterdata import (
    finish_source_run,
    persist_portfolio_rows,
    start_source_run,
)
from mutual_funds.services.mfforecast import (
    fetch_mfforecast_payload,
    parse_mfforecast_holdings,
)


SOURCE = "MFFORECAST_API"


class Command(BaseCommand):
    help = (
        "Import bounded scheme holdings snapshots from MF Forecast. "
        "The documented response does not include a disclosure date."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--scheme-code", action="append", dest="scheme_codes", default=[],
            help="AMFI scheme code. Repeat to request multiple schemes.",
        )
        parser.add_argument("--amc", default="", help="Optional AMC name filter.")
        parser.add_argument(
            "--limit", type=int, default=50,
            help="Maximum number of scheme requests (default: 50).",
        )
        parser.add_argument("--base-url", default="", help="Override MFFORECAST_API_BASE_URL.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        base_url = (
            options["base_url"] or os.environ.get("MFFORECAST_API_BASE_URL", "")
        ).strip().rstrip("/")
        if not base_url:
            raise CommandError(
                "Set MFFORECAST_API_BASE_URL to the API base URL specified by MF Forecast."
            )
        parsed_base = urlparse(base_url)
        if parsed_base.scheme != "https" or not parsed_base.netloc:
            raise CommandError("MFFORECAST_API_BASE_URL must be an absolute HTTPS URL.")

        api_key = os.environ.get("MFFORECAST_API_KEY", "").strip()
        api_key_header = os.environ.get("MFFORECAST_API_KEY_HEADER", "").strip()
        if bool(api_key) != bool(api_key_header):
            raise CommandError(
                "Set both MFFORECAST_API_KEY and MFFORECAST_API_KEY_HEADER, "
                "or leave both blank. The header name must come from provider documentation."
            )
        headers = {api_key_header: api_key} if api_key else {}

        try:
            timeout = int(os.environ.get("MFFORECAST_API_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise CommandError("MFFORECAST_API_TIMEOUT_SECONDS must be an integer.") from exc
        if not 1 <= timeout <= 120:
            raise CommandError("MFFORECAST_API_TIMEOUT_SECONDS must be between 1 and 120.")

        scheme_codes = list(dict.fromkeys(str(code).strip() for code in options["scheme_codes"] if str(code).strip()))
        amc = options["amc"].strip()
        limit = options["limit"]
        if not 1 <= limit <= 50:
            raise CommandError("--limit must be between 1 and 50 to keep provider usage bounded.")
        if len(scheme_codes) > limit:
            raise CommandError("The number of --scheme-code values cannot exceed --limit.")
        if not scheme_codes and not amc:
            raise CommandError("Specify at least one --scheme-code or an --amc filter; unbounded full-universe calls are disabled.")

        schemes_qs = AMFIMasterScheme.objects.filter(is_active=True)
        if scheme_codes:
            schemes_qs = schemes_qs.filter(scheme_code__in=scheme_codes)
        if amc:
            schemes_qs = schemes_qs.filter(amc_name__icontains=amc)
        schemes = list(schemes_qs.order_by("scheme_code")[:limit])
        found_codes = {scheme.scheme_code for scheme in schemes}
        missing_codes = sorted(set(scheme_codes) - found_codes)
        if missing_codes:
            raise CommandError("Unknown or inactive AMFI scheme code(s): " + ", ".join(missing_codes))
        if not schemes:
            raise CommandError("No active AMFI schemes matched the supplied selector.")

        dry_run = options["dry_run"]
        run = None if dry_run else start_source_run(
            source=SOURCE,
            command="sync_mfforecast_holdings",
            as_of_date=None,
            source_url=base_url,
        )
        observed_start = timezone.now()
        counters = {
            "schemes_requested": len(schemes),
            "schemes_with_response": 0,
            "schemes_not_found": 0,
            "holdings_parsed": 0,
            "holdings_inserted": 0,
            "holdings_updated": 0,
            "holdings_skipped": 0,
            "snapshots_saved": 0,
        }
        errors = []
        fatal_error = ""

        try:
            with requests.Session() as session:
                for scheme in schemes:
                    try:
                        payload, endpoint, raw_path = fetch_mfforecast_payload(
                            session=session,
                            base_url=base_url,
                            scheme_code=scheme.scheme_code,
                            headers=headers,
                            timeout=timeout,
                        )
                        if payload is None:
                            counters["schemes_not_found"] += 1
                            counters["holdings_skipped"] += 1
                            continue

                        parsed = parse_mfforecast_holdings(
                            payload, scheme_code=scheme.scheme_code,
                        )
                        observed_at = timezone.now()
                        portfolio_date = timezone.localdate(observed_at)
                        rows = parsed["holdings"]
                        counters["schemes_with_response"] += 1
                        counters["holdings_parsed"] += len(rows)

                        if dry_run:
                            self.stdout.write(
                                f"{scheme.scheme_code} | {scheme.scheme_name} | "
                                f"provider_name={parsed['scheme_name'] or 'not supplied'} | "
                                f"holdings={len(rows)} | weight_total={parsed['total_weight_pct']}"
                            )
                            continue

                        with transaction.atomic():
                            MFExternalPortfolioSnapshot.objects.create(
                                scheme=scheme,
                                provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
                                observed_at=observed_at,
                                provider_as_of_date=None,
                                scheme_name=parsed["scheme_name"],
                                fund_house=parsed["fund_house"],
                                category=parsed["category"],
                                aum_cr=parsed["aum_cr"],
                                expense_ratio_pct=parsed["expense_ratio_pct"],
                                asset_allocation=parsed["asset_allocation"],
                                holdings_count=parsed["holdings_count"],
                                total_weight_pct=parsed["total_weight_pct"],
                                quality_flags=parsed["quality_flags"],
                                source_url=endpoint,
                                raw_file_path=raw_path,
                            )
                            result = persist_portfolio_rows(
                                scheme,
                                rows,
                                portfolio_date=portfolio_date,
                                portfolio_date_basis=MFMasterHolding.DATE_BASIS_FETCHED_AT,
                                source=SOURCE,
                                source_url=endpoint,
                            )
                            counters["holdings_inserted"] += result["inserted"]
                            counters["holdings_updated"] += result["updated"]
                            counters["holdings_skipped"] += result["skipped"]
                            counters["snapshots_saved"] += 1

                    except PermissionError as exc:
                        fatal_error = str(exc)
                        errors.append({"scheme_code": scheme.scheme_code, "error": fatal_error})
                        break
                    except Exception as exc:
                        if len(errors) < 100:
                            errors.append({
                                "scheme_code": scheme.scheme_code,
                                "error": str(exc)[:1000],
                            })
                        continue
        except requests.RequestException as exc:
            fatal_error = f"MF Forecast HTTP session failed: {exc}"
            errors.append({"error": fatal_error})

        if run:
            if fatal_error:
                status = (
                    MFSourceRun.Status.PARTIAL
                    if counters["snapshots_saved"] else MFSourceRun.Status.FAILED
                )
            elif errors or counters["schemes_not_found"]:
                status = MFSourceRun.Status.PARTIAL
            else:
                status = MFSourceRun.Status.SUCCESS
            finish_source_run(
                run,
                status=status,
                parsed=counters["holdings_parsed"],
                inserted=counters["holdings_inserted"],
                updated=counters["holdings_updated"],
                skipped=counters["holdings_skipped"],
                unmatched=len(errors),
                errors=errors,
                metadata={
                    **counters,
                    "api_base_url": base_url,
                    "api_key_configured": bool(api_key),
                    "observation_started_at": observed_start.isoformat(),
                    "observation_completed_at": timezone.now().isoformat(),
                    "portfolio_date_basis": "FETCHED_AT",
                },
            )

        summary = (
            f"schemes={counters['schemes_requested']} "
            f"responses={counters['schemes_with_response']} "
            f"not_found={counters['schemes_not_found']} "
            f"holdings={counters['holdings_parsed']} "
            f"inserted={counters['holdings_inserted']} "
            f"updated={counters['holdings_updated']} "
            f"skipped={counters['holdings_skipped']} "
            f"errors={len(errors)} dry_run={dry_run}"
        )
        if dry_run:
            self.stdout.write(self.style.SUCCESS("DRY RUN: " + summary + ". No database records were written."))
        else:
            self.stdout.write(self.style.SUCCESS("MF Forecast import completed: " + summary + "."))
        if fatal_error:
            raise CommandError(fatal_error)
