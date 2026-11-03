"""Evidence-first historical identity discovery using official public sources.

The current NSE master is current-state data; it never establishes a historical
validity interval. Historical validity is only supplied by dated official
documents/providers. Unverified candidates remain pending and never resolve.
"""
import csv
import hashlib
import io
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import requests
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from investments.models import Asset, SecurityIdentifierMapping, SecurityIdentityDiscoveryCheckpoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdentifierEvidence:
    asset_id: int
    identifier_type: str
    identifier_value: str
    exchange: str
    valid_from: date | None
    valid_to: date | None
    source: str
    source_reference: str
    verification_status: str = "PENDING"
    confidence: Decimal = Decimal("0.5")
    evidence: dict | None = None
    document_hash: str = ""


class IdentityProvider:
    name = "base"

    def discover(self, asset: Asset, since: date, until: date):
        raise NotImplementedError


class NSESecurityMasterProvider(IdentityProvider):
    """Current NSE equity master CSV; its scope is explicitly current-only."""
    name = "NSE_SECURITY_MASTER"
    URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

    def discover(self, asset, since, until):
        cache_key = "pwms:security-identity:nse-current-master:v1"
        rows = cache.get(cache_key)
        if rows is None:
            response = requests.get(self.URL, timeout=30, headers={
                "User-Agent": "PWMS/1.0 (security identity discovery)",
                "Accept": "text/csv",
            })
            response.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
            if not rows:
                raise ValueError("NSE current equity master returned no rows")
            cache.set(cache_key, rows, 21600)

        asset_isin = str(asset.isin or "").strip().upper()
        if not asset_isin:
            return
        for row in rows:
            isin = str(row.get("ISIN NUMBER") or row.get("ISIN") or "").strip().upper()
            if isin != asset_isin:
                continue
            symbol = str(row.get("SYMBOL") or "").strip().upper()
            name = str(row.get("NAME OF COMPANY") or "").strip()
            yield IdentifierEvidence(asset.pk, "ISIN", isin, "NSE", None, None,
                self.name, self.URL, "VERIFIED", Decimal("0.98"),
                {"coverage": "current-only", "company_name": name, "symbol": symbol})
            if symbol:
                yield IdentifierEvidence(asset.pk, "NSE_SYMBOL", symbol, "NSE", None, None,
                    self.name, self.URL, "VERIFIED", Decimal("0.98"),
                    {"coverage": "current-only", "paired_isin": isin, "company_name": name})


class NSDLIssuerSearchProvider(IdentityProvider):
    """Public NSDL securities-company search landing page, candidate-only.

    The page exposes interactive search; no public bulk historical interface
    was verified. We do not automate hidden/private endpoints.
    """
    name = "NSDL_ISSUER_SEARCH"
    URL = "https://nsdl.com/participant/securities-company-search"

    def discover(self, asset, since, until):
        return
        yield  # pragma: no cover


class CDSLCurrentMasterProvider(IdentityProvider):
    """CDSL publishes a current ISIN master format; access to the full report
    is participant/report-oriented, so the public page is not treated as a
    downloadable historical feed.
    """
    name = "CDSL_CURRENT_MASTER"
    URL = "https://www.cdslindia.com/DP/Harmonization.html"

    def discover(self, asset, since, until):
        return
        yield  # pragma: no cover


class NSEFilingEvidenceProvider(IdentityProvider):
    """Convert existing dated exchange filings into pending observations.

    An announcement's publication date is not asserted as the identifier's
    valid-from/to period; historical attachments need explicit review.
    """
    name = "NSE_FILING_EVIDENCE"

    def discover(self, asset, since, until):
        from filing_intelligence.models import Filing
        symbol = str(asset.symbol or "").strip().upper()
        if symbol.endswith(".NS") or symbol.endswith(".BO"):
            symbol = symbol[:-3]
        rows = Filing.objects.filter(exchange="NSE", published_at__date__gte=since,
                                     published_at__date__lte=until)
        if asset.isin:
            rows = rows.filter(Q(isin__iexact=asset.isin) | Q(symbol__iexact=symbol))
        elif symbol:
            rows = rows.filter(symbol__iexact=symbol)
        for filing in rows.iterator():
            isin = str(filing.isin or "").strip().upper()
            if not isin:
                continue
            reference = filing.filing_url or filing.source_url
            if not reference:
                continue
            yield IdentifierEvidence(asset.pk, "ISIN", isin, "NSE",
                None, None, self.name, reference, "PENDING", Decimal("0.55"),
                {"filing_id": filing.pk, "published_at": filing.published_at.isoformat(),
                 "subject": filing.subject, "note": "requires dated document validity extraction"},
                hashlib.sha256(reference.encode()).hexdigest())


