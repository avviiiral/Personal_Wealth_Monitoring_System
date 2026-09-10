from collections import defaultdict

from django.core.management.base import BaseCommand

from users.models import (
    FamilyGroup,
    FamilyMembership,
    UserProfile,
)

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
    help = (
        "Read-only audit of existing financial records to determine "
        "whether each record can be mapped unambiguously to exactly "
        "one FamilyGroup."
    )

    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "=================================================="
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "       PWMS FAMILY OWNERSHIP AUDIT"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "=================================================="
            )
        )
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "READ-ONLY MODE: No database records will be changed."
            )
        )
        self.stdout.write("")

        self.audit_family_structure()
        self.audit_investments()
        self.audit_mutual_funds()

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "=================================================="
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Audit completed. No database records were modified."
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "=================================================="
            )
        )

    # ==============================================================
    # FAMILY STRUCTURE
    # ==============================================================

    def audit_family_structure(self):
        families = list(
            FamilyGroup.objects
            .order_by("id")
            .values("id", "name")
        )

        memberships = list(
            FamilyMembership.objects
            .select_related("profile__user", "family_group")
            .order_by(
                "family_group__name",
                "profile__user__username",
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "FAMILY STRUCTURE"
            )
        )

        self.stdout.write(
            f"FamilyGroup count: {len(families)}"
        )

        for family in families:
            self.stdout.write(
                f"  Family ID={family['id']} "
                f"name={family['name']!r}"
            )

        self.stdout.write(
            f"FamilyMembership count: {len(memberships)}"
        )

        for membership in memberships:
            self.stdout.write(
                f"  User={membership.profile.user.username!r} "
                f"-> Family={membership.family_group.name!r} "
                f"(ID={membership.family_group_id})"
            )

        self.stdout.write("")

        profiles = (
            UserProfile.objects
            .select_related("user")
            .prefetch_related("family_groups")
            .order_by("user__username")
        )

        self.stdout.write("User family membership summary:")

        for profile in profiles:
            family_list = list(
                profile.family_groups
                .order_by("id")
                .values_list("name", flat=True)
            )

            self.stdout.write(
                f"  User={profile.user.username!r} "
                f"families={family_list}"
            )

        self.stdout.write("")

    # ==============================================================
    # FAMILY NAME RESOLUTION
    # ==============================================================

    @staticmethod
    def normalize_family_name(value):
        if value is None:
            return ""

        return " ".join(
            str(value)
            .strip()
            .casefold()
            .split()
        )

    @staticmethod
    def family_candidates_from_name(family_name):
        normalized = Command.normalize_family_name(
            family_name
        )

        if not normalized:
            return []

        return list(
            FamilyGroup.objects
            .filter(name__iexact=family_name.strip())
            .order_by("id")
        )

    @staticmethod
    def family_ids_for_user(user_id):
        profile = (
            UserProfile.objects
            .filter(user_id=user_id)
            .first()
        )

        if profile is None:
            return []

        return list(
            profile.family_groups
            .order_by("id")
            .values_list("id", flat=True)
        )

    def resolve_family_names(self, family_names):
        """
        Resolve a set of existing family-name values into FamilyGroup IDs.

        Resolution is deliberately strict:

        - Empty names are unresolved.
        - Exactly one matching FamilyGroup is safe.
        - Zero matches are unresolved.
        - Multiple matches are ambiguous.

        No fallback family is ever selected.
        """

        resolved_ids = set()
        unresolved_names = []
        ambiguous_names = []

        for raw_name in family_names:
            cleaned = (
                str(raw_name).strip()
                if raw_name is not None
                else ""
            )

            if not cleaned:
                continue

            candidates = (
                FamilyGroup.objects
                .filter(name__iexact=cleaned)
                .order_by("id")
            )

            candidate_ids = list(
                candidates.values_list("id", flat=True)
            )

            if len(candidate_ids) == 1:
                resolved_ids.add(candidate_ids[0])

            elif len(candidate_ids) == 0:
                unresolved_names.append(cleaned)

            else:
                ambiguous_names.append(
                    (cleaned, candidate_ids)
                )

        return (
            resolved_ids,
            unresolved_names,
            ambiguous_names,
        )

    # ==============================================================
    # GENERIC REPORTING
    # ==============================================================

    def report_result(
        self,
        label,
        total,
        mapped,
        ambiguous,
        unresolved,
    ):
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(label)
        )

        self.stdout.write(
            f"  Total records     : {total}"
        )

        self.stdout.write(
            f"  Already mapped    : {mapped}"
        )

        self.stdout.write(
            f"  Safely resolvable : {len(unresolved) == 0 and len(ambiguous) == 0}"
        )

        self.stdout.write(
            f"  Ambiguous         : {len(ambiguous)}"
        )

        self.stdout.write(
            f"  Unresolved        : {len(unresolved)}"
        )

    def print_family_resolution_details(
        self,
        ambiguous,
        unresolved,
    ):
        if ambiguous:
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(
                    "  AMBIGUOUS FAMILY MATCHES:"
                )
            )

            for record_id, values in ambiguous:
                self.stdout.write(
                    f"    Record ID={record_id}: {values}"
                )

        if unresolved:
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(
                    "  UNRESOLVED FAMILY NAMES:"
                )
            )

            for record_id, value in unresolved:
                self.stdout.write(
                    f"    Record ID={record_id}: {value!r}"
                )

    # ==============================================================
    # INVESTMENTS
    # ==============================================================

    def audit_investments(self):
        self.audit_assets()
        self.audit_transactions()
        self.audit_holdings()
        self.audit_portfolio_positions()
        self.audit_security_masters()

    def audit_assets(self):
        records = (
            Asset.objects
            .select_related("owner", "family_group")
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for asset in records:
            if asset.family_group_id is not None:
                mapped += 1
                continue

            family_names = set(
                asset.transactions
                .exclude(family_name__isnull=True)
                .exclude(family_name="")
                .values_list(
                    "family_name",
                    flat=True,
                )
            )

            ids, unknown_names, ambiguous_names = (
                self.resolve_family_names(
                    family_names
                )
            )

            if len(ids) == 1 and not unknown_names and not ambiguous_names:
                continue

            if len(ids) > 1:
                ambiguous.append(
                    (
                        asset.id,
                        {
                            "asset": asset.name,
                            "family_ids": sorted(ids),
                            "family_names": sorted(
                                family_names
                            ),
                        },
                    )
                )

            elif unknown_names or ambiguous_names or not ids:
                unresolved.append(
                    (
                        asset.id,
                        {
                            "asset": asset.name,
                            "owner_id": asset.owner_id,
                            "family_names": sorted(
                                family_names
                            ),
                            "unknown_names": unknown_names,
                            "ambiguous_names": ambiguous_names,
                        },
                    )
                )

        self.report_result(
            "INVESTMENTS.Asset",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    def audit_transactions(self):
        records = (
            Transaction.objects
            .select_related(
                "owner",
                "family_group",
                "asset",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            family_name = (
                record.family_name
                or ""
            ).strip()

            if not family_name:
                unresolved.append(
                    (
                        record.id,
                        {
                            "owner_id": record.owner_id,
                            "asset_id": record.asset_id,
                            "reason": "family_name is empty",
                        },
                    )
                )
                continue

            candidates = (
                FamilyGroup.objects
                .filter(name__iexact=family_name)
                .order_by("id")
            )

            candidate_ids = list(
                candidates.values_list("id", flat=True)
            )

            if len(candidate_ids) == 1:
                continue

            if len(candidate_ids) > 1:
                ambiguous.append(
                    (
                        record.id,
                        {
                            "family_name": family_name,
                            "family_ids": candidate_ids,
                        },
                    )
                )
            else:
                unresolved.append(
                    (
                        record.id,
                        {
                            "family_name": family_name,
                            "reason": "no FamilyGroup matches",
                        },
                    )
                )

        self.report_result(
            "INVESTMENTS.Transaction",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    def audit_holdings(self):
        records = (
            Holding.objects
            .select_related(
                "owner",
                "family_group",
                "asset",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            asset_family_id = record.asset.family_group_id

            if asset_family_id is not None:
                continue

            unresolved.append(
                (
                    record.id,
                    {
                        "owner_id": record.owner_id,
                        "asset_id": record.asset_id,
                        "asset_name": record.asset.name,
                        "reason": (
                            "asset has no family_group"
                        ),
                    },
                )
            )

        self.report_result(
            "INVESTMENTS.Holding",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    def audit_portfolio_positions(self):
        records = (
            PortfolioPosition.objects
            .select_related(
                "owner",
                "family_group",
                "asset",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            family_name = (
                record.family_name
                or ""
            ).strip()

            if not family_name:
                unresolved.append(
                    (
                        record.id,
                        {
                            "owner_id": record.owner_id,
                            "asset_id": record.asset_id,
                            "reason": "family_name is empty",
                        },
                    )
                )
                continue

            candidates = (
                FamilyGroup.objects
                .filter(name__iexact=family_name)
                .order_by("id")
            )

            candidate_ids = list(
                candidates.values_list("id", flat=True)
            )

            if len(candidate_ids) == 1:
                continue

            if len(candidate_ids) > 1:
                ambiguous.append(
                    (
                        record.id,
                        {
                            "family_name": family_name,
                            "family_ids": candidate_ids,
                        },
                    )
                )
            else:
                unresolved.append(
                    (
                        record.id,
                        {
                            "family_name": family_name,
                            "reason": "no FamilyGroup matches",
                        },
                    )
                )

        self.report_result(
            "INVESTMENTS.PortfolioPosition",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    def audit_security_masters(self):
        records = (
            SecurityMaster.objects
            .select_related(
                "owner",
                "family_group",
            )
            .prefetch_related(
                "assets",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            asset_family_ids = set(
                asset.family_group_id
                for asset in record.assets.all()
                if asset.family_group_id is not None
            )

            if len(asset_family_ids) == 1:
                continue

            if len(asset_family_ids) > 1:
                ambiguous.append(
                    (
                        record.id,
                        {
                            "family_ids": sorted(
                                asset_family_ids
                            ),
                        },
                    )
                )
                continue

            unresolved.append(
                (
                    record.id,
                    {
                        "owner_id": record.owner_id,
                        "reason": (
                            "no associated Asset has "
                            "a family_group"
                        ),
                    },
                )
            )

        self.report_result(
            "INVESTMENTS.SecurityMaster",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    # ==============================================================
    # MUTUAL FUNDS
    # ==============================================================

    def audit_mutual_funds(self):
        self.audit_mutual_fund_schemes()
        self.audit_mutual_fund_transactions()
        self.audit_mutual_fund_holdings()
        self.audit_sips()

    def audit_mutual_fund_schemes(self):
        records = (
            MutualFundScheme.objects
            .select_related(
                "owner",
                "family_group",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            family_names = set(
                record.transactions
                .exclude(family_name__isnull=True)
                .exclude(family_name="")
                .values_list(
                    "family_name",
                    flat=True,
                )
            )

            ids, unknown_names, ambiguous_names = (
                self.resolve_family_names(
                    family_names
                )
            )

            if len(ids) == 1 and not unknown_names and not ambiguous_names:
                continue

            if len(ids) > 1:
                ambiguous.append(
                    (
                        record.id,
                        {
                            "scheme": record.scheme_name,
                            "family_ids": sorted(ids),
                            "family_names": sorted(
                                family_names
                            ),
                        },
                    )
                )
            else:
                unresolved.append(
                    (
                        record.id,
                        {
                            "scheme": record.scheme_name,
                            "owner_id": record.owner_id,
                            "family_names": sorted(
                                family_names
                            ),
                            "unknown_names": unknown_names,
                            "ambiguous_names": ambiguous_names,
                        },
                    )
                )

        self.report_result(
            "MUTUAL_FUNDS.MutualFundScheme",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    def audit_mutual_fund_transactions(self):
        records = (
            MutualFundTransaction.objects
            .select_related(
                "owner",
                "family_group",
                "scheme",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            family_name = (
                record.family_name
                or ""
            ).strip()

            if not family_name:
                unresolved.append(
                    (
                        record.id,
                        {
                            "owner_id": record.owner_id,
                            "scheme_id": record.scheme_id,
                            "reason": "family_name is empty",
                        },
                    )
                )
                continue

            candidates = (
                FamilyGroup.objects
                .filter(name__iexact=family_name)
                .order_by("id")
            )

            candidate_ids = list(
                candidates.values_list("id", flat=True)
            )

            if len(candidate_ids) == 1:
                continue

            if len(candidate_ids) > 1:
                ambiguous.append(
                    (
                        record.id,
                        {
                            "family_name": family_name,
                            "family_ids": candidate_ids,
                        },
                    )
                )
            else:
                unresolved.append(
                    (
                        record.id,
                        {
                            "family_name": family_name,
                            "reason": "no FamilyGroup matches",
                        },
                    )
                )

        self.report_result(
            "MUTUAL_FUNDS.MutualFundTransaction",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    def audit_mutual_fund_holdings(self):
        records = (
            MutualFundHolding.objects
            .select_related(
                "owner",
                "family_group",
                "scheme",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            scheme_family_id = (
                record.scheme.family_group_id
            )

            if scheme_family_id is not None:
                continue

            unresolved.append(
                (
                    record.id,
                    {
                        "owner_id": record.owner_id,
                        "scheme_id": record.scheme_id,
                        "reason": (
                            "scheme has no family_group"
                        ),
                    },
                )
            )

        self.report_result(
            "MUTUAL_FUNDS.MutualFundHolding",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )

    def audit_sips(self):
        records = (
            SIP.objects
            .select_related(
                "owner",
                "family_group",
                "scheme",
            )
            .order_by("id")
        )

        total = records.count()
        mapped = 0
        ambiguous = []
        unresolved = []

        for record in records:
            if record.family_group_id is not None:
                mapped += 1
                continue

            scheme_family_id = (
                record.scheme.family_group_id
            )

            if scheme_family_id is not None:
                continue

            family_names = set(
                record.scheme.transactions
                .exclude(family_name__isnull=True)
                .exclude(family_name="")
                .values_list(
                    "family_name",
                    flat=True,
                )
            )

            ids, unknown_names, ambiguous_names = (
                self.resolve_family_names(
                    family_names
                )
            )

            if len(ids) == 1 and not unknown_names and not ambiguous_names:
                continue

            if len(ids) > 1:
                ambiguous.append(
                    (
                        record.id,
                        {
                            "scheme_id": record.scheme_id,
                            "family_ids": sorted(ids),
                            "family_names": sorted(
                                family_names
                            ),
                        },
                    )
                )
            else:
                unresolved.append(
                    (
                        record.id,
                        {
                            "scheme_id": record.scheme_id,
                            "owner_id": record.owner_id,
                            "family_names": sorted(
                                family_names
                            ),
                            "unknown_names": unknown_names,
                            "ambiguous_names": ambiguous_names,
                        },
                    )
                )

        self.report_result(
            "MUTUAL_FUNDS.SIP",
            total,
            mapped,
            ambiguous,
            unresolved,
        )

        self.print_family_resolution_details(
            ambiguous,
            unresolved,
        )