"""Investment-driven AMFI master refresh.

Only mutual-fund schemes that are actually present in the user's investments
are materialized into the shared AMFI master tables. Asset.ISIN is the primary
mapping key; scheme names are never used to guess a holding.
"""

import logging

from django.db.models import Q

from investments.models import Asset, AssetCategory
from mutual_funds.services.amfi import AMFIService

logger = logging.getLogger(__name__)


class InvestmentAMFIService:
    """Resolve and refresh AMFI data only for owned mutual-fund ISINs."""

    @staticmethod
    def _normalize_isin(value):
        return str(value or "").strip().upper()

    @classmethod
    def investment_isins(cls, asset_ids=None):
        queryset = Asset.objects.filter(
            isin__isnull=False,
            is_active=True,
        ).exclude(isin="")

        if asset_ids:
            queryset = queryset.filter(id__in=list(asset_ids))

        queryset = queryset.filter(
            Q(category=AssetCategory.MUTUAL_FUND)
            | Q(transactions__sub_class__icontains="mutual fund")
        ).distinct()

        return {
            cls._normalize_isin(isin)
            for isin in queryset.values_list("isin", flat=True)
            if cls._normalize_isin(isin)
        }

    @classmethod
    def refresh_for_assets(cls, asset_ids):
        """Refresh latest AMFI scheme/NAV data for the supplied MF assets."""
        isins = cls.investment_isins(asset_ids)
        return cls.refresh_for_isins(isins)

    @classmethod
    def refresh_for_all_investments(cls):
        """Refresh latest AMFI scheme/NAV data for all owned MF investments."""
        return cls.refresh_for_isins(cls.investment_isins())

    @classmethod
    def refresh_for_investments_with_history(cls):
        """Refresh latest NAVs, then backfill only missing MIS history."""
        result = cls.refresh_for_all_investments()

        # MISHistoryPrefetch checks first/last stored NAV coverage and only
        # downloads missing historical ranges. If coverage is current, no
        # historical AMFI request is made.
        from portfolio.mis_history_prefetch import MISHistoryPrefetch

        history = MISHistoryPrefetch.run_for_all_families()
        result["history"] = history
        return result

    @classmethod
    def refresh_for_isins(cls, isins):
        requested = {
            cls._normalize_isin(isin)
            for isin in (isins or [])
            if cls._normalize_isin(isin)
        }

        if not requested:
            return {
                "requested_isins": 0,
                "matched_isins": 0,
                "unmatched_isins": [],
                "schemes": 0,
                "nav_records": 0,
            }

        # AMFI's latest NAV download contains scheme code, scheme name,
        # ISIN(s), NAV and date. We download that authoritative feed once,
        # but persist only rows whose ISIN belongs to an investment.
        text = AMFIService.download_latest_nav()
        records = AMFIService.parse_nav_file(text, historical=False)

        matched = []
        matched_isins = set()

        for record in records:
            record_isins = {
                cls._normalize_isin(record.get("isin_growth")),
                cls._normalize_isin(record.get("isin_dividend")),
            } - {""}
            overlap = record_isins & requested
            if overlap:
                matched.append(record)
                matched_isins.update(overlap)

        # A scheme can publish multiple rows for growth/dividend ISINs. Keep
        # all matched rows; AMFIService._import_master_records de-duplicates
        # scheme/date keys safely.
        result = AMFIService._import_master_records(matched)
        unmatched = sorted(requested - matched_isins)

        if unmatched:
            logger.warning(
                "Investment-driven AMFI refresh: no AMFI latest-NAV row found "
                "for ISIN(s): %s",
                ", ".join(unmatched),
            )

        return {
            "requested_isins": len(requested),
            "matched_isins": len(matched_isins),
            "unmatched_isins": unmatched,
            "schemes": result.get("schemes", 0),
            "nav_records": result.get("nav_records", 0),
        }
