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
            # This feed is current-state data, not historical ISIN evidence.
            # The pre-existing Asset ISIN is the input; only the exchange symbol
            # is a newly discovered identifier from this current master row.
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


class TrentHistoricalSplitDocumentProvider(IdentityProvider):
    """Discover Trent's archived ISIN transition from its official annual report.

    The provider does not hardcode an identifier edge. It parses the issuer's
    archived report and requires the report itself to name the pre- and
    post-subdivision ISIN values. The 2016 annual report is the issuer source.
    """
    name = "TRENT_OFFICIAL_ANNUAL_REPORT"
    URL = "https://docs.trentlimited.com/TrentLimited/Annual%20Reports/2016-17/Sixty%20Fifth%20Annual%20Report%20FY%202016-17.pdf"

    def discover(self, asset, since, until):
        if "TRENT" not in str(asset.name or "").upper():
            return
        response = requests.get(self.URL, timeout=45, headers={
            "User-Agent": "PWMS/1.0 (official issuer report identity evidence)",
            "Accept": "application/pdf",
        })
        response.raise_for_status()
        document = response.content
        if not document.startswith(b"%PDF"):
            raise ValueError("Issuer report response is not a PDF")
        try:
            import pymupdf
            text = "\\n".join(page.get_text() for page in pymupdf.open(stream=document, filetype="pdf"))
        except ImportError:
            import fitz
            text = "\\n".join(page.get_text() for page in fitz.open(stream=document, filetype="pdf"))
        text = re.sub(r"\\s+", " ", text).upper()
        isins = list(dict.fromkeys(re.findall(r"INE[A-Z0-9]{9}[0-9]", text)))
        # Require both explicit ISINs in the same official issuer report.
        # The face-value subdivision confirms that this is the relevant class.
        if len(isins) < 2 or "PRIOR TO SUB-DIVISION" not in text or "PURSUANT TO SUB-DIVISION" not in text:
            raise ValueError("Annual report did not expose an explicit pre/post subdivision ISIN pair")
        old_isin, new_isin = isins[:2]
        if asset.isin and asset.isin.upper() != new_isin:
            return
        # The document describes the transition but does not precisely define
        # both ISIN trading boundaries. Store these as review evidence, pending
        # verification of the exact validity dates from depository data.
        yield IdentifierEvidence(asset.pk, "ISIN", old_isin, "", None, None,
            self.name, self.URL, "PENDING", Decimal("0.85"),
            {"document_sha256": hashlib.sha256(document).hexdigest(),
             "transition_evidence": "annual report labels prior-to and pursuant-to subdivision",
             "resulting_isin_candidate": new_isin, "action": "2016 face-value subdivision"})
        yield IdentifierEvidence(asset.pk, "ISIN", new_isin, "", None, None,
            self.name, self.URL, "PENDING", Decimal("0.85"),
            {"document_sha256": hashlib.sha256(document).hexdigest(),
             "transition_evidence": "annual report labels prior-to and pursuant-to subdivision",
             "previous_isin_candidate": old_isin, "action": "2016 face-value subdivision"})


class NSEFilingEvidenceProvider(IdentityProvider):
    """Attach exchange filings as review evidence without asserting validity."""
    name = "NSE_FILING_EVIDENCE"

    def discover(self, asset, since, until):
        from filing_intelligence.models import Filing
        from filing_intelligence.services.security_discovery import _asset_identity
        identity = _asset_identity(asset)
        symbol = str((identity or {}).get("symbol") or "").strip().upper()
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
            reference = filing.filing_url or filing.source_url
            if not reference:
                continue
            # ISIN-less corporate actions (e.g. face-value splits) are still
            # useful evidence; they stay attached to the filing for review.
            yield IdentifierEvidence(asset.pk, "ISIN", isin or "UNVERIFIED",
                "NSE", None, None, self.name, reference, "PENDING", Decimal("0.20"),
                {"filing_id": filing.pk, "published_at": filing.published_at.isoformat(),
                 "subject": filing.subject, "details": filing.details[:4000],
                 "note": "candidate evidence only; analyst must extract explicit identifier validity"},
                hashlib.sha256(reference.encode()).hexdigest())


class HistoricalIdentityDiscoveryService:
    PROVIDERS = (NSESecurityMasterProvider(), NSEFilingEvidenceProvider(),
                 TrentHistoricalSplitDocumentProvider(), NSDLIssuerSearchProvider(),
                 CDSLCurrentMasterProvider())

    @classmethod
    def discover_from_document(cls, *, asset, identifier_type, identifier_value,
                               exchange, valid_from, valid_to, source,
                               source_reference, document_bytes, evidence=None,
                               dry_run=False):
        """Persist an identifier only when a dated source document explicitly proves it."""
        from datetime import date as date_type
        if not valid_from or not valid_to:
            raise ValueError("Dated source evidence must define both validity boundaries.")
        if isinstance(valid_from, str):
            valid_from = date_type.fromisoformat(valid_from)
        if isinstance(valid_to, str):
            valid_to = date_type.fromisoformat(valid_to)
        if valid_to < valid_from:
            raise ValueError("valid_to must not precede valid_from.")
        if not source_reference.startswith("https://") or not document_bytes:
            raise ValueError("A retrievable official source document is required.")
        digest = hashlib.sha256(document_bytes).hexdigest()
        item = IdentifierEvidence(
            asset_id=asset.pk, identifier_type=identifier_type,
            identifier_value=identifier_value.strip().upper(), exchange=exchange,
            valid_from=valid_from, valid_to=valid_to, source=source,
            source_reference=source_reference, verification_status="VERIFIED",
            confidence=Decimal("0.99"),
            evidence={**(evidence or {}), "document_sha256": digest,
                      "validity_basis": "explicit source document dates"},
            document_hash=digest,
        )
        return cls.persist_evidence(item, dry_run=dry_run)

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
            # An unknown validity boundary cannot prove a historical match.
            query = query.filter(valid_from__isnull=False, valid_from__lte=event_date)
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
        if item.identifier_value == "UNVERIFIED":
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
        # Discovery targets currently represented portfolio assets. An explicit
        # asset-id can be used for a one-off investigation even without trades.
        from investments.models import Transaction
        active_asset_ids = Transaction.objects.values_list("asset_id", flat=True).distinct()
        if asset_id:
            assets = assets.filter(pk=asset_id)
        else:
            assets = assets.filter(pk__in=active_asset_ids)
        totals = {"assets": 0, "providers": 0, "created": 0, "would_create": 0,
                  "duplicates": 0, "conflicts": 0, "rejected": 0, "failures": 0, "skipped": 0}
        for asset in assets.iterator():
            totals["assets"] += 1
            try:
                result = cls.discover_asset(asset, since=since, until=until, dry_run=dry_run,
                                            force_refresh=force_refresh, exchange=exchange)
            except Exception:
                logger.exception("security identity discovery failed asset=%s", asset.pk)
                totals["failures"] += 1
                continue
            for key, value in result.items():
                totals[key] += value
        logger.info("security identity discovery finished stats=%s dry_run=%s", totals, dry_run)
        return totals
