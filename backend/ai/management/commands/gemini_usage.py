from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum, Count
from django.utils import timezone

from ai.models import GeminiUsageLog


class Command(BaseCommand):

    help = (
        "Print a summary of Gemini API token usage recorded in "
        "GeminiUsageLog - both article-analysis calls (from the "
        "portfolio news monitor) and portfolio-chat calls. "
        "Use --days to change the window (default 30)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="How many days back to summarize (default 30).",
        )

    def handle(self, *args, **options):

        days = options["days"]

        cutoff = timezone.now() - timedelta(days=days)

        queryset = GeminiUsageLog.objects.filter(
            created_at__gte=cutoff
        )

        total = queryset.aggregate(
            calls=Count("id"),
            prompt_tokens=Sum("prompt_tokens"),
            output_tokens=Sum("output_tokens"),
            total_tokens=Sum("total_tokens"),
            cached_tokens=Sum("cached_tokens"),
        )

        if not total["calls"]:
            self.stdout.write(
                f"No Gemini usage recorded in the last {days} "
                "day(s)."
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Gemini usage - last {days} day(s)\n"
                f"  Total calls:          {total['calls']}\n"
                f"  Prompt tokens:        {total['prompt_tokens'] or 0}\n"
                f"  Output tokens:        {total['output_tokens'] or 0}\n"
                f"  Cached tokens:        {total['cached_tokens'] or 0}\n"
                f"  Total tokens:         {total['total_tokens'] or 0}"
            )
        )

        self.stdout.write("\nBy endpoint:")

        by_endpoint = (
            queryset
            .values("endpoint")
            .annotate(
                calls=Count("id"),
                total_tokens=Sum("total_tokens"),
            )
            .order_by("-total_tokens")
        )

        for row in by_endpoint:
            self.stdout.write(
                f"  {row['endpoint']:<20} "
                f"{row['calls']:>6} calls   "
                f"{row['total_tokens'] or 0:>10} tokens"
            )
