"""Indian-market clock awareness without a hard-coded holiday calendar.

This labels the publication timestamp as pre-market, market-hours, post-market,
or weekend. Exchange holidays are intentionally not guessed; deployments can
extend this helper with an official holiday calendar later.
"""

from datetime import time
from django.utils import timezone

from config.pwms_config import get as get_pwms_config


def market_session(timestamp):
    if timestamp is None:
        return "unknown"

    local = timezone.localtime(timestamp)
    if local.weekday() >= 5:
        return "weekend"

    clock = local.time()
    market_open = time.fromisoformat(get_pwms_config("market", "open", "09:15"))
    market_close = time.fromisoformat(get_pwms_config("market", "close", "15:30"))
    if clock < market_open:
        return "pre-market"
    if clock <= market_close:
        return "market-hours"
    return "post-market"
