from django.db.models import Q

from mutual_funds.models import AMFIMasterScheme


class AMFIAssetResolver:
    """Resolve legacy portfolio assets against the authoritative AMFI master.

    Asset.category is user/import metadata and is not authoritative for market
    data routing. An asset is AMFI-backed when its ISIN (including a linked
    SecurityMaster ISIN) resolves to an active AMFI master scheme.
    """

    @staticmethod
    def _isins(asset):
        values = {str(getattr(asset, "isin", "") or "").strip().upper()}
        security_master = getattr(asset, "security_master", None)
        if security_master is not None:
            values.add(str(getattr(security_master, "isin", "") or "").strip().upper())
        return {value for value in values if value}

    @classmethod
    def schemes_for_isins(cls, isins):
        normalized = {
            str(value or "").strip().upper()
            for value in isins
            if str(value or "").strip()
        }
        if not normalized:
            return AMFIMasterScheme.objects.none()
        return (
            AMFIMasterScheme.objects
            .filter(is_active=True)
            .filter(
                Q(isin_growth__in=normalized)
                | Q(isin_dividend__in=normalized)
            )
            .order_by("id")
        )

    @classmethod
    def scheme_for_asset(cls, asset):
        """Return the active AMFI scheme identified by the asset's ISIN."""
        return cls.schemes_for_isins(cls._isins(asset)).first()

    @classmethod
    def is_amfi_backed(cls, asset):
        return cls.scheme_for_asset(asset) is not None

    @classmethod
    def amfi_asset_ids(cls, assets_by_id):
        """Return IDs of assets whose ISIN resolves to an AMFI scheme."""
        asset_isins = {}
        all_isins = set()
        for asset_id, asset in assets_by_id.items():
            isins = cls._isins(asset)
            if not isins:
                continue
            asset_isins[asset_id] = isins
            all_isins.update(isins)

        if not all_isins:
            return set()

        schemes = cls.schemes_for_isins(all_isins)
        matched_isins = set()
        for scheme in schemes:
            for value in (scheme.isin_growth, scheme.isin_dividend):
                normalized = str(value or "").strip().upper()
                if normalized:
                    matched_isins.add(normalized)

        return {
            asset_id
            for asset_id, isins in asset_isins.items()
            if isins & matched_isins
        }
