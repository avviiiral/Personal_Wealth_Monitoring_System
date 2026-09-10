from django.core.management.base import BaseCommand

from users.models import FamilyGroup, FamilyMembership
from investments.models import (
    Asset,
    Transaction,
    Holding,
    PortfolioPosition,
    SecurityMaster,
)
from mutual_funds.models import (
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundHolding,
    SIP,
)


class Command(BaseCommand):
    help = "Read-only verification of family ownership consistency."

    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("       PWMS FAMILY OWNERSHIP VERIFICATION")
        self.stdout.write("=" * 60)
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "READ-ONLY MODE: No database records will be changed."
            )
        )
        self.stdout.write("")

        errors = []

        # ---------------------------------------------------------
        # 1. FAMILY STRUCTURE
        # ---------------------------------------------------------
        self.stdout.write("FAMILY MEMBERSHIP CONSISTENCY")

        memberships = FamilyMembership.objects.select_related(
            "profile__user",
            "family_group",
        ).order_by("profile__user__username", "family_group__id")

        membership_map = {}

        for membership in memberships:
            username = membership.profile.user.username
            family_id = membership.family_group_id

            membership_map.setdefault(username, set()).add(family_id)

            self.stdout.write(
                f"  User='{username}' -> "
                f"Family='{membership.family_group.name}' "
                f"(ID={family_id})"
            )

        self.stdout.write("")

        # ---------------------------------------------------------
        # 2. GENERIC OWNER/FAMILY VALIDATION
        # ---------------------------------------------------------
        def validate_owner_family(
            queryset,
            model_name,
            owner_field="owner_id",
            family_field="family_group_id",
        ):
            local_errors = []

            total = queryset.count()

            for record in queryset.iterator():
                owner_id = getattr(record, owner_field, None)
                family_id = getattr(record, family_field, None)

                if family_id is None:
                    local_errors.append(
                        f"{model_name} ID={record.pk}: "
                        f"family_group is NULL"
                    )
                    continue

                if owner_id is None:
                    local_errors.append(
                        f"{model_name} ID={record.pk}: "
                        f"owner is NULL while family_group={family_id}"
                    )
                    continue

                profile = getattr(
                    record,
                    "_audit_profile",
                    None,
                )

                if profile is None:
                    try:
                        from users.models import UserProfile

                        profile = UserProfile.objects.get(user_id=owner_id)
                    except UserProfile.DoesNotExist:
                        local_errors.append(
                            f"{model_name} ID={record.pk}: "
                            f"owner user ID={owner_id} has no UserProfile"
                        )
                        continue

                member = FamilyMembership.objects.filter(
                    profile=profile,
                    family_group_id=family_id,
                ).exists()

                if not member:
                    local_errors.append(
                        f"{model_name} ID={record.pk}: "
                        f"owner user ID={owner_id} is NOT a member "
                        f"of family ID={family_id}"
                    )

            return total, local_errors

        # ---------------------------------------------------------
        # 3. INVESTMENTS
        # ---------------------------------------------------------
        self.stdout.write("INVESTMENTS")

        investment_models = [
            (Asset, "Asset"),
            (Transaction, "Transaction"),
            (Holding, "Holding"),
            (PortfolioPosition, "PortfolioPosition"),
            (SecurityMaster, "SecurityMaster"),
        ]

        for model, model_name in investment_models:
            total, model_errors = validate_owner_family(
                model.objects.all(),
                model_name,
            )

            errors.extend(model_errors)

            self.stdout.write(
                f"  {model_name:<20} "
                f"Total={total:<6} "
                f"Ownership errors={len(model_errors)}"
            )

        self.stdout.write("")

        # ---------------------------------------------------------
        # 4. ASSET RELATIONSHIP CONSISTENCY
        # ---------------------------------------------------------
        self.stdout.write("INVESTMENT RELATIONSHIP CONSISTENCY")

        # Transaction -> Asset
        transaction_errors = 0

        for transaction in Transaction.objects.select_related(
            "asset"
        ).iterator():
            if (
                transaction.asset_id
                and transaction.family_group_id
                != transaction.asset.family_group_id
            ):
                transaction_errors += 1
                errors.append(
                    f"Transaction ID={transaction.pk}: "
                    f"family_group={transaction.family_group_id} "
                    f"does not match Asset ID={transaction.asset_id} "
                    f"family_group={transaction.asset.family_group_id}"
                )

        self.stdout.write(
            f"  Transaction -> Asset       "
            f"Errors={transaction_errors}"
        )

        # Holding -> Asset
        holding_errors = 0

        for holding in Holding.objects.select_related(
            "asset"
        ).iterator():
            if (
                holding.asset_id
                and holding.family_group_id
                != holding.asset.family_group_id
            ):
                holding_errors += 1
                errors.append(
                    f"Holding ID={holding.pk}: "
                    f"family_group={holding.family_group_id} "
                    f"does not match Asset ID={holding.asset_id} "
                    f"family_group={holding.asset.family_group_id}"
                )

        self.stdout.write(
            f"  Holding -> Asset           "
            f"Errors={holding_errors}"
        )

        # PortfolioPosition -> Asset
        position_errors = 0

        for position in PortfolioPosition.objects.select_related(
            "asset"
        ).iterator():
            if (
                position.asset_id
                and position.family_group_id
                != position.asset.family_group_id
            ):
                position_errors += 1
                errors.append(
                    f"PortfolioPosition ID={position.pk}: "
                    f"family_group={position.family_group_id} "
                    f"does not match Asset ID={position.asset_id} "
                    f"family_group={position.asset.family_group_id}"
                )

        self.stdout.write(
            f"  PortfolioPosition -> Asset "
            f"Errors={position_errors}"
        )

        # SecurityMaster -> Asset
        security_errors = 0

        for security in SecurityMaster.objects.prefetch_related(
            "assets"
        ).iterator(chunk_size=1000):
            asset_families = {
                asset.family_group_id
                for asset in security.assets.all()
                if asset.family_group_id is not None
            }

            if len(asset_families) > 1:
                security_errors += 1
                errors.append(
                    f"SecurityMaster ID={security.pk}: "
                    f"linked to assets belonging to multiple families "
                    f"{sorted(asset_families)}"
                )

            if (
                security.family_group_id is not None
                and asset_families
                and security.family_group_id not in asset_families
            ):
                security_errors += 1
                errors.append(
                    f"SecurityMaster ID={security.pk}: "
                    f"family_group={security.family_group_id} "
                    f"does not match linked Asset families "
                    f"{sorted(asset_families)}"
                )

        self.stdout.write(
            f"  SecurityMaster -> Asset    "
            f"Errors={security_errors}"
        )

        self.stdout.write("")

        # ---------------------------------------------------------
        # 5. MUTUAL FUNDS
        # ---------------------------------------------------------
        self.stdout.write("MUTUAL FUND CONSISTENCY")

        mf_models = [
            (MutualFundScheme, "MutualFundScheme"),
            (MutualFundTransaction, "MutualFundTransaction"),
            (MutualFundHolding, "MutualFundHolding"),
            (SIP, "SIP"),
        ]

        for model, model_name in mf_models:
            total, model_errors = validate_owner_family(
                model.objects.all(),
                model_name,
            )

            errors.extend(model_errors)

            self.stdout.write(
                f"  {model_name:<24} "
                f"Total={total:<6} "
                f"Ownership errors={len(model_errors)}"
            )

        self.stdout.write("")

        # ---------------------------------------------------------
        # 6. MUTUAL FUND RELATIONSHIPS
        # ---------------------------------------------------------
        self.stdout.write("MUTUAL FUND RELATIONSHIP CONSISTENCY")

        mf_transaction_errors = 0

        for transaction in MutualFundTransaction.objects.select_related(
            "scheme"
        ).iterator():
            if (
                transaction.scheme_id
                and transaction.family_group_id
                != transaction.scheme.family_group_id
            ):
                mf_transaction_errors += 1
                errors.append(
                    f"MutualFundTransaction ID={transaction.pk}: "
                    f"family_group={transaction.family_group_id} "
                    f"does not match Scheme ID={transaction.scheme_id} "
                    f"family_group={transaction.scheme.family_group_id}"
                )

        self.stdout.write(
            f"  MutualFundTransaction -> Scheme "
            f"Errors={mf_transaction_errors}"
        )

        mf_holding_errors = 0

        for holding in MutualFundHolding.objects.select_related(
            "scheme"
        ).iterator():
            if (
                holding.scheme_id
                and holding.family_group_id
                != holding.scheme.family_group_id
            ):
                mf_holding_errors += 1
                errors.append(
                    f"MutualFundHolding ID={holding.pk}: "
                    f"family_group={holding.family_group_id} "
                    f"does not match Scheme ID={holding.scheme_id} "
                    f"family_group={holding.scheme.family_group_id}"
                )

        self.stdout.write(
            f"  MutualFundHolding -> Scheme      "
            f"Errors={mf_holding_errors}"
        )

        sip_errors = 0

        for sip in SIP.objects.select_related("scheme").iterator():
            if (
                sip.scheme_id
                and sip.family_group_id
                != sip.scheme.family_group_id
            ):
                sip_errors += 1
                errors.append(
                    f"SIP ID={sip.pk}: "
                    f"family_group={sip.family_group_id} "
                    f"does not match Scheme ID={sip.scheme_id} "
                    f"family_group={sip.scheme.family_group_id}"
                )

        self.stdout.write(
            f"  SIP -> Scheme                    "
            f"Errors={sip_errors}"
        )

        self.stdout.write("")

        # ---------------------------------------------------------
        # 7. FAMILY EXISTENCE CHECK
        # ---------------------------------------------------------
        self.stdout.write("FAMILY EXISTENCE CHECK")

        family_ids = set(
            FamilyGroup.objects.values_list("id", flat=True)
        )

        existence_errors = 0

        family_models = [
            (Asset, "Asset"),
            (Transaction, "Transaction"),
            (Holding, "Holding"),
            (PortfolioPosition, "PortfolioPosition"),
            (SecurityMaster, "SecurityMaster"),
            (MutualFundScheme, "MutualFundScheme"),
            (MutualFundTransaction, "MutualFundTransaction"),
            (MutualFundHolding, "MutualFundHolding"),
            (SIP, "SIP"),
        ]

        for model, model_name in family_models:
            invalid_ids = set(
                model.objects.exclude(
                    family_group_id__isnull=True
                ).values_list(
                    "family_group_id",
                    flat=True,
                )
            ) - family_ids

            if invalid_ids:
                existence_errors += len(invalid_ids)

                for family_id in sorted(invalid_ids):
                    errors.append(
                        f"{model_name}: references non-existent "
                        f"FamilyGroup ID={family_id}"
                    )

            self.stdout.write(
                f"  {model_name:<24} "
                f"Invalid family references={len(invalid_ids)}"
            )

        self.stdout.write("")

        # ---------------------------------------------------------
        # 8. FINAL RESULT
        # ---------------------------------------------------------
        self.stdout.write("=" * 60)

        if errors:
            self.stdout.write(
                self.style.ERROR(
                    f"VERIFICATION FAILED: {len(errors)} issue(s) found."
                )
            )
            self.stdout.write("")
            self.stdout.write("ISSUES FOUND:")

            for index, error in enumerate(errors, start=1):
                self.stdout.write(f"  {index}. {error}")

            self.stdout.write("")
            self.stdout.write(
                "NO DATABASE RECORDS WERE MODIFIED."
            )

            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS(
                "VERIFICATION PASSED: "
                "No family ownership inconsistencies found."
            )
        )

        self.stdout.write("")
        self.stdout.write(
            "All existing family_group assignments are "
            "consistent with the current family memberships "
            "and related financial records."
        )

        self.stdout.write("")
        self.stdout.write(
            "No database records were modified."
        )

        self.stdout.write("=" * 60)