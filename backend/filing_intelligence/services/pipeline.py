import hashlib
import logging
from django.conf import settings
from datetime import timedelta
from django.contrib.auth.models import User
from django.db.models import Sum
from django.utils import timezone
from investments.models import Holding
from portfolio_news.constants import AlertSourceType, HoldingType, ImpactLevel, Materiality, NewsCategory, NotificationTier, Sentiment, TimeHorizon
from portfolio_news.models import NewsArticle, PortfolioNewsAlert
from portfolio_news.services.alert_scoring import compute_alert_score, determine_notification_tier
from portfolio_news.services.web_push import deliver_alert_notification
from portfolio_news.services.article_store import store_article
from portfolio_news.services.news_provider import NewsArticleResult
from portfolio_news.services.market_hours import market_session
from ..models import Filing, FilingProcessingStatus
from ..providers.official import NSEFilingProvider, BSEFilingProvider
from .classifier import classify, FilingClassification, SEVERITY_SCORE
from .material_change import detect_material_change
from .matching import match_assets, users_for_holding, match_user_watchlists
logger=logging.getLogger(__name__)

def _hash(item): return hashlib.sha256("|".join([item.exchange,item.external_filing_id,item.company_name,item.symbol,item.isin,item.subject,item.details,item.filing_url]).encode()).hexdigest()
def _store_article(filing):
    """Store a filing in the shared NewsArticle/source-clustering layer.

    Using the same store as RSS news is what lets an NSE/BSE filing,
    company announcement and independent news reports collapse into one
    underlying event while retaining every supporting source.
    """
    url = filing.filing_url or filing.source_url
    candidate = NewsArticleResult(
        title=filing.subject or f"{filing.company_name} corporate filing",
        url=url,
        source=filing.exchange,
        description=filing.details[:5000],
        published_at=filing.published_at,
        matched_query=(filing.symbol or filing.company_name or "corporate filing")[:255],
    )
    return store_article(candidate)[0]

def _create_alert(user, holding_type, holding_id, display_name, filing, article, cls, weight=0.0):
    impact = cls.impact
    tier = determine_notification_tier(impact)
    confidence = min(0.97 + max(0, article.source_count - 1) * 0.01, 0.99)
    portfolio_relevance = min(
        100.0,
        float(cls.impact == "critical") * 100.0
        + float(cls.impact != "critical") * (float(cls.severity_score if hasattr(cls, "severity_score") else SEVERITY_SCORE[cls.severity]) * max(weight, 1.0) / 100.0),
    )
    session = market_session(filing.published_at)
    materiality = (
        cls.materiality
        if cls.materiality in {x.value for x in Materiality}
        else Materiality.TRIVIAL
    )
    category = (
        cls.category
        if cls.category in {x.value for x in NewsCategory}
        else NewsCategory.OTHER
    )
    score = compute_alert_score(
        impact_score=SEVERITY_SCORE[cls.severity],
        portfolio_weight_percent=weight,
        confidence=confidence,
        source_quality=getattr(article, "source_quality", "tier_1"),
        published_at=article.published_at,
    )

    alert, created = PortfolioNewsAlert.objects.get_or_create(
        user=user,
        article=article,
        holding_type=holding_type,
        holding_id=holding_id,
        defaults={
            "source_type": AlertSourceType.CORPORATE_FILING,
            "filing": filing,
            "holding_display_name": display_name,
            "relevant": True,
            "category": category,
            "sentiment": Sentiment.NEUTRAL,
            "time_horizon": TimeHorizon.UNSPECIFIED,
            "relevance_score": 100,
            "impact": impact,
            "impact_score": SEVERITY_SCORE[cls.severity],
            "confidence": confidence,
            "portfolio_weight_at_alert": weight,
            "alert_score": score,
            "notification_tier": tier,
            "summary": f"The {filing.exchange} filing reports: {filing.subject}.",
            "portfolio_implication": (
                "This filing is associated with a security in your monitored "
                "portfolio or watchlist."
            ),
            "reason": f"{cls.reason} Timing: {session}. Portfolio relevance signal: {portfolio_relevance:.1f}/100. Source confidence: {confidence:.2f}.",
            "notification_sent": False,
            "materiality": materiality,
            "key_facts": " ".join(cls.facts)[:5000],
            "interpretation": "",
            "uncertainty_notes": (
                "The severity is a deterministic PWMS classification of the "
                "filing text; no market outcome is inferred. Source count "
                f"currently represents {article.source_count} independent URL(s)."
            ),
        },
    )

    if created:
        if tier in ("critical", "high") and not alert.notification_sent:
            deliver_alert_notification(alert)
    elif filing:
        # A primary corporate filing upgrades an existing news alert for the
        # same clustered event. Supporting sources stay attached to the shared
        # NewsArticle and never generate another notification.
        updates = {
            "source_type": AlertSourceType.CORPORATE_FILING,
            "category": category,
            "impact": impact,
            "impact_score": SEVERITY_SCORE[cls.severity],
            "materiality": materiality,
            "notification_tier": tier,
            "relevance_score": 100,
            "alert_score": score,
            "summary": f"The {filing.exchange} filing reports: {filing.subject}.",
            "key_facts": " ".join(cls.facts)[:5000],
        }
        if confidence > alert.confidence:
            updates["confidence"] = confidence
        if alert.filing_id != filing.id:
            updates["filing"] = filing
        for field, value in updates.items():
            setattr(alert, field, value)
        alert.reason = (
            f"{cls.reason} Timing: {session}. Portfolio relevance signal: "
            f"{portfolio_relevance:.1f}/100. Source confidence: {confidence:.2f}."
        )
        updates["reason"] = alert.reason
        alert.save(update_fields=list(updates.keys()))
        if tier in ("critical", "high") and not alert.notification_sent:
            cooldown_seconds = max(0, int(getattr(settings, "NEWS_NOTIFICATION_COOLDOWN", 86400)))
            alert_age = (timezone.now() - alert.created_at).total_seconds()
            if alert_age <= cooldown_seconds:
                deliver_alert_notification(alert)

    return alert, created

