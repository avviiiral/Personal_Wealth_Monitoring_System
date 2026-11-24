from django.core.management.base import BaseCommand, CommandError

from mf_intelligence.constants import PLAN_CHOICES
from mf_intelligence.services.sync import (
    ALL_STEPS,
    STEP_AUM,
    STEP_BENCHMARKS,
    STEP_CAP_LIST,
    STEP_HOLDINGS,
    STEP_NAV,
    SYNC_LOCK,
    MFIntelligenceSync,
)


class Command(BaseCommand):
    help = (
        "Download mutual fund metrics, NAV history, holdings, AUM history and benchmarks "
        "from the CharlieAlpha API (plus the AMFI market-cap list) into the PWMS database."
    )

    def add_arguments(self, parser):
        parser.add_argument("--category", action="append", dest="categories", metavar="SLUG",
                            help="Category slug to sync (repeatable). Default: every category.")
        parser.add_argument("--plan", default="Regular", choices=["Regular", "Direct", "Both"],
                            help="Plan(s) whose NAV / holdings / AUM are fetched. Default: Regular.")
        parser.add_argument("--force", action="store_true", help="Refetch data even if it is still fresh.")
        parser.add_argument("--limit", type=int, default=None, help="Fetch at most N funds (testing aid).")
        parser.add_argument("--workers", type=int, default=None, help="Concurrent API fetch workers.")
        parser.add_argument("--skip-nav", action="store_true")
        parser.add_argument("--skip-holdings", action="store_true")
        parser.add_argument("--skip-aum", action="store_true")
        parser.add_argument("--skip-benchmarks", action="store_true")
        parser.add_argument("--skip-cap-list", action="store_true")

    def handle(self, *args, **options):
        plans = list(PLAN_CHOICES) if options["plan"] == "Both" else [options["plan"]]
        skipped = {
            STEP_NAV: options["skip_nav"],
            STEP_HOLDINGS: options["skip_holdings"],
            STEP_AUM: options["skip_aum"],
            STEP_BENCHMARKS: options["skip_benchmarks"],
            STEP_CAP_LIST: options["skip_cap_list"],
        }
        steps = [step for step in ALL_STEPS if not skipped[step]]
        if not SYNC_LOCK.acquire(blocking=False):
            raise CommandError("Another Mutual Fund Intelligence sync is running in this process.")
        try:
            sync = MFIntelligenceSync(workers=options["workers"], progress=lambda text: self.stdout.write(text))
            run = sync.run(
                categories=options["categories"],
                plans=plans,
                force=options["force"],
                steps=steps,
                limit=options["limit"],
                trigger="command",
            )
        finally:
            SYNC_LOCK.release()
        self.stdout.write(self.style.SUCCESS(f"Sync {run.status}: {run.stats}"))
        for error in run.errors[:20]:
            self.stdout.write(self.style.WARNING(error))
        if len(run.errors) > 20:
            self.stdout.write(self.style.WARNING(f"... {len(run.errors) - 20} more errors on sync run #{run.pk}"))
