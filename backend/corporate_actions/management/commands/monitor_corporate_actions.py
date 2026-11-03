import logging
import re
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
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
DATE_VALUE = (
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}[-./\s]+[A-Za-z]{3,9}[-./\s]+\d{4})"
)
DATE_PATTERNS = (
    ("ex_date", re.compile(r"(?i)\b(ex\s*[- ]?date|ex\s*[- ]?bonus\s*(?:basis\s*)?(?:from\s*)?date)\s*[:-]?\s*" + DATE_VALUE)),
    ("ex_date", re.compile(r"(?i)\bex\s*[- ]?bonus\s+basis\s+from\s+" + DATE_VALUE)),
    ("record_date", re.compile(r"(?i)\b(record\s*[- ]?date)\s*[:-]?\s*" + DATE_VALUE)),
    ("payment_date", re.compile(r"(?i)\b(payment\s*[- ]?date)\s*[:-]?\s*" + DATE_VALUE)),
)


def _parse_date(raw):
    """Parse numeric and month-name dates using the separators accepted by DATE_VALUE."""
    value = raw.strip()
    formats = (
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d",
        "%d %b %Y", "%d %B %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    # Exchange filings commonly mix separators and month names, e.g. 04-Jun-2026,
    # 04/Jun/2026, and 04.Jun.2026. Normalize only the date separators, then parse.
    normalized = re.sub(r"[-./\s]+", " ", value)
    for fmt in ("%d %m %Y", "%Y %m %d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


def classify_filing(filing):
    """Return an action type only for a genuine event announcement.

    Routine reports, newspaper copies, allotments and post-event notices are
    lifecycle communications, not new corporate actions.
    """
    subject = (filing.subject or "").strip().lower()
    details = (filing.details or "").strip().lower()
    filing_type = (filing.filing_type or "").strip().lower()
    text = " ".join([filing_type, subject, details])

    lifecycle_markers = (
        "monitoring agency report",
        "allotment of securities",
        "allotment of shares",
        "extinguishment of shares",
        "copy of newspaper publication",
        "newspaper publication",
        "communication sent to shareholders",
        "email communication sent to shareholders",
        "post buy-back",
        "post buyback",
        "completion of buy-back",
        "completion of buyback",
    )
    if any(marker in subject or marker in filing_type for marker in lifecycle_markers):
        return None

    rules = (
        (CorporateActionType.STOCK_SPLIT,
         ("stock split", "sub-division of equity shares", "subdivision of equity shares", "split of equity shares"),
         ("approved", "proposed", "recommend", "subject to", "ratio", "record date", "ex-date", "ex date")),
        (CorporateActionType.BONUS,
         ("bonus issue", "issue of bonus shares", "issuance of bonus shares", "issuance of bonus share",
          "bonus equity shares", "bonus shares"),
         ("approved", "proposed", "recommend", "subject to", "ratio", "proportion", "record date", "ex-date", "ex date")),
        (CorporateActionType.CASH_DIVIDEND,
         ("dividend", "idcw"),
         ("declared", "declaration", "recommended", "recommendation", "interim dividend",
          "final dividend", "dividend per share", "record date for the purpose of dividend", "payment date")),
        (CorporateActionType.RIGHTS_ISSUE,
         ("rights issue", "rights entitlement", "issue of shares on rights basis"),
         ("approve", "approved", "proposed", "offer", "issue price", "record date",
          "rights entitlement", "rights issue committee")),
        (CorporateActionType.MERGER,
         ("amalgamation", "merger"),
         ("scheme", "appointed date", "effective date", "approval", "approved", "order", "tribunal")),
        (CorporateActionType.DEMERGER,
         ("demerger", "spin-off", "spin off"),
         ("scheme", "appointed date", "effective date", "approval", "approved", "record date")),
        (CorporateActionType.BUYBACK,
         ("buyback", "buy-back"),
         ("board approved", "approved", "proposed", "tender offer", "record date", "buyback price", "buy-back price")),
        (CorporateActionType.REVERSE_SPLIT,
         ("consolidation of shares", "reverse stock split", "reverse split"),
         ("approved", "proposed", "ratio", "record date", "ex-date", "ex date")),
    )

    matches = []
    for action_type, keywords, evidence in rules:
        if any(keyword in text for keyword in keywords) and any(marker in text for marker in evidence):
            matches.append(action_type)

    return matches[0] if len(matches) == 1 else None


def extract_event_dates(filing):
    """Extract explicitly labelled event dates; announcement timestamps are never substituted."""
    text = " ".join([filing.subject or "", filing.details or ""])
    result = {}
    for field, pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = _parse_date(match.group(match.lastindex))
            if parsed:
                # Conflicting explicit labels are ambiguous; do not silently pick one.
                if field in result and result[field] != parsed:
                    result.pop(field)
                    break
                result[field] = parsed
    return result


def _parse_cli_date(value, option_name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option_name} must use YYYY-MM-DD.") from exc


class Command(BaseCommand):
    help = "Match exchange filings to held securities and create reviewable corporate-action events."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=None, help="Scan filings published within this many hours.")
        parser.add_argument("--since", type=str, default=None, help="Inclusive historical filing date (YYYY-MM-DD).")
        parser.add_argument("--until", type=str, default=None, help="Inclusive historical filing date (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cfg = section("corporate_actions")
        if not cfg.get("enabled", True):
            self.stdout.write("Corporate action monitoring is disabled.")
            return

        since = _parse_cli_date(options.get("since"), "--since")
        until = _parse_cli_date(options.get("until"), "--until")
        if since and until and since > until:
            raise CommandError("--since cannot be later than --until.")
        if (since or until) and options.get("hours"):
            raise CommandError("Use either --hours or --since/--until, not both.")

        queryset = Filing.objects.all()
        if since or until:
            if since:
                start = timezone.make_aware(datetime.combine(since, time.min))
                queryset = queryset.filter(published_at__gte=start)
            if until:
                end = timezone.make_aware(datetime.combine(until + timedelta(days=1), time.min))
                queryset = queryset.filter(published_at__lt=end)
        else:
            hours = max(1, options["hours"] or int(cfg.get("lookback_hours", 168)))
            queryset = queryset.filter(published_at__gte=timezone.now() - timedelta(hours=hours))

        filings = queryset.order_by("-published_at")[:int(cfg.get("max_events_per_run", 500))]
        scanned = created = updated = matched = skipped = 0
        User = get_user_model()

        for filing in filings.iterator():
            scanned += 1
            action_type = classify_filing(filing)
            event_dates = extract_event_dates(filing)
            if not action_type or not filing.isin or "ex_date" not in event_dates:
                skipped += 1
                continue

            defaults = {
                "security_name": filing.company_name,
                "isin": filing.isin.upper(),
                "symbol": (filing.symbol or "").upper(),
                "exchange": filing.exchange,
                "ex_date": event_dates["ex_date"],
                "record_date": event_dates.get("record_date"),
                "payment_date": event_dates.get("payment_date"),
                "source": f"{filing.exchange}_FILING",
                "source_reference": filing.filing_url,
                "status": CorporateActionStatus.PENDING,
                "notes": "Detected from exchange filing. Verify event details and entitlement ratio before confirmation.",
                "metadata": {
                    "filing_id": filing.id,
                    "announcement_date": filing.published_at.isoformat(),
                    "filing_subject": filing.subject,
                    "filing_type": filing.filing_type,
                    "event_dates_verified": True,
                },
            }
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
                        owner=owner, isin=filing.isin.upper(), action_type=action_type,
                        ex_date=event_dates["ex_date"], defaults=defaults,
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                    stats = reconcile_action_for_all_users(action)
                    matched += stats.get("direct", 0) + stats.get("mutual_fund", 0) + stats.get("pms", 0)

        self.stdout.write(self.style.SUCCESS(
            f"Corporate action scan: scanned={scanned}, created={created}, updated={updated}, "
            f"exposures={matched}, skipped_unclassified_or_unverified_dates={skipped}, dry_run={options['dry_run']}"
        ))
