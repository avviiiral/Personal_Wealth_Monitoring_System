from django.core.management.base import BaseCommand

from market_data.services.security_metrics_refresh import refresh_security_metrics


class Command(BaseCommand):
    help = "Refresh stock/ETF P/E, P/B, PEG, sector and cap from Yahoo Finance."

    def handle(self, *args, **options):
        self.stdout.write("Refreshing stock/ETF security metrics...")
        result = refresh_security_metrics()
        self.stdout.write(
            self.style.SUCCESS(
                "Security metrics refresh completed: "
                f"refreshed={result['refreshed']}, "
                f"skipped={result['skipped']}, "
                f"failed={result['failed']}"
            )
        )
