from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.official_discovery_fallback import ProductionMutualFundUnderlyingService


class Command(BaseCommand):
    help = "Fetch the latest official AMFI/AMC mutual-fund portfolio disclosures."

    def add_arguments(self, parser):
        parser.add_argument("--scheme-id", type=int, default=None)
        parser.add_argument("--owner-id", type=int, default=None)

    def handle(self, *args, **options):
        scheme_id = options.get("scheme_id")
        owner_id = options.get("owner_id")

        if scheme_id:
            from mutual_funds.models import MutualFundScheme
            scheme = MutualFundScheme.objects.filter(id=scheme_id, is_active=True).first()
            if scheme is None:
                raise CommandError("Scheme not found or inactive.")
            result = ProductionMutualFundUnderlyingService.fetch_scheme(scheme)
            self.stdout.write(self.style.SUCCESS(str(result)))
            return

        owner_ids = [owner_id] if owner_id else None
        result = ProductionMutualFundUnderlyingService.fetch_all_active(owner_ids=owner_ids)
        if result["failed"]:
            self.stderr.write(self.style.WARNING(str(result)))
        else:
            self.stdout.write(self.style.SUCCESS(str(result)))
