import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from config.pwms_config import section
from corporate_actions.models import CorporateAction, CorporateActionStatus, CorporateActionType
from corporate_actions.services import reconcile_action_for_all_users
from filing_intelligence.models import Filing


logger = logging.getLogger(__name__)


def classify_filing(filing):
    """Conservative keyword classification. It never infers ratios or event dates."""
    text = " ".join([filing.filing_type or "", filing.subject or "", filing.details or ""]).lower()
    rules = [
        (("stock split", "split of equity shares", "sub-division"), CorporateActionType.STOCK_SPLIT),
        (("bonus issue", "issue of bonus shares"), CorporateActionType.BONUS),
        (("dividend", "interim dividend", "final dividend"), CorporateActionType.CASH_DIVIDEND),
        (("rights issue", "rights entitlement"), CorporateActionType.RIGHTS_ISSUE),
        (("amalgamation", "merger"), CorporateActionType.MERGER),
        (("demerger", "scheme of arrangement", "spin-off", "spin off"), CorporateActionType.DEMERGER),
        (("buyback", "buy-back"), CorporateActionType.BUYBACK),
        (("consolidation of shares", "reverse split"), CorporateActionType.REVERSE_SPLIT),
    ]
    matches = [(terms, action_type) for terms, action_type in rules if any(term in text for term in terms)]
    return matches[0][1] if len(matches) == 1 else None


class Command(BaseCommand):
    help = "Match exchange filings to held securities and create reviewable corporate-action events."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cfg = section("corporate_actions")
        hours = max(1, options["hours"] or int(cfg.get("lookback_hours", 168)))
        cutoff = timezone.now() - timedelta(hours=hours)
        filings = Filing.objects.filter(published_at__gte=cutoff).order_by("-published_at")[:int(cfg.get("max_events_per_run", 500))]
        scanned = created = updated = matched = skipped = 0
        for filing in filings.iterator():
            scanned += 1
            action_type = classify_filing(filing)
            if not action_type or not filing.isin:
                skipped += 1
                continue
            # Filing timestamp is announcement date, not ex-date. We deliberately
            # do not synthesize missing ex/record dates from publication time.
            # A missing ex-date remains a provider/manual verification task.
            event_date = filing.published_at.date()
            defaults = {
                "security_name": filing.company_name,
                "isin": filing.isin.upper(),
                "symbol": filing.symbol.upper(),
                "exchange": filing.exchange,
                "ex_date": event_date,
                "source": f"{filing.exchange}_FILING",
                "source_reference": filing.filing_url,
                "status": CorporateActionStatus.PENDING,
                "notes": "Detected from an exchange filing. Announcement date is not assumed to be the ex-date; verify official event dates and terms before confirmation.",
                "metadata": {
                    "filing_id": filing.id,
                    "announcement_date": filing.published_at.isoformat(),
                    "filing_subject": filing.subject,
                    "filing_type": filing.filing_type,
                    "event_dates_verified": False,
                },
            }
            if options["dry_run"]:
                created += 1
                continue
            # This feature's action records are owned by the matched asset owner;
            # resolve exact ISINs from the filing's linked security if available.
            from investments.models import Asset
            asset_qs = Asset.objects.filter(is_active=True, isin__iexact=filing.isin)
            if filing.security_id:
                asset_qs = asset_qs.filter(id=filing.security_id)
            owners = list(asset_qs.exclude(owner__isnull=True).values_list("owner_id", flat=True).distinct())
            for owner_id in owners:
                from django.contrib.auth import get_user_model
                owner = get_user_model().objects.filter(id=owner_id, is_active=True).first()
                if owner is None:
                    continue
                with transaction.atomic():
                    action, was_created = CorporateAction.objects.update_or_create(
                        owner=owner,
                        isin=filing.isin.upper(),
                        action_type=action_type,
                        ex_date=event_date,
                        defaults=defaults,
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                    stats = reconcile_action_for_all_users(action)
                    matched += stats.get("direct", 0) + stats.get("mutual_fund", 0)
        self.stdout.write(self.style.SUCCESS(
            f"Corporate action scan: scanned={scanned}, created={created}, updated={updated}, "
            f"exposures={matched}, skipped_unclassified_or_missing_isin={skipped}, dry_run={options['dry_run']}"
        ))
