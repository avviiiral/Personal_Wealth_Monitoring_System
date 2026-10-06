import logging
from datetime import timedelta

from django.utils import timezone
from django.db.models import Min

from mutual_funds.models import AMFIMasterNAV
from mutual_funds.services.amfi import AMFIService
from watchlist.models import InvestmentProduct, ProductType
from watchlist.services.benchmark import BenchmarkPerformanceService


logger = logging.getLogger(__name__)


class WatchListAMFIHistoryService:
    """Prepare shared AMFI history when a Mutual Fund enters the Watch List."""

    @classmethod
    def prepare_product(cls, product, days=None):
        if not product or product.product_type != ProductType.MUTUAL_FUND:
            return {"prepared": False, "reason": "not_mutual_fund"}

        mutual_fund = getattr(product, "mutual_fund", None)
        scheme_code = str(
            getattr(mutual_fund, "scheme_code", None)
            or getattr(product, "external_identifier", None)
            or ""
        ).strip()
        if not scheme_code:
            logger.warning(
                "Watch List AMFI history skipped: product=%s has no scheme code",
                getattr(product, "id", None),
            )
            return {"prepared": False, "reason": "missing_scheme_code"}

        today = timezone.now().date()
        requested_days = days or BenchmarkPerformanceService.PERIOD_DAYS["5Y"]
        start = today - timedelta(days=requested_days + 31)
        inception_date = getattr(mutual_fund, "inception_date", None)
        if inception_date:
            start = max(start, inception_date)

        # Do not repeatedly download five years of history when the shared
        # master already covers the complete chart range for this scheme.
        coverage = AMFIMasterNAV.objects.filter(
            scheme__scheme_code=scheme_code,
            date__gte=start,
            date__lte=today,
            source="AMFI",
        ).aggregate(
            first_date=Min("date"),
            last_date=Min("date"),
        )
        first_date = coverage["first_date"]
        last_date = (
            AMFIMasterNAV.objects.filter(
                scheme__scheme_code=scheme_code,
                date__gte=start,
                date__lte=today,
                source="AMFI",
            )
            .order_by("-date")
            .values_list("date", flat=True)
            .first()
        )
        row_count = AMFIMasterNAV.objects.filter(
            scheme__scheme_code=scheme_code,
            date__gte=start,
            date__lte=today,
            source="AMFI",
        ).count()
        expected_rows = max(
            BenchmarkPerformanceService.MINIMUM_HISTORY_ROWS,
            int(
                (today - start).days
                * BenchmarkPerformanceService.MINIMUM_DAILY_COVERAGE_RATIO
            ),
        )
        if (
            row_count >= expected_rows
            and first_date is not None
            and last_date is not None
            and first_date <= start + timedelta(days=10)
            and last_date >= today - timedelta(days=10)
        ):
            return {
                "prepared": True,
                "scheme_code": scheme_code,
                "downloaded": False,
                "nav_records": 0,
            }

        result = AMFIService.import_historical_master_navs(
            start,
            today,
            scheme_codes={scheme_code},
        )
        logger.info(
            "Watch List AMFI history prepared: product=%s scheme=%s rows=%s",
            product.id,
            scheme_code,
            result["nav_records"],
        )
        return {
            "prepared": True,
            "scheme_code": scheme_code,
            "downloaded": True,
            "nav_records": result["nav_records"],
        }


def prepare_mutual_fund_watchlist_history(product, days=None):
    """Best-effort wrapper used by Watch List write paths."""
    try:
        return WatchListAMFIHistoryService.prepare_product(product, days=days)
    except Exception:
        logger.exception(
            "Watch List AMFI history preparation failed for product=%s",
            getattr(product, "id", None),
        )
        return {
            "prepared": False,
            "reason": "import_failed",
            "product_id": getattr(product, "id", None),
        }
