"""Indian-market clock awareness without a hard-coded holiday calendar.

This labels the publication timestamp as pre-market, market-hours, post-market,
or weekend. Exchange holidays are intentionally not guessed; deployments can
extend this helper with an official holiday calendar later.
"""

from datetime import time
from django.utils import timezone


def market_session(timestamp):
    if timestamp is None:
        return "unknown"

    local = timezone.localtime(timestamp)
    if local.weekday() >= 5:
        return "weekend"

    clock = local.time()
    if clock < time(9, 15):
        return "pre-market"
    if clock <= time(15, 30):
        return "market-hours"
    return "post-market"
