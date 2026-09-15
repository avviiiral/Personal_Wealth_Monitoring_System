from django.core.management.base import BaseCommand

from mutual_funds.services.underlying import MutualFundUnderlyingService


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
                self.stderr.write(self.style.ERROR("Scheme not found or inactive."))
                return
            try:
                result = MutualFundUnderlyingService.fetch_scheme(scheme)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                return
            self.stdout.write(self.style.SUCCESS(str(result)))
            return

        owner_ids = [owner_id] if owner_id else None
        result = MutualFundUnderlyingService.fetch_all_active(owner_ids=owner_ids)
        self.stdout.write(self.style.SUCCESS(str(result)))
