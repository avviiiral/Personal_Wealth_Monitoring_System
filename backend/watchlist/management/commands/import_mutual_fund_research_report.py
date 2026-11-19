import json
import re
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from watchlist.models import MutualFundResearchReport


def extract_js_json(source, variable):
    """Read a JSON object assigned to a JS const/let/var without evaluating JS."""
    match = re.search(r"\b(?:const|let|var)\s+" + re.escape(variable) + r"\s*=\s*", source)
    if not match:
        raise ValueError(f"JavaScript data object {variable} was not found")
    decoder = json.JSONDecoder()
    start = match.end()
    while start < len(source) and source[start].isspace():
        start += 1
    try:
        value, _end = decoder.raw_decode(source, start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{variable} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{variable} must be a JSON object")
    return value


class Command(BaseCommand):
    help = "Import RAW, P2P and ATTR research data from the supplied Nexedge dashboard HTML."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to Nexedge_multicap_dashboard_*.html")
        parser.add_argument("--report-key", default="", help="Optional report key; defaults to category slug")

    def handle(self, *args, **options):
        path = Path(options["file"]).expanduser()
        if not path.is_file():
            raise CommandError(f"HTML file does not exist: {path}")
        source = path.read_text(encoding="utf-8-sig")
        try:
            raw = extract_js_json(source, "RAW")
            p2p = extract_js_json(source, "P2P")
            attr = extract_js_json(source, "ATTR")
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        funds = raw.get("funds")
        if not isinstance(funds, list) or not funds:
            raise CommandError("RAW.funds is missing or empty")
        if not isinstance(p2p.get("funds"), dict) or not isinstance(attr.get("per_fund"), dict):
            raise CommandError("P2P.funds or ATTR.per_fund is missing")

        meta = raw.get("meta") or {}
        raw_date = meta.get("data_date") or meta.get("date")
        try:
            report_date = datetime.strptime(raw_date, "%B %d, %Y").date()
        except (TypeError, ValueError) as exc:
            raise CommandError(f"Unsupported RAW report date: {raw_date!r}") from exc

        category = str(meta.get("category") or "mutual-fund")
        report_key = options["report_key"].strip() or slugify(category) or "mutual-fund"
        payload = {"RAW": raw, "P2P": p2p, "ATTR": attr}
        report, created = MutualFundResearchReport.objects.update_or_create(
            report_key=report_key,
            report_date=report_date,
            defaults={
                "report_cycle": str(meta.get("cycle") or ""),
                "source_file": path.name,
                "payload": payload,
            },
        )
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} report {report.report_key} ({report.report_date}): "
            f"{len(funds)} RAW funds, {p2p.get('n_covered', 0)} P2P-covered, "
            f"{len(attr['per_fund'])} attribution funds."
        ))