def _process(item,dry_run=False):
    content_hash=_hash(item)
    existing=Filing.objects.filter(exchange=item.exchange,external_filing_id=item.external_filing_id).first() if item.external_filing_id else Filing.objects.filter(exchange=item.exchange,content_hash=content_hash).first()
    if existing:
        return {"duplicate":True,"stored":False,"matched":0,"alerts":0}
    if dry_run:
        cls=classify(item.subject,item.details,item.filing_type); logger.info("filing dry-run exchange=%s symbol=%s event=%s severity=%s",item.exchange,item.symbol,cls.event_type,cls.severity)
        return {"duplicate":False,"stored":False,"matched":0,"alerts":0}
    filing=Filing.objects.create(exchange=item.exchange,company_name=item.company_name or item.symbol,symbol=item.symbol,isin=item.isin,bse_code=item.bse_code,filing_type=item.filing_type,subject=item.subject or "Exchange filing",details=item.details,filing_url=item.filing_url or item.source_url,source_url=item.source_url,external_filing_id=item.external_filing_id,published_at=item.published_at,content_hash=content_hash)
    cls=classify(filing.subject,filing.details,filing.filing_type)
    previous = (
        Filing.objects
        .filter(
            company_name__iexact=filing.company_name,
            event_type=cls.event_type,
        )
        .order_by("-published_at")
        .first()
    )
    material_change = detect_material_change(
        " ".join(filter(None, [previous.subject, previous.details])) if previous else "",
        " ".join(filter(None, [filing.subject, filing.details])),
    )
    if material_change:
        cls = FilingClassification(
            event_type=cls.event_type,
            severity=cls.severity,
            category=cls.category,
            impact=cls.impact,
            materiality=cls.materiality,
            reason=f"{cls.reason} {material_change}.",
            facts=[*cls.facts, f"Material change: {material_change}"],
        )
    filing.event_type,filing.severity,filing.classification_reason,filing.classification_facts=cls.event_type,cls.severity,cls.reason,cls.facts
    filing.save(update_fields=["event_type","severity","classification_reason","classification_facts","updated_at"])
    assets,method=match_assets(filing); article=_store_article(filing); alerts=0; matched=len(assets)
    holdings=list(Holding.objects.filter(asset__in=assets,quantity__gt=0).select_related("owner","family","asset"))
    user_ids={u.id for h in holdings for u in users_for_holding(h)}
    totals={h["owner_id"]: float(h["total"] or 0) for h in Holding.objects.filter(owner_id__in=user_ids,quantity__gt=0).values("owner_id").annotate(total=Sum("current_value"))}
    for holding in holdings:
        users=users_for_holding(holding)
        for user in users:
            total=totals.get(user.id,0.0)
            weight=(float(holding.current_value or 0)/total*100) if total else 0
            _,created=_create_alert(user,HoldingType.EQUITY,holding.asset_id,holding.asset.name,filing,article,cls,weight); alerts+=int(created)
    if assets:
        filing.security = assets[0]
    entries=match_user_watchlists(filing); matched+=len(entries)
    for entry in entries:
        _,created=_create_alert(entry.user,HoldingType.WATCHLIST,entry.product_id,entry.product.name,filing,article,cls); alerts+=int(created)
    filing.processing_status=FilingProcessingStatus.PROCESSED if matched else FilingProcessingStatus.UNMATCHED
    filing.save(update_fields=["security","processing_status","updated_at"])
    logger.info("filing processed exchange=%s symbol=%s method=%s matched=%s alerts=%s",filing.exchange,filing.symbol,method,matched,alerts)
    return {"duplicate":False,"stored":True,"matched":matched,"alerts":alerts}

def ingest_exchange_filings(hours=24,exchanges=None,dry_run=False,providers=None):
    since=timezone.now()-timedelta(hours=max(1,hours)); providers=providers or {"NSE":NSEFilingProvider(),"BSE":BSEFilingProvider()}; exchanges=[x.upper() for x in (exchanges or providers.keys())]
    stats={"filings_fetched":0,"filings_stored":0,"duplicates":0,"matched":0,"alerts_created":0,"provider_failures":0,"unmatched":0}
    logger.info("filing ingestion started hours=%s exchanges=%s dry_run=%s",hours,exchanges,dry_run)
    for exchange in exchanges:
        provider=providers.get(exchange)
        if not provider: continue
        try: items=list(provider.fetch_filings(since))
        except Exception: stats["provider_failures"]+=1; logger.exception("filing provider failed exchange=%s",exchange); continue
        stats["filings_fetched"]+=len(items)
        for item in items:
            try:
                result=_process(item,dry_run); stats["duplicates"]+=int(result["duplicate"]); stats["filings_stored"]+=int(result["stored"]); stats["matched"]+=result["matched"]; stats["alerts_created"]+=result["alerts"]; stats["unmatched"]+=int(result["stored"] and not result["matched"])
            except Exception: logger.exception("filing processing failed exchange=%s symbol=%s",item.exchange,item.symbol)
    logger.info("filing ingestion complete stats=%s",stats); return stats
