from django.core.management.base import BaseCommand, CommandError

from mutual_funds.models import AMFIMasterScheme, MutualFundScheme
from mutual_funds.services.amfi import AMFIService


class Command(BaseCommand):
    help = "Resolve an AMFI scheme code to its historical NAV detail ID."

    def add_arguments(self, parser):
        parser.add_argument("scheme_code")

    def handle(self, *args, **options):
        scheme_code = str(options["scheme_code"]).strip()
        if not scheme_code:
            raise CommandError("scheme_code is required.")

        master = (
            AMFIMasterScheme.objects
            .filter(scheme_code=scheme_code)
            .values(
                "scheme_name",
                "isin_growth",
                "isin_dividend",
            )
            .first()
        )
        family = (
            MutualFundScheme.objects
            .filter(scheme_code=scheme_code)
            .values(
                "scheme_name",
                "isin_growth",
                "isin_dividend",
                "plan",
                "option",
            )
            .first()
        )

        self.stdout.write(f"scheme code: {scheme_code}")
        self.stdout.write(
            f"master scheme name: {(master or {}).get('scheme_name') or '-'}"
        )
        self.stdout.write(
            f"master ISIN growth: {(master or {}).get('isin_growth') or '-'}"
        )
        self.stdout.write(
            f"master ISIN dividend: {(master or {}).get('isin_dividend') or '-'}"
        )
        if family:
            self.stdout.write(f"family scheme name: {family.get('scheme_name') or '-'}")
            self.stdout.write(f"family plan: {family.get('plan') or '-'}")
            self.stdout.write(f"family option: {family.get('option') or '-'}")

        try:
            resolved = AMFIService._resolve_nav_ids({scheme_code})[scheme_code]
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"selected NAV ID: {resolved['nav_id']}"
            )
        )
        self.stdout.write(
            f"match method: {resolved.get('match_method') or '-'}"
        )
        self.stdout.write(
            f"mutual fund ID: {resolved.get('mf_id') or '-'}"
        )
