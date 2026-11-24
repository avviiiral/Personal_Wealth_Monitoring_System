"""Synchronize selected CharlieAlpha mutual-fund data domains into PWMS."""
from django.core.management.base import BaseCommand, CommandError

from mutual_funds.models import AMFIMasterScheme, MFSchemeIdentityMap
from mutual_funds.services.charliealpha import CharlieAlphaService


class Command(BaseCommand):
    help = (
        "Ingest CharlieAlpha scheme master, NAV, metrics, TER, AUM, holdings and "
        "normalized benchmark history into the configured PWMS database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            choices=("schemes", "nav", "nav-history", "metrics", "ter", "aum", "holdings", "benchmarks", "compare", "overlap", "all"),
            default="all",
            help="Data domain to synchronize (default: all).",
        )
        parser.add_argument(
            "--scheme-code", action="append", dest="scheme_codes", default=[],
            help="AMFI scheme code; repeat to select multiple schemes.",
        )
        parser.add_argument(
            "--category", default="",
            help="CharlieAlpha category slug (for schemes/metrics) or AMFI category filter.",
        )
        parser.add_argument(
            "--compare-code", action="append", dest="compare_codes", default=[],
            help="Scheme code for --domain compare/overlap; supply 2 to 20.",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Optional upper bound for per-scheme calls; 0 means no artificial cap.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Validate and fetch without changing scheme data.")
        parser.add_argument(
            "--skip-holdings-changes", action="store_true",
            help="Do not request the provider's previous-period holdings change summary.",
        )

    def handle(self, *args, **options):
        domain = options["domain"]
        codes = list(dict.fromkeys(str(v).strip() for v in options["scheme_codes"] if str(v).strip()))
        category = options["category"].strip()
        limit = options["limit"]
        if limit < 0:
            raise CommandError("--limit cannot be negative.")
        if codes and limit and len(codes) > limit:
            codes = codes[:limit]

        service = CharlieAlphaService(dry_run=options["dry_run"])
        result = {}

        if domain in {"schemes", "all"}:
            result["schemes"] = service.sync_schemes(category=category)

        if domain in {"nav", "all"}:
            result["nav"] = service.sync_navall()

        if domain == "nav-history":
            if not codes:
                raise CommandError("--domain nav-history requires at least one --scheme-code.")
            result["nav_history"] = service.sync_nav_history(codes)

        if domain == "metrics":
            result["metrics"] = service.sync_metrics(
                scheme_codes=codes or None,
                category_slugs=[category] if category else None,
            )

        if domain == "all":
            result["metrics"] = service.sync_metrics(
                category_slugs=[category] if category else None,
            )

        if domain in {"ter", "aum", "holdings", "all"}:
            selected_codes = codes or self._linked_scheme_codes(category)
            if limit:
                selected_codes = selected_codes[:limit]
            if not selected_codes:
                raise CommandError(
                    "No CharlieAlpha-linked AMFI schemes are available. Run --domain schemes first "
                    "or specify --scheme-code explicitly."
                )
            if domain in {"ter", "all"}:
                result["ter"] = service.sync_ter(selected_codes, limit)
            if domain in {"aum", "all"}:
                result["aum"] = service.sync_aum(selected_codes, limit)
            if domain in {"holdings", "all"}:
                result["holdings"] = service.sync_holdings(
                    selected_codes,
                    include_changes=not options["skip_holdings_changes"],
                    limit=limit or None,
                )

        if domain in {"benchmarks", "all"}:
            result["benchmarks"] = service.sync_benchmarks(limit=limit or None)

        if domain in {"compare", "overlap"}:
            compare_codes = list(dict.fromkeys(
                str(v).strip() for v in (options["compare_codes"] or codes) if str(v).strip()
            ))
            if not 2 <= len(compare_codes) <= 20:
                raise CommandError(
                    "--domain compare/overlap requires 2 to 20 codes via --compare-code "
                    "(or repeat --scheme-code)."
                )
            result[domain] = service.compare(compare_codes, overlap_only=(domain == "overlap"))

        self.stdout.write(self.style.SUCCESS("CharlieAlpha sync finished."))
        self.stdout.write("Domain: " + domain)
        for name, values in result.items():
            self.stdout.write(name + ": " + str(values))
        self.stdout.write(
            "Totals: fetched={fetched} inserted={inserted} updated={updated} skipped={skipped} "
            "failed={failed} unmatched={unmatched}".format(**service.stats)
        )
        if service.errors:
            self.stdout.write(self.style.WARNING("Reported issues (first 100):"))
            for error in service.errors[:100]:
                self.stdout.write(str(error))

    @staticmethod
    def _linked_scheme_codes(category: str = "") -> list[str]:
        queryset = AMFIMasterScheme.objects.filter(
            is_active=True,
            identity_maps__source="CHARLIEALPHA",
            identity_maps__status=MFSchemeIdentityMap.STATUS_ACTIVE,
        )
        if category:
            queryset = queryset.filter(level_3_category__icontains=category)
        return list(queryset.order_by("scheme_code").values_list("scheme_code", flat=True).distinct())
