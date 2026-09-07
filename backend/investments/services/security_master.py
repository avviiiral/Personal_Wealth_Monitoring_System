from investments.models import (
    Asset,
    AssetCategory,
    SecurityMaster,
)

from investments.services.amc_name_resolver import resolve_amc_name


class SecurityMasterService:
    """
    Creates and retrieves Security Master records.

    ISIN is the primary security identifier whenever
    an ISIN is available.
    """

    @staticmethod
    def _derived_amc_name(asset):
        """
        For a mutual fund Asset, the AMC name can be read straight
        off the scheme name (see amc_name_resolver) - never
        fabricated, never fetched. Returns None for anything that
        isn't a mutual fund, or whose name doesn't match a known
        AMC prefix.
        """

        if asset.category != AssetCategory.MUTUAL_FUND:
            return None

        return resolve_amc_name(asset.name)

    @staticmethod
    def get_or_create(
        owner,
        asset,
    ):
        isin = (
            asset.isin.strip()
            if asset.isin
            else ""
        )

        if isin:
            security = (
                SecurityMaster.objects
                .filter(
                    owner=owner,
                    isin=isin,
                )
                .first()
            )

            if security:

                changed = False

                if security.asset_name != asset.name:
                    security.asset_name = asset.name
                    changed = True

                if not security.amc_name:

                    derived_amc_name = (
                        SecurityMasterService
                        ._derived_amc_name(asset)
                    )

                    if derived_amc_name:
                        security.amc_name = derived_amc_name
                        changed = True

                if changed:
                    security.save(
                        update_fields=[
                            "asset_name",
                            "amc_name",
                            "updated_at",
                        ]
                    )

                return security

            return SecurityMaster.objects.create(
                owner=owner,
                isin=isin,
                asset_name=asset.name,
                amc_name=(
                    SecurityMasterService
                    ._derived_amc_name(asset)
                ),
            )

        security = (
            SecurityMaster.objects
            .filter(
                owner=owner,
                isin__isnull=True,
                asset_name=asset.name,
            )
            .first()
        )

        if security:
            return security

        return SecurityMaster.objects.create(
            owner=owner,
            isin=None,
            asset_name=asset.name,
            amc_name=(
                SecurityMasterService
                ._derived_amc_name(asset)
            ),
        )

    @staticmethod
    def get_for_asset(
        owner,
        asset,
    ):
        isin = (
            asset.isin.strip()
            if asset.isin
            else ""
        )

        if isin:
            return (
                SecurityMaster.objects
                .filter(
                    owner=owner,
                    isin=isin,
                )
                .first()
            )

        return (
            SecurityMaster.objects
            .filter(
                owner=owner,
                isin__isnull=True,
                asset_name=asset.name,
            )
            .first()
        )

    @staticmethod
    def update_classification(
        owner,
        security_id,
        sector=None,
        cap_type=None,
    ):
        security = (
            SecurityMaster.objects
            .filter(
                id=security_id,
                owner=owner,
            )
            .first()
        )

        if security is None:
            raise SecurityMaster.DoesNotExist(
                "Security Master record not found."
            )

        if sector is not None:
            security.sector = str(
                sector
            ).strip()

        if cap_type is not None:
            security.cap_type = str(
                cap_type
            ).strip()

        security.save()

        return security

