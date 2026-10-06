from datetime import datetime
from decimal import Decimal, InvalidOperation
from dateutil.relativedelta import relativedelta
import logging
import time

import requests

from django.db import transaction
from django.db.models import OuterRef, Q, Subquery

from mutual_funds.models import (
    AMFIMasterNAV,
    AMFIMasterScheme,
    MutualFundNAV,
    MutualFundScheme,
)


logger = logging.getLogger(__name__)


class AMFIService:
    """
    Service for downloading and processing mutual-fund
    NAV data from AMFI.
    """

    NAV_URL = (
        "https://www.amfiindia.com/spages/NAVAll.txt"
    )

    NAV_HISTORY_URL = (
        "https://portal.amfiindia.com/"
        "DownloadNAVHistoryReport_Po.aspx"
    )
    AMFI_API_URL = "https://www.amfiindia.com"
    AMFI_LATEST_API = f"{AMFI_API_URL}/api/latest-nav"
    AMFI_SCHEME_LIST_API = f"{AMFI_API_URL}/api/get-nav-history/navs"
    AMFI_HISTORY_API = f"{AMFI_API_URL}/api/nav-history"

    @staticmethod
    def _headers():
        return {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/151.0 Safari/537.36"
            )
        }

    @staticmethod
    def download_latest_nav():
        """
        Download the latest NAV text file from AMFI.
        """

        response = requests.get(
            AMFIService.NAV_URL,
            headers=AMFIService._headers(),
            timeout=30,
        )

        response.raise_for_status()

        return response.text

    @staticmethod
    def _api_headers():
        return {
            **AMFIService._headers(),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.amfiindia.com/net-asset-value",
        }

    @staticmethod
    def _normalize_name(value):
        return " ".join(str(value or "").lower().split())

    @staticmethod
    def _normalize_identity(value):
        value = AMFIService._normalize_name(value)
        for token in ("-", "_", "/", "(", ")", ",", "."):
            value = value.replace(token, " ")
        return " ".join(value.split())

    @staticmethod
    def _first_value(record, *keys):
        if not isinstance(record, dict):
            return None
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _extract_identity(record, inherited=None):
        inherited = inherited or {}
        code = AMFIService._first_value(
            record,
            "schemeId", "scheme_id", "Scheme_Code", "scheme_code", "schemeCode",
        )
        name = AMFIService._first_value(
            record,
            "schemeName", "Scheme_Name", "scheme_name", "nav_name",
        )
        mf_id = AMFIService._first_value(
            record, "mutualFundId", "MF_ID", "mf_id", "mutual_fund_id"
        ) or inherited.get("mf_id")
        isin_growth = AMFIService._first_value(
            record,
            "isin_growth", "ISIN_Growth", "isinGrowth", "ISINPrimary",
            "isin", "ISIN", "ISIN_Div_Payout_ISIN_Growth",
        ) or inherited.get("isin_growth")
        isin_dividend = AMFIService._first_value(
            record,
            "isin_dividend", "ISIN_Dividend", "isinDividend", "ISINReinvestment",
            "ISIN_Div_Reinvestment",
        ) or inherited.get("isin_dividend")
        plan = AMFIService._first_value(
            record, "plan", "Plan", "planName", "plan_name"
        ) or inherited.get("plan")
        option = AMFIService._first_value(
            record, "option", "Option", "optionName", "option_name"
        ) or inherited.get("option")
        fund_type = AMFIService._first_value(
            record, "type", "fundType", "fund_type"
        ) or inherited.get("fund_type")

        return {
            "scheme_code": str(code).strip() if code else None,
            "scheme_name": str(name).strip() if name else None,
            "mf_id": str(mf_id).strip() if mf_id else None,
            "isin_growth": str(isin_growth).strip() if isin_growth else None,
            "isin_dividend": str(isin_dividend).strip() if isin_dividend else None,
            "plan": str(plan).strip() if plan else None,
            "option": str(option).strip() if option else None,
            "fund_type": str(fund_type).strip() if fund_type else None,
        }

    @staticmethod
    def _flatten_latest_api(data):
        records = []

        def walk(node, inherited=None):
            inherited = inherited or {}
            if isinstance(node, dict):
                current = dict(inherited)
                extracted = AMFIService._extract_identity(node, current)
                for key in (
                    "mf_id", "isin_growth", "isin_dividend",
                    "plan", "option", "fund_type",
                ):
                    if extracted.get(key):
                        current[key] = extracted[key]

                if extracted.get("scheme_code") and extracted.get("scheme_name") and extracted.get("mf_id"):
                    records.append(extracted)

                for key, value in node.items():
                    if key in {"mutualFundId", "MF_ID", "mf_id"} and value:
                        current["mf_id"] = str(value).strip()
                    elif key in {"type", "fundType", "fund_type"} and value:
                        current["fund_type"] = str(value).strip()
                    walk(value, current)
            elif isinstance(node, list):
                for item in node:
                    walk(item, inherited)

        walk(data)
        deduped = {}
        for record in records:
            code = record["scheme_code"]
            existing = deduped.get(code)
            if existing is None:
                deduped[code] = record
                continue
            for key, value in record.items():
                if value and not existing.get(key):
                    existing[key] = value
        return deduped

    @staticmethod
    def _normalize_candidate(candidate, mf_id=None):
        if not isinstance(candidate, dict):
            return None
        identity = AMFIService._extract_identity(
            candidate,
            {"mf_id": str(mf_id).strip() if mf_id else None},
        )
        nav_id = AMFIService._first_value(
            candidate, "nav_id", "navId", "schemeDetailId",
            "scheme_detail_id", "sd_id", "id",
        )
        identity["nav_id"] = str(nav_id).strip() if nav_id else None
        return identity

    @staticmethod
    def _metadata_for_code(code):
        metadata = {
            "scheme_code": str(code).strip(),
            "scheme_name": None,
            "mf_id": None,
            "isin_growth": None,
            "isin_dividend": None,
            "plan": None,
            "option": None,
            "fund_type": None,
        }

        master = (
            AMFIMasterScheme.objects
            .filter(scheme_code=code)
            .values(
                "scheme_name", "isin_growth", "isin_dividend"
            )
            .first()
        )
        if master:
            metadata.update({
                key: value
                for key, value in master.items()
                if value not in (None, "")
            })

        family_rows = list(
            MutualFundScheme.objects
            .filter(scheme_code=code)
            .values(
                "scheme_name", "isin_growth", "isin_dividend",
                "plan", "option",
            )[:10]
        )
        if family_rows:
            # Prefer a row whose identity agrees with the master name/ISIN.
            row = family_rows[0]
            master_name = AMFIService._normalize_identity(metadata["scheme_name"])
            for candidate in family_rows:
                candidate_name = AMFIService._normalize_identity(candidate["scheme_name"])
                if master_name and candidate_name == master_name:
                    row = candidate
                    break
                if metadata["isin_growth"] and metadata["isin_growth"] in {
                    candidate.get("isin_growth"), candidate.get("isin_dividend")
                }:
                    row = candidate
                    break
            for key, value in row.items():
                if value not in (None, ""):
                    metadata[key] = value

        return metadata

    @staticmethod
    def _candidate_score(metadata, candidate):
        score = 0
        methods = []

        target_isins = {
            str(metadata.get("isin_growth") or "").strip().lower(),
            str(metadata.get("isin_dividend") or "").strip().lower(),
        } - {""}
        candidate_isins = {
            str(candidate.get("isin_growth") or "").strip().lower(),
            str(candidate.get("isin_dividend") or "").strip().lower(),
        } - {""}

        if target_isins & candidate_isins:
            score += 1000
            methods.append("isin")

        # The AMFI scheme code is the canonical identifier. Some AMC
        # scheme-list responses contain similarly named sibling plans, so an
        # exact nav_id/code match must outrank name-token similarity.
        if candidate.get("nav_id") == metadata.get("scheme_code"):
            score += 5000
            methods.append("scheme_code")

        if metadata.get("mf_id") and candidate.get("mf_id") == metadata["mf_id"]:
            score += 250

        target_name = AMFIService._normalize_identity(metadata.get("scheme_name"))
        candidate_name = AMFIService._normalize_identity(candidate.get("scheme_name"))
        if target_name and candidate_name == target_name:
            score += 500
            methods.append("exact_name")
        elif target_name and candidate_name:
            target_tokens = set(target_name.split())
            candidate_tokens = set(candidate_name.split())
            overlap = len(target_tokens & candidate_tokens)
            if overlap:
                score += min(200, overlap * 20)
            if target_name in candidate_name or candidate_name in target_name:
                score += 50
                methods.append("structured_name")

        target_plan = AMFIService._normalize_identity(metadata.get("plan"))
        candidate_plan = AMFIService._normalize_identity(candidate.get("plan"))
        if target_plan and candidate_plan and target_plan == candidate_plan:
            score += 100

        target_option = AMFIService._normalize_identity(metadata.get("option"))
        candidate_option = AMFIService._normalize_identity(candidate.get("option"))
        if target_option and candidate_option and target_option == candidate_option:
            score += 100

        # Derive plan/option from names when AMFI omits separate fields.
        combined_target = f"{target_name} {target_plan} {target_option}"
        combined_candidate = f"{candidate_name} {candidate_plan} {candidate_option}"
        for token, weight in (("direct", 30), ("regular", 30), ("growth", 40), ("idcw", 40), ("dividend", 40)):
            if token in combined_target and token in combined_candidate:
                score += weight

        if "scheme_code" in methods:
            return score, "scheme_code"
        if "exact_name" in methods and target_isins & candidate_isins:
            return score, "isin"
        if target_isins & candidate_isins:
            return score, "isin"
        if "exact_name" in methods:
            return score, "exact_name"
        if "structured_name" in methods:
            return score, "structured_name"
        return score, "fallback"

    @staticmethod
    def _resolve_nav_ids(scheme_codes):
        codes = {str(code).strip() for code in scheme_codes}
        if not codes:
            return {}

        latest_by_code = {}
        requested = {}

        def refresh_requested():
            requested.clear()
            requested.update({
                code: latest_by_code[code]
                for code in codes
                if code in latest_by_code
            })

        # AMFI documents the supported latest-NAV type filters as blank/all,
        # Open Ended, Close Ended, and Interval Fund. Query the broad response
        # first, then only unresolved codes through explicit types.
        for fund_type in ("", "Open Ended", "Close Ended", "Interval Fund"):
            if fund_type and len(requested) == len(codes):
                break
            response = requests.get(
                AMFIService.AMFI_LATEST_API,
                params={"mfid": "all", "type": fund_type},
                headers=AMFIService._api_headers(),
                timeout=60,
            )
            response.raise_for_status()
            latest_by_code.update(
                AMFIService._flatten_latest_api(response.json())
            )
            refresh_requested()

        unresolved_codes = sorted(codes - requested.keys())
        if unresolved_codes:
            raise RuntimeError(
                "AMFI current API did not resolve scheme codes: "
                + ", ".join(unresolved_codes)
            )

        # Merge API metadata with persisted master/family identity hints.
        for code in list(requested):
            stored = AMFIService._metadata_for_code(code)
            api_metadata = requested[code]
            for key, value in stored.items():
                if value not in (None, ""):
                    api_metadata[key] = value

        scheme_lists = {}
        for mf_id in {item["mf_id"] for item in requested.values()}:
            response = requests.get(
                AMFIService.AMFI_SCHEME_LIST_API,
                params={"mf_id": mf_id},
                headers=AMFIService._api_headers(),
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            raw = payload.get("data", payload) if isinstance(payload, dict) else payload
            if isinstance(raw, dict):
                raw = raw.get("schemes", raw.get("navs", []))
            scheme_lists[mf_id] = [
                candidate
                for candidate in (raw or [])
                if isinstance(candidate, dict)
            ]

        result = {}
        for code in sorted(codes):
            metadata = requested[code]
            candidates = [
                normalized
                for item in scheme_lists.get(metadata["mf_id"], [])
                if (normalized := AMFIService._normalize_candidate(
                    item, metadata["mf_id"]
                ))
                and normalized.get("nav_id")
            ]

            scored = []
            for candidate in candidates:
                score, method = AMFIService._candidate_score(metadata, candidate)
                scored.append((score, method, candidate))

            scored.sort(
                key=lambda item: (
                    item[0],
                    item[2].get("nav_id") or "",
                ),
                reverse=True,
            )

            if not scored or scored[0][0] <= 0:
                candidate_ids = [item[2].get("nav_id") for item in scored]
                candidate_names = [item[2].get("scheme_name") for item in scored]
                raise RuntimeError(
                    "AMFI scheme resolution failed: "
                    f"code={code} mf_id={metadata.get('mf_id')} "
                    f"requested_name={metadata.get('scheme_name')} "
                    f"candidate_count={len(scored)} "
                    f"candidate_nav_ids={candidate_ids} "
                    f"candidate_names={candidate_names}"
                )

            best_score, match_method, best = scored[0]
            ties = [
                item for item in scored
                if item[0] == best_score
            ]
            if len(ties) > 1:
                raise RuntimeError(
                    "AMFI scheme resolution ambiguous: "
                    f"code={code} mf_id={metadata.get('mf_id')} "
                    f"requested_name={metadata.get('scheme_name')} "
                    f"candidate_nav_ids={[item[2].get('nav_id') for item in ties]} "
                    f"candidate_names={[item[2].get('scheme_name') for item in ties]}"
                )

            logger.info(
                "AMFI scheme resolution: code=%s fund_type=%s mf_id=%s "
                "scheme_name=%s matched_nav_id=%s match_method=%s",
                code,
                metadata.get("fund_type") or "",
                metadata.get("mf_id") or "",
                metadata.get("scheme_name") or "",
                best["nav_id"],
                match_method,
            )

            result[code] = {
                "nav_id": best["nav_id"],
                "scheme_name": metadata.get("scheme_name") or best.get("scheme_name"),
                "match_method": match_method,
                "mf_id": metadata.get("mf_id"),
            }

        return result

    @staticmethod
    def _download_historical_api_records(from_date, to_date, scheme_codes):
        """Download authoritative AMFI history grouped by AMC.

        The legacy AMFI historical endpoint expects ``mf`` to be an AMC
        identifier. Resolve requested schemes through AMFI's current metadata
        API, group them by AMC, and download only relevant AMC reports.
        """
        if from_date > to_date:
            raise ValueError("From date cannot be after to_date.")

        requested_codes = {
            str(code).strip()
            for code in (scheme_codes or [])
            if str(code or "").strip()
        }
        if not requested_codes:
            return []

        resolved = AMFIService._resolve_nav_ids(requested_codes)
        mf_ids_by_code = {
            code: str(item.get("mf_id") or "").strip()
            for code, item in resolved.items()
            if item.get("mf_id")
        }
        missing_mf_ids = requested_codes - set(mf_ids_by_code)
        if missing_mf_ids:
            raise RuntimeError(
                "AMFI could not resolve AMC identifiers for scheme codes: "
                + ", ".join(sorted(missing_mf_ids))
            )

        codes_by_mf = {}
        for code, mf_id in mf_ids_by_code.items():
            codes_by_mf.setdefault(mf_id, set()).add(code)

        headers = {
            **AMFIService._headers(),
            "Accept": "text/plain,text/csv,text/*;q=0.9,*/*;q=0.8",
            "Referer": "https://www.amfiindia.com/net-asset-value/nav-download",
        }
        records = []

        for mf_id, mf_codes in sorted(codes_by_mf.items()):
            window_start = from_date
            while window_start <= to_date:
                window_end = min(
                    window_start + relativedelta(days=AMFIService.HISTORICAL_WINDOW_DAYS - 1),
                    to_date,
                )
                params = {
                    "mf": mf_id,
                    "tp": "1",
                    "frmdt": window_start.strftime("%d-%b-%Y"),
                    "todt": window_end.strftime("%d-%b-%Y"),
                }

                report_text = ""
                last_error = None
                for attempt in range(AMFIService.HISTORICAL_MAX_RETRIES):
                    try:
                        response = requests.get(
                            AMFIService.NAV_HISTORY_URL,
                            params=params,
                            headers=headers,
                            timeout=90,
                        )
                        response.raise_for_status()
                        candidate = response.text or ""
                        if not AMFIService._is_historical_report(candidate, scheme_codes=mf_codes):
                            raise RuntimeError(
                                "AMFI historical response was not a NAV report "
                                f"(mf={mf_id}, bytes={len(response.content)})"
                            )
                        report_text = candidate
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt + 1 < AMFIService.HISTORICAL_MAX_RETRIES:
                            time.sleep(1.5 * (attempt + 1))

                if not report_text:
                    raise RuntimeError(
                        "Unable to download AMFI historical NAV report "
                        f"for mf={mf_id}, {window_start} to {window_end}"
                    ) from last_error

                window_records = AMFIService.parse_nav_file(
                    report_text,
                    historical=True,
                    scheme_codes=mf_codes,
                )
                if window_records:
                    records.extend(window_records)

                window_start = window_end + relativedelta(days=1)

        deduped = {
            (record["scheme_code"], record["date"]): record
            for record in records
            if record["scheme_code"] in requested_codes
        }
        matched_codes = {record["scheme_code"] for record in deduped.values()}
        missing_codes = requested_codes - matched_codes
        if missing_codes:
            logger.warning(
                "AMFI historical backfill returned no rows for scheme codes: %s "
                "range=%s to %s",
                sorted(missing_codes),
                from_date,
                to_date,
            )
        return sorted(
            deduped.values(),
            key=lambda item: (item["scheme_code"], item["date"]),
        )
    @staticmethod
    def download_historical_nav(
        from_date,
        to_date,
        scheme_codes=None,
    ):
        """
        Download the real AMFI historical NAV report.

        AMFI documents a maximum 90-day range. The historical download
        endpoint is a text report even though AMFI can return an HTML
        WebForms page for an unsuccessful request. Treat a successful HTTP
        status as insufficient: only a response containing the AMFI
        historical header is accepted as report data.
        """
        if scheme_codes:
            return AMFIService._download_historical_api_records(
                from_date,
                to_date,
                scheme_codes,
            )

        if from_date > to_date:
            raise ValueError("From date cannot be after to_date.")

        # The public command accepts arbitrary historical ranges. Split long
        # requests here so callers do not have to manually create 90-day
        # batches. The scheme-specific path already performs its own chunking.
        if (to_date - from_date).days > 90:
            records = []
            window_start = from_date
            while window_start <= to_date:
                window_end = min(
                    window_start + relativedelta(days=89),
                    to_date,
                )
                report_text = AMFIService.download_historical_nav(
                    window_start,
                    window_end,
                )
                records.extend(
                    AMFIService.parse_nav_file(
                        report_text,
                        historical=True,
                    )
                )
                window_start = window_end + relativedelta(days=1)
            return records

        date_params = {
            "frmdt": from_date.strftime("%d-%b-%Y"),
            "todt": to_date.strftime("%d-%b-%Y"),
        }
        headers = {
            **AMFIService._headers(),
            "Accept": "text/plain,text/csv,text/*;q=0.9,*/*;q=0.8",
            "Referer": "https://www.amfiindia.com/net-asset-value/nav-download",
        }

        # AMFI's all-schemes historical download is addressable directly by
        # date range. Do not send mf=0: the current portal treats that value
        # as an invalid AMC selection and returns its HTML WebForms page.
        # Keep tp=1 as the preferred text-report mode, with a fallback that
        # lets the endpoint infer the report mode.
        attempts = (
            {"tp": "1", **date_params},
            date_params,
        )
        last_response = None

        with requests.Session() as session:
            for params in attempts:
                response = session.get(
                    AMFIService.NAV_HISTORY_URL,
                    params=params,
                    headers=headers,
                    timeout=60,
                )
                last_response = response
                text = response.text or ""

                # Do not accept an HTTP 200 WebForms/error page as if it
                # were the downloadable report. This was the reason the
                # previous importer could silently return zero records.
                if (
                    200 <= int(getattr(response, "status_code", 0) or 0) < 300
                    and AMFIService._is_historical_report(text)
                ):
                    return text

                preview = " ".join(text.split())[:240]
                logger.warning(
                    "AMFI historical response was not a NAV report: "
                    "endpoint=%s status=%s content_type=%s "
                    "from=%s to=%s bytes=%s preview=%r",
                    getattr(response, "url", AMFIService.NAV_HISTORY_URL),
                    getattr(response, "status_code", None),
                    getattr(response, "headers", {}).get("Content-Type", ""),
                    from_date,
                    to_date,
                    len(getattr(response, "content", text.encode("utf-8"))),
                    preview,
                )

        if last_response is not None:
            last_response.raise_for_status()
            raise RuntimeError(
                "AMFI historical endpoint returned an unexpected response "
                f"for {from_date} to {to_date} "
                f"(status={getattr(last_response, 'status_code', None)}, "
                f"content_type={getattr(last_response, 'headers', {}).get('Content-Type', '')}, "
                f"bytes={len(getattr(last_response, 'content', b''))})."
            )

        raise RuntimeError("AMFI historical endpoint returned no response.")

    @staticmethod
    def _is_historical_report(text, scheme_codes=None):
        """Return True for AMFI historical text, including header variants."""
        if not text:
            return False

        lines = [
            line.strip().replace("\ufeff", "")
            for line in text.splitlines()
            if line.strip()
        ]
        if not lines:
            return False

        required = {
            "scheme code",
            "net asset value",
            "date",
        }
        for line in lines[:100]:
            tokens = {token.strip().lower() for token in line.split(";")}
            if required.issubset(tokens) and (
                "scheme name" in tokens or "nav name" in tokens
            ):
                return True

        # AMFI has changed/added report preamble and header text over time.
        # A real historical report is still identifiable by its 8-column
        # semicolon-delimited scheme rows. When codes are supplied, require
        # one of those requested codes to appear in a valid NAV/date row.
        requested = {
            str(code).strip()
            for code in (scheme_codes or [])
            if str(code or "").strip()
        }
        # Some AMFI responses omit the recognizable header but still contain
        # valid historical rows. Validate the payload structurally rather than
        # requiring a particular requested scheme to appear in this chunk.
        # The current report puts NAV at index 6; older variants used index 4.
        for line in lines:
            parts = [part.strip() for part in line.split(";")]
            if len(parts) < 8 or not parts[0].isdigit():
                continue
            try:
                datetime.strptime(parts[-1], "%d-%b-%Y")
            except (ValueError, TypeError):
                continue
            nav_values = []
            for index in (6, 4, len(parts) - 2):
                if index >= len(parts):
                    continue
                try:
                    nav_values.append(Decimal(parts[index]))
                except (InvalidOperation, ValueError, TypeError):
                    continue
            if nav_values:
                return True
        return False

    @staticmethod
    def _build_record(
        scheme_code,
        isin_first,
        isin_second,
        scheme_name,
        nav,
        nav_date,
    ):
        """
        Build a normalized AMFI NAV record.
        """

        scheme_name_lower = (
            scheme_name.lower()
        )

        isin_growth = None
        isin_dividend = None

        if (
            "growth" in scheme_name_lower
            and "idcw" not in scheme_name_lower
            and "dividend" not in scheme_name_lower
        ):

            if (
                isin_first
                and isin_first != "-"
            ):
                isin_growth = isin_first

        else:

            if (
                isin_first
                and isin_first != "-"
            ):
                isin_dividend = isin_first

            elif (
                isin_second
                and isin_second != "-"
            ):
                isin_dividend = isin_second

        return {
            "scheme_code": scheme_code,
            "isin_growth": isin_growth,
            "isin_dividend": isin_dividend,
            "scheme_name": scheme_name,
            "nav": nav,
            "date": nav_date,
        }

    @staticmethod
    def _parse_latest_record(parts):
        """
        Parse latest AMFI NAV format.

        0 = Scheme Code
        1 = ISIN Div Payout / ISIN Growth
        2 = ISIN Div Reinvestment
        3 = Scheme Name
        4 = Plan
        5 = Option
        6 = NAV
        7 = Date

        AMFI added the Plan/Option columns to this feed
        after this parser was originally written, which
        shifted NAV and Date two columns to the right.
        """

        # AMFI has published both the 8-column and newer compact 6-column
        # latest formats. In both formats NAV is the penultimate field and
        # Date is the final field.
        if len(parts) < 6:
            return None

        scheme_code = parts[0]
        isin_first = parts[1]
        isin_second = parts[2]
        scheme_name = ";".join(parts[3:-2]).strip()
        nav_text = parts[-2]
        date_text = parts[-1]

        if not scheme_code.isdigit():
            return None

        if not scheme_name:
            return None

        try:
            nav = Decimal(nav_text)
        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ):
            return None

        if nav < 0:
            return None

        try:
            nav_date = datetime.strptime(
                date_text,
                "%d-%b-%Y",
            ).date()
        except ValueError:
            return None

        return AMFIService._build_record(
            scheme_code=scheme_code,
            isin_first=isin_first,
            isin_second=isin_second,
            scheme_name=scheme_name,
            nav=nav,
            nav_date=nav_date,
        )

    @staticmethod
    def _parse_historical_record(parts, positions=None):
        """Parse one row from AMFI's current historical text report."""
        if len(parts) < 8:
            return None

        if positions:
            scheme_index = positions.get("scheme_code", 0)
            name_index = positions.get("scheme_name", 1)
            nav_index = positions.get("nav", 4)
            date_index = positions.get("date", len(parts) - 1)
            isin_first_index = positions.get("isin_first", 2)
            isin_second_index = positions.get("isin_second", 3)
        else:
            scheme_index = 0
            name_index = 1
            nav_index = 4
            date_index = len(parts) - 1
            isin_first_index = 2
            isin_second_index = 3

        if max(
            scheme_index,
            name_index,
            nav_index,
            date_index,
            isin_first_index,
            isin_second_index,
        ) >= len(parts):
            return None

        scheme_code = parts[scheme_index]
        scheme_name = parts[name_index]
        if not scheme_code.isdigit() or not scheme_name:
            return None

        try:
            nav = Decimal(parts[nav_index])
        except (InvalidOperation, ValueError, TypeError):
            return None
        if nav < 0:
            return None

        try:
            nav_date = datetime.strptime(
                parts[date_index],
                "%d-%b-%Y",
            ).date()
        except ValueError:
            return None

        isin_first = parts[isin_first_index] or None
        isin_second = parts[isin_second_index] or None

        return AMFIService._build_record(
            scheme_code=scheme_code,
            isin_first=isin_first,
            isin_second=isin_second,
            scheme_name=scheme_name,
            nav=nav,
            nav_date=nav_date,
        )

    @staticmethod
    def parse_nav_file(
        text,
        historical=False,
        scheme_codes=None,
    ):
        """
        Parse AMFI NAV data.

        Supports both latest and historical formats. scheme_codes filters
        historical rows before the full-universe file is parsed into records.
        """
        records = []
        normalized_codes = (
            {str(code).strip() for code in scheme_codes}
            if scheme_codes
            else None
        )
        historical_positions = None

        for raw_line in text.splitlines():
            line = raw_line.strip().replace("\ufeff", "")
            if not line or ";" not in line:
                continue

            parts = [part.strip() for part in line.split(";")]

            if historical and parts[0].strip().lower() == "scheme code":
                normalized = [part.strip().lower() for part in parts]
                historical_positions = {
                    "scheme_code": normalized.index("scheme code"),
                    "scheme_name": (
                        normalized.index("scheme name")
                        if "scheme name" in normalized
                        else normalized.index("nav name")
                    ),
                    "nav": normalized.index("net asset value"),
                    "date": normalized.index("date"),
                    "isin_first": (
                        normalized.index("isin div payout/isin growth")
                        if "isin div payout/isin growth" in normalized
                        else 2
                    ),
                    "isin_second": (
                        normalized.index("isin div reinvestment")
                        if "isin div reinvestment" in normalized
                        else 3
                    ),
                }
                continue

            if not parts[0].isdigit():
                continue

            if historical and normalized_codes and parts[0] not in normalized_codes:
                continue

            record = (
                AMFIService._parse_historical_record(
                    parts,
                    positions=historical_positions,
                )
                if historical
                else AMFIService._parse_latest_record(parts)
            )
            if record:
                records.append(record)

        return records

    # Each batch commits as its own short transaction rather than
    # one giant transaction spanning the entire import (which can
    # be 14,000+ scheme/NAV upserts for a full AMFI file). A single
    # multi-minute transaction holds SQLite's write lock the whole
    # time, causing unrelated concurrent requests (login, dashboard
    # reads, user management) to fail with "database is locked"
    # even with a generous busy-timeout configured. Committing in
    # batches bounds the lock-hold time to a fraction of a second
    # per batch, letting other connections interleave, while each
    # batch is still atomic (no partial-batch corruption on error).
    NAV_IMPORT_BATCH_SIZE = 500

    # AMFI's historical report is large and can intermittently return an
    # incomplete/stub response for long ranges. Small chunks make retries
    # reliable while still keeping the importer independent of third-party APIs.
    HISTORICAL_WINDOW_DAYS = 90
    HISTORICAL_MAX_RETRIES = 3

    # Pause between batch transactions so the write lock is
    # actually released for a moment before the next batch's
    # BEGIN. Per-batch atomics alone don't guarantee a waiting
    # connection (e.g. a manual-price PUT from another user) gets
    # a turn - if this loop reacquires the lock immediately, a
    # concurrent writer can lose the race on every retry within
    # its own busy_timeout window. Combined with the bulk_create
    # rewrite of _import_batch below, this keeps lock-hold time
    # per batch to milliseconds instead of seconds.
    NAV_IMPORT_BATCH_PAUSE_SECONDS = 0.1

    @staticmethod
    def _import_records(
        owner,
        records,
    ):
        """
        Import parsed NAV records in short, bounded-size
        transactions (see NAV_IMPORT_BATCH_SIZE) instead of one
        transaction for the whole file.

        Existing schemes are updated.
        Existing NAV records are updated rather
        than duplicated.
        """

        scheme_count = 0
        nav_count = 0

        batch = []

        for record in records:
            batch.append(record)

            if len(batch) >= AMFIService.NAV_IMPORT_BATCH_SIZE:
                batch_schemes, batch_navs = (
                    AMFIService._import_batch(owner, batch)
                )

                scheme_count += batch_schemes
                nav_count += batch_navs

                batch = []

                time.sleep(
                    AMFIService
                    .NAV_IMPORT_BATCH_PAUSE_SECONDS
                )

        if batch:
            batch_schemes, batch_navs = (
                AMFIService._import_batch(owner, batch)
            )

            scheme_count += batch_schemes
            nav_count += batch_navs

        return {
            "schemes": scheme_count,
            "nav_records": nav_count,
        }

    @staticmethod
    @transaction.atomic
    def _import_batch(
        owner,
        records,
    ):
        """
        Import one bounded AMFI batch while respecting both family-level
        uniqueness constraints on MutualFundScheme.

        AMFI can occasionally publish multiple rows that normalize to the
        same scheme_name.  The database intentionally allows only one scheme
        with a given name inside a family, so those rows must resolve to the
        existing family scheme instead of attempting a second insert.

        Normal rows are still handled in bulk.  Only name-collision rows use
        individual updates, keeping the common path fast.
        """

        from users.permissions import require_active_family

        family = require_active_family(owner)

        # Collapse duplicate AMFI rows by scheme code first.  The last row
        # wins, matching the previous sequential import behavior.
        records_by_code = {}
        for record in records:
            records_by_code[record["scheme_code"]] = record

        records = list(records_by_code.values())
        scheme_codes = [record["scheme_code"] for record in records]
        scheme_names = [record["scheme_name"] for record in records]

        existing_schemes = {
            scheme.scheme_code: scheme
            for scheme in (
                MutualFundScheme.objects
                .filter(
                    family=family,
                    scheme_code__in=scheme_codes,
                )
            )
        }
        existing_by_name = {
            scheme.scheme_name: scheme
            for scheme in (
                MutualFundScheme.objects
                .filter(
                    family=family,
                    scheme_name__in=scheme_names,
                )
            )
        }

        # Resolve every incoming record to one canonical AMFI code.  If the
        # same scheme name appears under multiple codes in one feed, retain
        # the first code because the family has a unique scheme_name.
        canonical_input_code = {}
        first_code_by_name = {}

        for record in records:
            code = record["scheme_code"]
            name = record["scheme_name"]

            existing_by_name_match = existing_by_name.get(name)
            existing_by_code = existing_schemes.get(code)

            if existing_by_name_match is not None and existing_by_code is None:
                canonical_input_code[code] = existing_by_name_match.scheme_code
            elif name in first_code_by_name:
                canonical_input_code[code] = first_code_by_name[name]
            else:
                first_code_by_name[name] = code
                canonical_input_code[code] = code

        canonical_records = {}
        for record in records:
            canonical_code = canonical_input_code[record["scheme_code"]]
            if canonical_code not in canonical_records:
                canonical_records[canonical_code] = record

        canonical_records = list(canonical_records.values())

        canonical_codes = [
            record["scheme_code"]
            for record in canonical_records
        ]

        # Bulk path for schemes that do not collide with an existing
        # family/name row.  Records already mapped to an existing
        # family/name scheme are excluded from this upsert.
        bulk_records = [
            record
            for record in canonical_records
            if not (
                existing_by_name.get(record["scheme_name"]) is not None
                and existing_schemes.get(record["scheme_code"]) is None
            )
        ]

        if bulk_records:
            bulk_by_code = {}

            for record in bulk_records:
                existing = existing_schemes.get(
                    record["scheme_code"]
                )

                bulk_by_code[record["scheme_code"]] = (
                    MutualFundScheme(
                        owner=owner,
                        family=family,
                        scheme_code=record["scheme_code"],
                        scheme_name=record["scheme_name"],
                        isin_growth=(
                            record["isin_growth"]
                            or (
                                existing.isin_growth
                                if existing
                                else None
                            )
                        ),
                        isin_dividend=(
                            record["isin_dividend"]
                            or (
                                existing.isin_dividend
                                if existing
                                else None
                            )
                        ),
                    )
                )

            MutualFundScheme.objects.bulk_create(
                list(bulk_by_code.values()),
                update_conflicts=True,
                unique_fields=["family", "scheme_code"],
                update_fields=[
                    "scheme_name",
                    "isin_growth",
                    "isin_dividend",
                ],
            )

        # Re-fetch the canonical scheme rows.  This also gives us the
        # primary key of rows reused because of the family/name uniqueness rule.
        scheme_ids_by_code = dict(
            MutualFundScheme.objects
            .filter(
                family=family,
                scheme_code__in=canonical_codes,
            )
            .values_list(
                "scheme_code",
                "id",
            )
        )

        # Every original AMFI code points to the canonical scheme code that
        # was actually stored for this family.
        original_to_scheme_id = {}
        for original_code, canonical_code in canonical_input_code.items():
            scheme_id = scheme_ids_by_code.get(canonical_code)
            if scheme_id is not None:
                original_to_scheme_id[original_code] = scheme_id

        navs_by_key = {}

        for record in records:
            scheme_id = original_to_scheme_id.get(
                record["scheme_code"]
            )

            if scheme_id is None:
                continue

            nav_key = (scheme_id, record["date"])
            navs_by_key[nav_key] = MutualFundNAV(
                scheme_id=scheme_id,
                date=record["date"],
                source="AMFI",
                nav=record["nav"],
            )

        if navs_by_key:
            MutualFundNAV.objects.bulk_create(
                list(navs_by_key.values()),
                update_conflicts=True,
                unique_fields=["scheme", "date", "source"],
                update_fields=["nav"],
            )

        return len(records), len(records)

    MASTER_SCHEME_BATCH_SIZE = 250
    MASTER_NAV_BATCH_SIZE = 250

    @staticmethod
    @transaction.atomic
    def _import_master_records(records):
        """Upsert AMFI data into the global master tables.

        A latest-NAV download normally contains one row per scheme, while a
        historical download contains many rows per scheme (one per date).
        Keep one canonical scheme row per scheme code, but retain every
        scheme/date NAV observation in the master NAV table.
        """
        if not records:
            return {"schemes": 0, "nav_records": 0}

        schemes_by_code = {}
        nav_records_by_key = {}

        for record in records:
            scheme_code = record["scheme_code"]

            # Keep the most recent metadata row for each scheme while merging
            # any ISIN values that may only appear on one of the rows.
            existing = schemes_by_code.get(scheme_code)
            if existing is None:
                schemes_by_code[scheme_code] = dict(record)
            else:
                if record.get("isin_growth"):
                    existing["isin_growth"] = record["isin_growth"]
                if record.get("isin_dividend"):
                    existing["isin_dividend"] = record["isin_dividend"]
                existing["scheme_name"] = record["scheme_name"]

            if record["date"] is not None:
                nav_records_by_key[(scheme_code, record["date"])] = record

        scheme_records = list(schemes_by_code.values())
        nav_records = list(nav_records_by_key.values())

        schemes = [
            AMFIMasterScheme(
                scheme_code=record["scheme_code"],
                scheme_name=record["scheme_name"],
                isin_growth=record["isin_growth"],
                isin_dividend=record["isin_dividend"],
                is_active=True,
            )
            for record in scheme_records
        ]

        AMFIMasterScheme.objects.bulk_create(
            schemes,
            batch_size=AMFIService.MASTER_SCHEME_BATCH_SIZE,
            update_conflicts=True,
            unique_fields=["scheme_code"],
            update_fields=[
                "scheme_name",
                "isin_growth",
                "isin_dividend",
                "is_active",
                "updated_at",
            ],
        )

        scheme_ids = dict(
            AMFIMasterScheme.objects
            .filter(
                scheme_code__in=[
                    record["scheme_code"]
                    for record in scheme_records
                ]
            )
            .values_list("scheme_code", "id")
        )

        navs = [
            AMFIMasterNAV(
                scheme_id=scheme_ids[record["scheme_code"]],
                date=record["date"],
                nav=record["nav"],
                source="AMFI",
            )
            for record in nav_records
            if record["scheme_code"] in scheme_ids
        ]

        if navs:
            AMFIMasterNAV.objects.bulk_create(
                navs,
                batch_size=AMFIService.MASTER_NAV_BATCH_SIZE,
                update_conflicts=True,
                unique_fields=["scheme", "date", "source"],
                update_fields=["nav"],
            )

        return {
            "schemes": len(scheme_records),
            "nav_records": len(navs),
        }

    @staticmethod
    def import_latest_master_navs():
        """Download AMFI once and refresh the global master dataset."""
        text = AMFIService.download_latest_nav()
        records = AMFIService.parse_nav_file(text, historical=False)
        return AMFIService._import_master_records(records)

    @staticmethod
    def import_historical_master_navs(
        from_date,
        to_date,
        scheme_codes=None,
    ):
        """Import historical AMFI NAVs into the global master dataset.

        ``scheme_codes`` is an optional write-side filter. AMFI's historical
        endpoint returns the full universe for the requested date range, but
        callers such as the benchmark chart only need one scheme. Filtering
        before the database upsert keeps the shared master authoritative
        without duplicating unrelated historical rows.
        """
        if scheme_codes:
            records = AMFIService.download_historical_nav(
                from_date,
                to_date,
                scheme_codes=scheme_codes,
            )
            return AMFIService._import_master_records(records)

        downloaded = AMFIService.download_historical_nav(from_date, to_date)
        # Long all-scheme ranges are internally chunked by
        # download_historical_nav(), so that path returns already-parsed
        # records. A single-window request still returns the raw report text.
        records = (
            downloaded
            if isinstance(downloaded, list)
            else AMFIService.parse_nav_file(
                downloaded,
                historical=True,
                scheme_codes=scheme_codes,
            )
        )
        return AMFIService._import_master_records(records)

    @staticmethod
    def sync_user_nav_from_master(owner):
        """Materialize the latest shared master NAVs into one family."""
        from users.permissions import require_active_family

        family = require_active_family(owner)
        master_schemes = list(AMFIMasterScheme.objects.filter(is_active=True))
        if not master_schemes:
            return {"schemes": 0, "nav_records": 0}

        codes = [scheme.scheme_code for scheme in master_schemes]
        family_schemes = [
            MutualFundScheme(
                owner=owner,
                family=family,
                scheme_code=master.scheme_code,
                scheme_name=master.scheme_name,
                isin_growth=master.isin_growth,
                isin_dividend=master.isin_dividend,
            )
            for master in master_schemes
        ]

        MutualFundScheme.objects.bulk_create(
            family_schemes,
            batch_size=AMFIService.MASTER_SCHEME_BATCH_SIZE,
            update_conflicts=True,
            unique_fields=["family", "scheme_code"],
            update_fields=["scheme_name", "isin_growth", "isin_dividend"],
        )

        scheme_ids = dict(
            MutualFundScheme.objects
            .filter(family=family, scheme_code__in=codes)
            .values_list("scheme_code", "id")
        )

        latest_nav_id = (
            AMFIMasterNAV.objects
            .filter(scheme_id=OuterRef("scheme_id"))
            .order_by("-date", "-id")
            .values("id")[:1]
        )
        latest_master_navs = (
            AMFIMasterNAV.objects
            .filter(
                scheme__is_active=True,
                id=Subquery(latest_nav_id),
            )
            .select_related("scheme")
        )

        family_navs = [
            MutualFundNAV(
                scheme_id=scheme_ids[master_nav.scheme.scheme_code],
                date=master_nav.date,
                nav=master_nav.nav,
                source="AMFI",
            )
            for master_nav in latest_master_navs
            if master_nav.scheme.scheme_code in scheme_ids
        ]

        if family_navs:
            MutualFundNAV.objects.bulk_create(
                family_navs,
                batch_size=AMFIService.MASTER_NAV_BATCH_SIZE,
                update_conflicts=True,
                unique_fields=["scheme", "date", "source"],
                update_fields=["nav"],
            )

        return {"schemes": len(master_schemes), "nav_records": len(family_navs)}
    
    @staticmethod
    def sync_owned_navs_from_master(owner):
        """Materialize latest AMFI NAVs only for schemes owned by a family.

        The shared AMFI master is refreshed once globally. This method avoids
        copying the full 14k+ scheme universe into every family and refreshes
        only the family's owned mutual funds.
        """
        from users.permissions import require_active_family
        from mutual_funds.services.holding_engine import MutualFundHoldingEngine

        family = require_active_family(owner)
        schemes = list(
            MutualFundScheme.objects
            .filter(family=family, is_active=True)
            .only(
                "id",
                "scheme_code",
                "scheme_name",
                "isin_growth",
                "isin_dividend",
            )
        )

        if not schemes:
            return {"schemes": 0, "matched": 0, "nav_records": 0}

        isins = {
            isin.strip().upper()
            for scheme in schemes
            for isin in (scheme.isin_growth, scheme.isin_dividend)
            if isin
        }

        if not isins:
            return {
                "schemes": len(schemes),
                "matched": 0,
                "nav_records": 0,
            }

        masters = (
            AMFIMasterScheme.objects
            .filter(
                is_active=True,
            )
            .filter(
                Q(isin_growth__in=isins)
                | Q(isin_dividend__in=isins)
            )
            .only(
                "id",
                "scheme_code",
                "scheme_name",
                "isin_growth",
                "isin_dividend",
            )
        )

        master_by_isin = {}
        for master in masters:
            for isin in (master.isin_growth, master.isin_dividend):
                if isin:
                    master_by_isin.setdefault(isin.strip().upper(), master)

        master_ids = [master.id for master in master_by_isin.values()]
        latest_nav_id = (
            AMFIMasterNAV.objects
            .filter(scheme_id=OuterRef("scheme_id"))
            .order_by("-date", "-id")
            .values("id")[:1]
        )
        latest_navs = (
            AMFIMasterNAV.objects
            .filter(
                scheme_id__in=master_ids,
                id=Subquery(latest_nav_id),
            )
            .only("scheme_id", "date", "nav")
        )
        latest_by_master_id = {
            nav.scheme_id: nav
            for nav in latest_navs
        }

        matched = 0
        nav_records = 0

        for scheme in schemes:
            master = None
            for isin in (scheme.isin_growth, scheme.isin_dividend):
                if isin:
                    master = master_by_isin.get(isin.strip().upper())
                    if master is not None:
                        break

            if master is None:
                continue

            latest_nav = latest_by_master_id.get(master.id)
            if latest_nav is None:
                continue

            changed_fields = []
            if scheme.scheme_code != master.scheme_code:
                scheme.scheme_code = master.scheme_code
                changed_fields.append("scheme_code")

            if not scheme.isin_growth and master.isin_growth:
                scheme.isin_growth = master.isin_growth
                changed_fields.append("isin_growth")

            if not scheme.isin_dividend and master.isin_dividend:
                scheme.isin_dividend = master.isin_dividend
                changed_fields.append("isin_dividend")

            if changed_fields:
                changed_fields.append("updated_at")
                scheme.save(update_fields=changed_fields)

            MutualFundNAV.objects.update_or_create(
                scheme=scheme,
                date=latest_nav.date,
                source="AMFI",
                defaults={"nav": latest_nav.nav},
            )
            MutualFundHoldingEngine.rebuild_holding(scheme)

            matched += 1
            nav_records += 1

        return {
            "schemes": len(schemes),
            "matched": matched,
            "nav_records": nav_records,
        }

    @staticmethod
    def import_latest_navs(owner):
        """Compatibility wrapper: refresh the shared master, then sync one family."""
        AMFIService.import_latest_master_navs()
        return AMFIService.sync_user_nav_from_master(owner)

    @staticmethod
    def import_historical_navs(owner, from_date, to_date):
        """Compatibility wrapper: import history once into the master, then sync the family."""
        AMFIService.import_historical_master_navs(from_date, to_date)
        return AMFIService.sync_user_nav_from_master(owner)
