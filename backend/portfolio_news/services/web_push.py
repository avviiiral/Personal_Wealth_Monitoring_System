import json
import logging

from django.conf import settings

from ..constants import NotificationTier
from ..models import PortfolioNewsAlert, PushSubscription

logger = logging.getLogger(__name__)


def web_push_is_configured() -> bool:
    return bool(
        getattr(settings, "WEB_PUSH_ENABLED", False)
        and getattr(settings, "WEB_PUSH_VAPID_PUBLIC_KEY", "")
        and getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", "")
    )


def _payload_for_alert(alert: PortfolioNewsAlert) -> dict[str, str | int]:
    return {
        "title": f"{alert.notification_tier.title()} Impact - {alert.holding_display_name}",
        "body": alert.article.title,
        "url": f"/portfolio-news/{alert.pk}",
        "tag": f"pwms-alert-{alert.pk}",
        "alert_id": alert.pk,
    }


def deliver_alert_notification(alert: PortfolioNewsAlert) -> bool:
    """
    Deliver one immediate Web Push notification to all active browser
    subscriptions for the alert owner.

    Returns True only when at least one push service accepted the
    notification. notification_sent is updated only on that success.
    """
    if alert.notification_sent:
        return True

    if not alert.relevant:
        return False

    if alert.notification_tier not in (
        NotificationTier.CRITICAL,
        NotificationTier.HIGH,
    ):
        return False

    if not web_push_is_configured():
        logger.info(
            "Web Push is not configured; alert id=%s remains unsent",
            alert.pk,
        )
        return False

    subscriptions = list(
        PushSubscription.objects.filter(
            user=alert.user,
            enabled=True,
        )
    )

    if not subscriptions:
        logger.info(
            "No active Web Push subscriptions for user_id=%s alert_id=%s",
            alert.user_id,
            alert.pk,
        )
        return False

    from pywebpush import WebPushException, webpush

    payload = json.dumps(
        _payload_for_alert(alert),
        separators=(",", ":"),
    )

    delivered = False

    for subscription in subscriptions:
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh,
                "auth": subscription.auth,
            },
        }

        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": settings.WEB_PUSH_VAPID_SUBJECT,
                },
                ttl=86400,
                timeout=10,
            )
            delivered = True
            logger.info(
                "Delivered Web Push alert_id=%s subscription_id=%s",
                alert.pk,
                subscription.pk,
            )
        except WebPushException as exc:
            status_code = getattr(exc, "status_code", None)
            logger.warning(
                "Web Push delivery failed alert_id=%s subscription_id=%s "
                "status=%s error=%s",
                alert.pk,
                subscription.pk,
                status_code,
                exc,
            )

            if status_code in (404, 410):
                subscription.enabled = False
                subscription.save(update_fields=["enabled", "updated_at"])
        except Exception:
            logger.exception(
                "Unexpected Web Push delivery error alert_id=%s "
                "subscription_id=%s",
                alert.pk,
                subscription.pk,
            )

    if delivered:
        alert.notification_sent = True
        alert.save(update_fields=["notification_sent"])
        return True

    return False
