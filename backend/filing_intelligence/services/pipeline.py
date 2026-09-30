import hashlib
import logging
from datetime import timedelta
from django.contrib.auth.models import User
from django.db.models import Sum
from django.utils import timezone
from investments.models import Holding
from portfolio_news.constants import AlertSourceType, HoldingType, ImpactLevel, Materiality, NewsCategory, NotificationTier, Sentiment, TimeHorizon
from portfolio_news.models import NewsArticle, PortfolioNewsAlert
from portfolio_news.services.alert_scoring import determine_notification_tier, should_send_immediate_notification
from ..models import Filing, FilingProcessingStatus
from ..providers.official import NSEFilingProvider, BSEFilingProvider
from .classifier import classify, SEVERITY_SCORE
from .matching import match_assets, users_for_holding, match_user_watchlists
logger=logging.getLogger(__name__)

def _hash(item): return hashlib.sha256("|".join([item.exchange,item.external_filing_id,item.company_name,item.symbol,item.isin,item.subject,item.details,item.filing_url]).encode()).hexdigest()
def _store_article(filing):
    url=filing.filing_url or filing.source_url
    h=hashlib.sha256(url.encode()).hexdigest()
    article,_=NewsArticle.objects.get_or_create(url_hash=h,defaults={"title":filing.subject[:500],"normalized_title":filing.subject.lower()[:500],"url":url,"source":filing.exchange,"description":filing.details[:5000],"published_at":filing.published_at,"fingerprint":filing.content_hash,"matched_query":"exchange_filing","source_quality":"tier_1","source_count":1})
    return article

def _create_alert(user, holding_type, holding_id, display_name, filing, article, cls, weight=0.0):
    impact=cls.impact
    tier=determine_notification_tier(impact)
    materiality=cls.materiality if cls.materiality in {x.value for x in Materiality} else Materiality.TRIVIAL
    category=cls.category if cls.category in {x.value for x in NewsCategory} else NewsCategory.OTHER
    score=float(SEVERITY_SCORE[cls.severity])
    return PortfolioNewsAlert.objects.get_or_create(user=user,article=article,holding_type=holding_type,holding_id=holding_id,defaults={
        "source_type":AlertSourceType.EXCHANGE_FILING,"filing":filing,"holding_display_name":display_name,"relevant":True,"category":category,"sentiment":Sentiment.NEUTRAL,"time_horizon":TimeHorizon.UNSPECIFIED,"relevance_score":100,"impact":impact,"impact_score":SEVERITY_SCORE[cls.severity],"confidence":1.0,"portfolio_weight_at_alert":weight,"alert_score":score,"notification_tier":tier,"summary":f"The {filing.exchange} filing reports: {filing.subject}.","portfolio_implication":"This filing is associated with a security in your monitored portfolio or watchlist.","reason":cls.reason,"notification_sent":should_send_immediate_notification(tier),"materiality":materiality,"key_facts":" ".join(cls.facts)[:5000],"interpretation":"","uncertainty_notes":"The severity is a deterministic PWMS classification of the filing text; no market outcome is inferred."})

def _process(item,dry_run=False):
    content_hash=_hash(item)
    existing=Filing.objects.filter(exchange=item.exchange,external_filing_id=item.external_filing_id).first() if item.external_filing_id else Filing.objects.filter(exchange=item.exchange,content_hash=content_hash).first()
    if existing:
        return {"duplicate":True,"stored":False,"matched":0,"alerts":0}
    if dry_run:
        cls=classify(item.subject,item.details); logger.info("filing dry-run exchange=%s symbol=%s event=%s severity=%s",item.exchange,item.symbol,cls.event_type,cls.severity)
        return {"duplicate":False,"stored":False,"matched":0,"alerts":0}
    filing=Filing.objects.create(exchange=item.exchange,company_name=item.company_name or item.symbol,symbol=item.symbol,isin=item.isin,bse_code=item.bse_code,filing_type=item.filing_type,subject=item.subject or "Exchange filing",details=item.details,filing_url=item.filing_url or item.source_url,source_url=item.source_url,external_filing_id=item.external_filing_id,published_at=item.published_at,content_hash=content_hash)
    cls=classify(filing.subject,filing.details)
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
        filing.security_id=assets[0].id
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
