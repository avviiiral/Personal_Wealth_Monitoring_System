from investments.models import (
    Asset,
    AssetCategory,
    SecurityMaster,
)

from investments.services.amc_name_resolver import resolve_amc_name


class SecurityMasterService:
    """
    Creates and retrieves Security Master records within a
    family ownership boundary.

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
        family_group_id=None,
    ):
        """
        Get/create the Security Master record for an asset.

        When family_group_id is supplied, the lookup and creation
        are strictly family-scoped. The owner is retained only as
        the existing audit/uploader field during the ownership
        migration.
        """

        if family_group_id is None:
            raise ValueError(
                "family_group_id is required for Security Master "
                "operations."
            )

        isin = (
            asset.isin.strip()
            if asset.isin
            else ""
        )

        if isin:
            security = (
                SecurityMaster.objects
                .filter(
                    family_group_id=family_group_id,
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
                family_group_id=family_group_id,
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
                family_group_id=family_group_id,
                isin__isnull=True,
                asset_name=asset.name,
            )
            .first()
        )

        if security:
            return security

        return SecurityMaster.objects.create(
            owner=owner,
            family_group_id=family_group_id,
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
        family_group_id=None,
    ):
        """Return the Security Master record for an asset's family."""

        if family_group_id is None:
            raise ValueError(
                "family_group_id is required for Security Master "
                "operations."
            )

        isin = (
            asset.isin.strip()
            if asset.isin
            else ""
        )

        if isin:
            return (
                SecurityMaster.objects
                .filter(
                    family_group_id=family_group_id,
                    isin=isin,
                )
                .first()
            )

        return (
            SecurityMaster.objects
            .filter(
                family_group_id=family_group_id,
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
        family_group_id=None,
    ):
        """Update classification only inside the active family."""

        if family_group_id is None:
            raise ValueError(
                "family_group_id is required for Security Master "
                "operations."
            )

        security = (
            SecurityMaster.objects
            .filter(
                id=security_id,
                family_group_id=family_group_id,
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
