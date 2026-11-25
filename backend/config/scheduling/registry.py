"""Builds the job list from pwms_config.json."""
from config.pwms_config import get as get_pwms_config

# Nightly jobs run one after another in this order. Chained jobs follow
# their parent directly.
JOB_NAMES = (
    "market_prices",
    "mf_nav",
    "sip",
    "mf_intelligence",
    "watchlist",
    "security_master",
    "security_metrics",
    "mf_underlying",
    "corporate_actions",
)


def job_config(name):
    jobs = get_pwms_config("scheduler", "jobs", {}) or {}
    return dict(jobs.get(name) or {})


def build_jobs():
    from corporate_actions.jobs import CorporateActionsJob
    from market_data.jobs import MarketPricesJob, SecurityMasterJob, SecurityMetricsJob
    from mf_intelligence.jobs import MFIntelligenceJob
    from mutual_funds.jobs import MutualFundNavJob, MutualFundUnderlyingJob, SipJob
    from watchlist.jobs import WatchListJob

    return [
        MarketPricesJob(),
        MutualFundNavJob(),
        SipJob(),
        MFIntelligenceJob(),
        WatchListJob(),
        SecurityMasterJob(),
        SecurityMetricsJob(),
        MutualFundUnderlyingJob(),
        CorporateActionsJob(),
    ]


def get_job(name):
    for job in build_jobs():
        if job.name == name:
            return job
    raise KeyError(name)
