import logging
import re
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from config.pwms_config import section
from corporate_actions.models import CorporateAction, CorporateActionStatus, CorporateActionType
from corporate_actions.services import reconcile_action_for_all_users
from filing_intelligence.models import Filing
from investments.models import Asset, AssetUnderlyingHolding
from mutual_funds.models import MutualFundTransaction
from mutual_funds.underlying_models import MutualFundUnderlying
from users.models import FamilyMembership


logger = logging.getLogger(__name__)
DATE_PATTERN = re.compile(
    r"(?i)\b(ex\s*[- ]?date|record\s*[- ]?date|payment\s*[- ]?date)\s*[:\-]?\s*"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
)


def classify_filing(filing):
    """Conservative keyword classification; ambiguous subjects are not auto-classified."""
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
    matches = [action_type for terms, action_type in rules if any(term in text for term in terms)]
    return matches[0] if len(matches) == 1 else None


def extract_event_dates(filing):
    """Extract explicit labeled dates only; never substitute the announcement timestamp."""
    text = " ".join([filing.subject or "", filing.details or ""])
    result = {}
    for match in DATE_PATTERN.finditer(text):
        label = re.sub(r"[^a-z]", "", match.group(1).lower())
        raw = match.group(2).strip()
        parsed = None
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if parsed:
            if label.startswith("exdate"):
                result["ex_date"] = parsed
            elif label.startswith("recorddate"):
                result["record_date"] = parsed
            elif label.startswith("paymentdate"):
                result["payment_date"] = parsed
    return result


class Command(BaseCommand):
    help = "Match exchange filings to held securities and create reviewable corporate-action events."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cfg = section("corporate_actions")
        if not cfg.get("enabled", True):
            self.stdout.write("Corporate action monitoring is disabled.")
            return

        hours = max(1, options["hours"] or int(cfg.get("lookback_hours", 168)))
        cutoff = timezone.now() - timedelta(hours=hours)
        filings = Filing.objects.filter(
            published_at__gte=cutoff
        ).order_by("-published_at")[:int(cfg.get("max_events_per_run", 500))]

        scanned = created = updated = matched = skipped = 0
        User = get_user_model()

        for filing in filings.iterator():
            scanned += 1
            action_type = classify_filing(filing)
            event_dates = extract_event_dates(filing)
            if not action_type or not filing.isin or "ex_date" not in event_dates:
                skipped += 1
                continue

            # A dividend per-share amount is safe to parse only from an explicitly
            # labeled "dividend of Rs X per share" phrase. Other ratios remain manual.
            text = " ".join([filing.subject or "", filing.details or ""])
            dividend_match = re.search(
                r"(?i)dividend\s+(?:of\s+)?(?:rs\.?|inr|₹)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:per\s+share)?",
                text,
            )
            defaults = {
                "security_name": filing.company_name,
                "isin": filing.isin.upper(),
                "symbol": filing.symbol.upper(),
                "exchange": filing.exchange,
                "ex_date": event_dates["ex_date"],
                "record_date": event_dates.get("record_date"),
                "payment_date": event_dates.get("payment_date"),
                "source": f"{filing.exchange}_FILING",
                "source_reference": filing.filing_url,
                "status": CorporateActionStatus.PENDING,
                "notes": "Detected from exchange filing. Verify event details and eligibility before confirmation.",
                "metadata": {
                    "filing_id": filing.id,
                    "announcement_date": filing.published_at.isoformat(),
                    "filing_subject": filing.subject,
                    "filing_type": filing.filing_type,
                    "event_dates_verified": True,
                },
            }
            if action_type == CorporateActionType.CASH_DIVIDEND and dividend_match:
                defaults["cash_per_unit"] = dividend_match.group(1)

            if options["dry_run"]:
                created += 1
                continue

            direct_assets = Asset.objects.filter(is_active=True, isin__iexact=filing.isin)
            family_ids = set(direct_assets.exclude(family_id__isnull=True).values_list("family_id", flat=True))
            pms_family_ids = set(
                AssetUnderlyingHolding.objects.filter(isin__iexact=filing.isin).values_list("family_id", flat=True)
            )
            mf_scheme_ids = MutualFundUnderlying.objects.filter(isin__iexact=filing.isin).values_list("scheme_id", flat=True)
            mf_owner_ids = MutualFundTransaction.objects.filter(
                scheme_id__in=mf_scheme_ids
            ).exclude(owner__isnull=True).values_list("owner_id", flat=True)
            direct_owner_ids = direct_assets.exclude(owner__isnull=True).values_list("owner_id", flat=True)
            member_ids = FamilyMembership.objects.filter(
                family_group_id__in=family_ids | pms_family_ids
            ).values_list("profile__user_id", flat=True)

            owners = set(direct_owner_ids) | set(mf_owner_ids) | set(member_ids)
            for owner in User.objects.filter(id__in=owners, is_active=True):
                with transaction.atomic():
                    action, was_created = CorporateAction.objects.update_or_create(
                        owner=owner,
                        isin=filing.isin.upper(),
                        action_type=action_type,
                        ex_date=event_dates["ex_date"],
                        defaults=defaults,
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                    stats = reconcile_action_for_all_users(action)
                    matched += (
                        stats.get("direct", 0)
                        + stats.get("mutual_fund", 0)
                        + stats.get("pms", 0)
                    )

        self.stdout.write(self.style.SUCCESS(
            f"Corporate action scan: scanned={scanned}, created={created}, updated={updated}, "
            f"exposures={matched}, skipped_unclassified_or_unverified_dates={skipped}, dry_run={options['dry_run']}"
        ))
