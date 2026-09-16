from django.core.management.base import BaseCommand

from watchlist.services.pms import APMIPMSDiscoveryService
from watchlist.services.universe import AMFIUniverseService


class Command(BaseCommand):
    help = "Refresh the generic Watch List investment universe."

    def handle(self, *args, **options):
        mf = AMFIUniverseService.refresh()
        pms = APMIPMSDiscoveryService.refresh()
        self.stdout.write(self.style.SUCCESS(f"Mutual funds: {mf}"))
        self.stdout.write(self.style.SUCCESS(f"PMS: {pms}"))
