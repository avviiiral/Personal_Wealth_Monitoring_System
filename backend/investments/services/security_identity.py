"""Conservative, family-scoped resolution of transaction security identities."""
from investments.models import Asset, AssetCategory, SecurityAlias, SecurityMaster
from market_data.services.security_resolver import SecurityResolver


class SecurityIdentityResolver:
    """Resolve only through verified ISINs, approved aliases, or unique typed names."""

    @staticmethod
    def normalize_name(value):
        return SecurityAlias.normalize(value)

    @staticmethod
    def compatible_categories(asset_class, sub_class):
        asset_class = str(asset_class or "").strip().upper().replace("-", "_")
        sub_class = str(sub_class or "").strip().upper().replace("-", "_")
        if asset_class in {"EQUITY", "STOCK"}:
            return {AssetCategory.STOCK}
        if asset_class in {"MUTUAL_FUND", "MUTUAL FUND"} or sub_class in {"MUTUAL_FUND", "MUTUAL FUND"}:
            return {AssetCategory.MUTUAL_FUND}
        if asset_class == "ETF":
            return {AssetCategory.ETF}
        if asset_class in {"DEBT", "BOND"}:
            return {AssetCategory.BOND}
        if asset_class == "CASH":
            return {AssetCategory.CASH}
        if asset_class == "GOLD":
            return {AssetCategory.GOLD}
        if asset_class in {"REAL ESTATE", "REAL_ESTATE"}:
            return {AssetCategory.REAL_ESTATE}
        if asset_class == "CRYPTO":
            return {AssetCategory.CRYPTO}
        return None

    @staticmethod
    def _unique_assets(assets):
        return list({asset.id: asset for asset in assets}.values())

    @staticmethod
    def _name_conflict(family, asset, input_underlying):
        if not input_underlying:
            return False
        normalized = SecurityIdentityResolver.normalize_name(input_underlying)
        if normalized == SecurityIdentityResolver.normalize_name(asset.name):
            return False
        aliases = SecurityAlias.objects.filter(family=family, asset=asset, is_active=True)
        return not any(
            SecurityIdentityResolver.normalize_name(alias.alias) == normalized
            for alias in aliases
        )

    @classmethod
    def resolve(cls, *, family, asset_name, underlying, isin, asset_class, sub_class):
        input_name = str(underlying or asset_name or "").strip()
        normalized_isin = str(isin or "").strip().upper()
        categories = cls.compatible_categories(asset_class, sub_class)

        if normalized_isin:
            assets = list(
                Asset.objects.filter(family=family, isin__iexact=normalized_isin, is_active=True).order_by("id")
            )
            securities = list(
                SecurityMaster.objects.filter(family=family, isin__iexact=normalized_isin).order_by("id")
            )
            for security in securities:
                assets.extend(security.assets.filter(family=family, is_active=True).order_by("id"))
            assets = cls._unique_assets(assets)
            compatible = [asset for asset in assets if categories is None or asset.category in categories]
            if len(compatible) == 1:
                asset = compatible[0]
                conflict = cls._name_conflict(family, asset, underlying)
                return {
                    "asset": asset, "canonical_name": asset.name, "method": "ISIN",
                    "status": "CONFLICT" if conflict else "MATCHED",
                    "details": {"reason": "Explicit Underlying conflicts with the verified ISIN identity."}
                    if conflict else {"reason": "Matched by exact family-scoped ISIN."},
                }
            if len(compatible) > 1:
                return {
                    "asset": None, "canonical_name": "", "method": "ISIN", "status": "AMBIGUOUS",
                    "details": {"reason": "Multiple family-scoped Assets share this ISIN."},
                }
            if securities and not assets:
                if len(securities) == 1:
                    security = securities[0]
                    conflict = bool(
                        underlying and cls.normalize_name(underlying) != cls.normalize_name(security.asset_name)
                    )
                    return {
                        "asset": None, "canonical_name": security.asset_name, "method": "ISIN",
                        "status": "CONFLICT" if conflict else "MATCHED",
                        "details": {"reason": "Matched by family-scoped Security Master ISIN."},
                    }
            if assets or securities:
                return {
                    "asset": None, "canonical_name": "", "method": "ISIN", "status": "CONFLICT",
                    "details": {"reason": "The verified ISIN exists in this family but its instrument type is incompatible."},
                }

        # Resolve against the canonical Security Master even when this family
        # has not created an Asset record for the security yet.
        # Try the underlying first, then the asset name when it is distinct.
        master_names = []
        for candidate_name in (underlying, asset_name):
            candidate_name = str(candidate_name or "").strip()
            if candidate_name and candidate_name not in master_names:
                master_names.append(candidate_name)
        master_resolution = None
        if normalized_isin:
            master_resolution = SecurityResolver.resolve_security_from_master(
                name=input_name, isin=normalized_isin,
            )
        else:
            for candidate_name in master_names:
                candidate_resolution = SecurityResolver.resolve_security_from_master(
                    name=candidate_name,
                )
                if candidate_resolution["status"] != "NOT_FOUND":
                    master_resolution = candidate_resolution
                    break

        if master_resolution and master_resolution["status"] == "MATCHED":
            if categories is None:
                return {
                    "asset": None, "canonical_name": master_resolution["canonical_name"],
                    "isin": master_resolution["isin"], "method": "ISIN" if normalized_isin else "NORMALIZED_NAME",
                    "status": "UNRESOLVED",
                    "details": {"reason": "Security Master identity found, but instrument type is unknown; automatic asset creation is disabled."},
                }
            return {
                "asset": None, "canonical_name": master_resolution["canonical_name"],
                "isin": master_resolution["isin"], "yahoo_symbol": master_resolution.get("yahoo_symbol", ""),
                "method": "ISIN" if normalized_isin else "NORMALIZED_NAME", "status": "MATCHED",
                "details": {"reason": "Matched to canonical Security Master identity by ISIN." if normalized_isin else "Matched to canonical Security Master name after legal-name and punctuation normalization."},
            }
        if master_resolution and master_resolution["status"] in {"CONFLICT", "AMBIGUOUS"}:
            return {
                "asset": None, "canonical_name": master_resolution.get("canonical_name", ""),
                "isin": master_resolution.get("isin", ""), "method": "ISIN" if normalized_isin else "NORMALIZED_NAME",
                "status": master_resolution["status"],
                "details": {"reason": "Uploaded name and ISIN conflict with the Security Master." if master_resolution["status"] == "CONFLICT" else "Security Master identity is ambiguous; automatic matching was blocked."},
            }

        normalized_name = cls.normalize_name(input_name)
        if not normalized_name:
            return {
                "asset": None, "canonical_name": "", "method": "NONE", "status": "UNRESOLVED",
                "details": {"reason": "No usable security name or ISIN was provided."},
            }

        # Normalize the stored alias text at read time as well, so aliases created
        # before a normalization-rule update remain resolvable.
        aliases = [
            alias for alias in SecurityAlias.objects.filter(
                family=family, is_active=True
            ).select_related("asset").order_by("id")
            if cls.normalize_name(alias.alias) == normalized_name
        ]
        if len(aliases) > 1:
            return {
                "asset": None, "canonical_name": "", "method": "APPROVED_ALIAS", "status": "AMBIGUOUS",
                "details": {"reason": "Multiple active aliases matched."},
            }
        if aliases:
            asset = aliases[0].asset
            if categories is None or (categories and asset.category not in categories):
                return {
                    "asset": None, "canonical_name": asset.name, "method": "APPROVED_ALIAS", "status": "CONFLICT",
                    "details": {"reason": "Alias cannot be auto-matched because instrument type is unknown or incompatible."},
                }
            if normalized_isin and asset.isin and asset.isin.strip().upper() != normalized_isin:
                return {
                    "asset": None, "canonical_name": asset.name, "method": "APPROVED_ALIAS", "status": "CONFLICT",
                    "details": {"reason": "Uploaded ISIN conflicts with the approved alias target."},
                }
            return {
                "asset": asset, "canonical_name": asset.name, "method": "APPROVED_ALIAS", "status": "MATCHED",
                "details": {"reason": "Matched by an explicitly approved alias."},
            }

        if categories is None:
            return {
                "asset": None, "canonical_name": "", "method": "NONE", "status": "UNRESOLVED",
                "details": {"reason": "Instrument type is unknown; name-only matching is disabled."},
            }

        candidates = list(
            Asset.objects.filter(family=family, is_active=True, category__in=categories).order_by("id")
        )
        candidates = [
            asset for asset in candidates
            if cls.normalize_name(asset.name) == normalized_name
            and (not normalized_isin or not asset.isin or asset.isin.strip().upper() == normalized_isin)
        ]
        candidates = cls._unique_assets(candidates)
        if len(candidates) == 1:
            asset = candidates[0]
            return {
                "asset": asset, "canonical_name": asset.name, "method": "NORMALIZED_NAME", "status": "MATCHED",
                "details": {"reason": "Matched by a unique normalized name and compatible instrument type."},
            }
        if len(candidates) > 1:
            return {
                "asset": None, "canonical_name": "", "method": "NORMALIZED_NAME", "status": "AMBIGUOUS",
                "details": {"reason": "More than one compatible family-scoped Asset has this normalized name."},
            }
        return {
            "asset": None, "canonical_name": "", "method": "NONE", "status": "UNRESOLVED",
            "details": {"reason": "No verified identifier, approved alias, or unique compatible name matched."},
        }
