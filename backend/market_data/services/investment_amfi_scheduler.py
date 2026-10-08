import logging
import threading
import time

from django.contrib.auth.models import User
from django.db import close_old_connections

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 30 * 60


class InvestmentAMFIScheduler:
    """Refresh investment-linked AMFI NAVs and missing history every 30 minutes."""

    _started = False
    _lock = threading.Lock()

    @classmethod
    def start(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            thread = threading.Thread(
                target=cls._run,
                name="investment-amfi-scheduler",
                daemon=True,
            )
            thread.start()
            logger.info(
                "Investment AMFI scheduler started; refresh interval=%s minutes.",
                REFRESH_INTERVAL_SECONDS // 60,
            )

    @classmethod
    def _run_refresh(cls):
        close_old_connections()
        try:
            from mutual_funds.services.investment_amfi import InvestmentAMFIService
            from mutual_funds.services.amfi import AMFIService

            result = InvestmentAMFIService.refresh_for_investments_with_history()

            # Materialize the latest master NAV into each user's family
            # models so existing portfolio/current-value flows see today's NAV.
            for user_id in User.objects.filter(is_active=True).values_list("id", flat=True):
                try:
                    user = User.objects.get(id=user_id)
                    AMFIService.sync_owned_navs_from_master(user)
                except Exception:
                    logger.exception(
                        "Investment AMFI family sync failed for user %s.",
                        user_id,
                    )

            logger.info(
                "Investment AMFI refresh completed: requested_isins=%s "
                "matched_isins=%s schemes=%s nav_records=%s "
                "unmatched=%s history=%s",
                result.get("requested_isins", 0),
                result.get("matched_isins", 0),
                result.get("schemes", 0),
                result.get("nav_records", 0),
                result.get("unmatched_isins", []),
                result.get("history", {}),
            )
        except Exception:
            logger.exception("Investment AMFI 30-minute refresh failed.")
        finally:
            close_old_connections()

    @classmethod
    def _run(cls):
        time.sleep(15)
        while True:
            cls._run_refresh()
            time.sleep(REFRESH_INTERVAL_SECONDS)
