from pathlib import Path
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from watchlist.services.benchmark import BenchmarkPerformanceService


class Command(BaseCommand):
    help = "Validate and install an actual BSE 500 TRI historical CSV."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="CSV exported from the licensed BSE 500 TRI source.")

    def handle(self, *args, **options):
        source = Path(options["file"]).expanduser().resolve()
        if not source.is_file():
            raise CommandError(f"Source file does not exist: {source}")

        try:
            text = source.read_text(encoding="utf-8-sig")
            points = BenchmarkPerformanceService._load_bse_tri_csv(text)
        except (OSError, UnicodeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        if not points:
            raise CommandError("BSE 500 TRI source contains no data rows.")

        target = Path(getattr(settings, "WATCHLIST_BENCHMARK_BSE500_TRI_FILE", Path(__file__).resolve().parents[2] / "data" / "bse500_tri.csv"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        self.stdout.write(self.style.SUCCESS(f"Installed {len(points)} BSE 500 TRI observations ({points[0]['date']} -> {points[-1]['date']}) at {target}"))