from django.core.management.base import BaseCommand

from investments.models import AssetUnderlyingHolding, SecurityMaster
from investments.services.underlying_security_classifier import UnderlyingSecurityClassifier


class Command(BaseCommand):
    help = "Backfill ISIN, sector, and market-cap classification for uploaded underlying holdings."

    def handle(self, *args, **options):
        rows = AssetUnderlyingHolding.objects.select_related("family").all()
        updated = 0
        unresolved = 0

        for row in rows:
            isin = (row.isin or "").strip().upper() or None

            if not isin:
                isin = UnderlyingSecurityClassifier.resolve_isin(row.stock_name)

            sector = (row.sector or "").strip() or None
            cap_type = (row.cap_type or "").strip() or None

            if isin:
                master = (
                    SecurityMaster.objects
                    .filter(family=row.family, isin__iexact=isin)
                    .only("sector", "cap_type")
                    .first()
                )
                if master:
                    sector = sector or (master.sector or "").strip() or None
                    cap_type = cap_type or (master.cap_type or "").strip() or None

            if not sector or not cap_type:
                fallback_sector, fallback_cap_type = UnderlyingSecurityClassifier.classify(
                    row.stock_name
                )
                sector = sector or fallback_sector
                cap_type = cap_type or fallback_cap_type

            changed = (
                row.isin != isin
                or row.sector != sector
                or row.cap_type != cap_type
            )

            if changed:
                row.isin = isin
                row.sector = sector
                row.cap_type = cap_type
                row.save(update_fields=["isin", "sector", "cap_type", "updated_at"])
                updated += 1

            if not isin or not sector or not cap_type:
                unresolved += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {rows.count()} underlying rows; updated {updated}; "
                f"still unresolved {unresolved}."
            )
        )
