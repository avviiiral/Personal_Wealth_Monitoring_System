from django.core.management.base import BaseCommand

from investments.models import Asset, SecurityMaster


class Command(BaseCommand):
    """
    Link every Asset to its matching SecurityMaster row (same owner,
    same ISIN), where one already exists but the FK was never set.

    This is a purely structural fix — it never invents or guesses
    any financial data (AMC, sector, cap type, ratios, etc.). It
    only wires up a relationship between two rows that already
    agree on ISIN and asset name. If SecurityMaster's own fields
    (amc_name, sector, cap_type, pe_ratio, ...) are empty, they stay
    empty after linking — this command does not touch them.

    Dry-run by default: prints what WOULD be linked/skipped and why,
    without writing anything. Pass --apply to actually save.
    """

    help = (
        "Link Assets to their matching SecurityMaster row by ISIN "
        "(same owner). Dry-run by default; pass --apply to write."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually save the links. Without this flag, only prints a report.",
        )

        parser.add_argument(
            "--user-id",
            type=int,
            help="Restrict to a single user's assets. Omit to cover every user.",
        )

    def handle(self, *args, **options):

        apply_changes = options.get("apply", False)
        user_id = options.get("user_id")

        assets = (
            Asset.objects
            .filter(security_master__isnull=True)
            .exclude(isin__isnull=True)
            .exclude(isin__exact="")
        )

        if user_id:
            assets = assets.filter(owner_id=user_id)

        linked = 0
        no_match = []
        no_isin_skipped = 0

        # Assets with no ISIN at all can't be matched this way —
        # counted separately so the report distinguishes "nothing to
        # match against" from "a real mismatch worth investigating".
        no_isin_assets = (
            Asset.objects
            .filter(security_master__isnull=True)
        )

        if user_id:
            no_isin_assets = no_isin_assets.filter(owner_id=user_id)

        no_isin_assets = no_isin_assets.filter(isin__isnull=True) | no_isin_assets.filter(isin__exact="")
        no_isin_skipped = no_isin_assets.count()

        for asset in assets.select_related(None):

            match = (
                SecurityMaster.objects
                .filter(
                    owner_id=asset.owner_id,
                    isin=asset.isin,
                )
                .first()
            )

            if not match:
                no_match.append(asset)
                continue

            self.stdout.write(
                f"  {asset.name!r} (ISIN {asset.isin}) "
                f"-> SecurityMaster #{match.id} ({match.asset_name!r})"
            )

            linked += 1

            if apply_changes:
                asset.security_master = match
                asset.save(update_fields=["security_master"])

        self.stdout.write("")

        if apply_changes:
            self.stdout.write(
                self.style.SUCCESS(f"Linked {linked} asset(s) to their SecurityMaster row.")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN — would link {linked} asset(s). Re-run with --apply to save."
                )
            )

        if no_isin_skipped:
            self.stdout.write(
                f"Skipped {no_isin_skipped} asset(s) with no ISIN "
                "(cannot be matched this way — typically unlisted/"
                "private holdings like AIF units)."
            )

        if no_match:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(no_match)} asset(s) have an ISIN but no matching "
                    "SecurityMaster row was found:"
                )
            )

            for asset in no_match:
                self.stdout.write(f"  {asset.name!r} (ISIN {asset.isin})")
