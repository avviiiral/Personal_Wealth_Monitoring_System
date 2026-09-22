from django.core.management.base import BaseCommand

from investments.models import AssetUnderlyingHolding
from investments.services.underlying_security_classifier import UnderlyingSecurityClassifier


class Command(BaseCommand):
    help = "Backfill ISIN, sector, and market-cap classification for uploaded underlying holdings."

    def handle(self, *args, **options):
        rows = AssetUnderlyingHolding.objects.select_related("family").all()
        updated = 0
        unresolved = 0

        for row in rows:
            isin, sector, cap_type = UnderlyingSecurityClassifier.resolve_metadata(
                row.stock_name
            )

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