class HistoricalIdentityDiscoveryService:
    PROVIDERS = (NSESecurityMasterProvider(), NSEFilingEvidenceProvider(),
                 NSDLIssuerSearchProvider(), CDSLCurrentMasterProvider())

    @classmethod
    def resolve_identifier(cls, *, identifier_type, value, exchange="", event_date=None):
        value = str(value or "").strip().upper()
        if not value:
            return {"status": "UNRESOLVED", "asset": None}
        query = SecurityIdentifierMapping.objects.filter(
            identifier_type=identifier_type, identifier_value__iexact=value,
            verification_status="VERIFIED")
        if exchange:
            query = query.filter(exchange__iexact=exchange)
        if event_date:
            query = query.filter(Q(valid_from__isnull=True) | Q(valid_from__lte=event_date))
            query = query.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=event_date))
        matches = list(query.select_related("asset"))
        assets = {row.asset_id: row.asset for row in matches}
        if len(assets) == 1:
            return {"status": "MATCHED", "asset": next(iter(assets.values())),
                    "mapping": matches[0], "method": "HISTORICAL_IDENTIFIER"}
        if len(assets) > 1:
            return {"status": "CONFLICT", "asset": None, "candidates": list(assets.values())}
        return {"status": "UNRESOLVED", "asset": None}

    @classmethod
    def persist_evidence(cls, item, *, dry_run=False):
        if not item.source_reference.startswith("https://") or not item.identifier_value:
            return "rejected"
        if item.identifier_type not in {"ISIN", "NSE_SYMBOL", "BSE_SYMBOL", "DEPOSITORY_ISIN"}:
            return "rejected"
        conflict = SecurityIdentifierMapping.objects.filter(
            identifier_type=item.identifier_type, identifier_value__iexact=item.identifier_value,
            exchange=item.exchange or "", verification_status="VERIFIED").exclude(asset_id=item.asset_id)
        if conflict.exists():
            if not dry_run:
                with transaction.atomic():
                    conflict.update(verification_status="CONFLICT")
                    SecurityIdentifierMapping.objects.filter(
                        asset_id=item.asset_id, identifier_type=item.identifier_type,
                        identifier_value__iexact=item.identifier_value,
                        exchange=item.exchange or "").update(verification_status="CONFLICT")
                    SecurityIdentifierMapping.objects.get_or_create(
                        asset_id=item.asset_id, identifier_type=item.identifier_type,
                        identifier_value=item.identifier_value, valid_from=item.valid_from, source=item.source,
                        defaults={"exchange": item.exchange, "valid_to": item.valid_to,
                                  "source_reference": item.source_reference,
                                  "source_document_hash": item.document_hash,
                                  "verification_status": "CONFLICT",
                                  "confidence": item.confidence, "evidence": item.evidence or {}})
            return "conflict"
        if item.verification_status == "VERIFIED":
            overlap = SecurityIdentifierMapping.objects.filter(
                asset_id=item.asset_id, identifier_type=item.identifier_type,
                exchange=item.exchange or "", verification_status="VERIFIED",
            ).exclude(identifier_value__iexact=item.identifier_value)
            if overlap.exists():
                if not dry_run:
                    overlap.update(verification_status="CONFLICT")
                return "conflict"
        if dry_run:
            return "would_create"
        try:
            SecurityIdentifierMapping.objects.get_or_create(
                asset_id=item.asset_id, identifier_type=item.identifier_type,
                identifier_value=item.identifier_value, valid_from=item.valid_from, source=item.source,
                defaults={"exchange": item.exchange, "valid_to": item.valid_to,
                          "source_reference": item.source_reference,
                          "source_document_hash": item.document_hash,
                          "verification_status": item.verification_status,
                          "confidence": item.confidence, "evidence": item.evidence or {}})
            return "created"
        except IntegrityError:
            return "duplicate"

    @classmethod
    def discover_asset(cls, asset, *, since, until, dry_run=False, force_refresh=False, exchange=""):
        stats = {"providers": 0, "created": 0, "would_create": 0, "duplicates": 0,
                 "conflicts": 0, "rejected": 0, "failures": 0, "skipped": 0}
        for provider in cls.PROVIDERS:
            if exchange and exchange not in {"NSE", "NSDL", "CDSL"}:
                continue
            if exchange == "NSE" and provider.name not in {"NSE_SECURITY_MASTER", "NSE_FILING_EVIDENCE"}:
                continue
            if exchange == "NSDL" and provider.name != "NSDL_ISSUER_SEARCH":
                continue
            if exchange == "CDSL" and provider.name != "CDSL_CURRENT_MASTER":
                continue
            stats["providers"] += 1
            scope = f"asset:{asset.pk}"
            checkpoint, _ = SecurityIdentityDiscoveryCheckpoint.objects.get_or_create(
                provider=provider.name, scope=scope)
            if (not force_refresh and checkpoint.last_success_at
                    and timezone.now() - checkpoint.last_success_at < timedelta(hours=24)):
                stats["skipped"] += 1
                continue
            try:
                for evidence in provider.discover(asset, since, until):
                    result = cls.persist_evidence(evidence, dry_run=dry_run)
                    if result == "created": stats["created"] += 1
                    elif result == "would_create": stats["would_create"] += 1
                    elif result == "duplicate": stats["duplicates"] += 1
                    elif result == "conflict": stats["conflicts"] += 1
                    else: stats["rejected"] += 1
                if not dry_run:
                    checkpoint.last_success_at = timezone.now()
                    checkpoint.last_window_start = since
                    checkpoint.last_window_end = until
                    checkpoint.last_error = ""
                    checkpoint.save()
            except Exception as exc:
                stats["failures"] += 1
                logger.exception("security identity provider failed provider=%s asset=%s", provider.name, asset.pk)
                if not dry_run:
                    checkpoint.last_error = str(exc)[:2000]
                    checkpoint.save(update_fields=["last_error", "updated_at"])
        return stats

    @classmethod
    def run(cls, *, since, until, exchange="", asset_id=None, dry_run=False, force_refresh=False):
        assets = Asset.objects.filter(is_active=True).select_related("security_master").order_by("id")
        if asset_id:
            assets = assets.filter(pk=asset_id)
        totals = {"assets": 0, "providers": 0, "created": 0, "would_create": 0,
                  "duplicates": 0, "conflicts": 0, "rejected": 0, "failures": 0, "skipped": 0}
        for asset in assets.iterator():
            totals["assets"] += 1
            result = cls.discover_asset(asset, since=since, until=until, dry_run=dry_run,
                                        force_refresh=force_refresh, exchange=exchange)
            for key, value in result.items():
                totals[key] += value
        logger.info("security identity discovery finished stats=%s dry_run=%s", totals, dry_run)
        return totals
