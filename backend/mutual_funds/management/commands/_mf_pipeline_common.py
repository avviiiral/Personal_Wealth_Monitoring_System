"""Shared output for the mf_* pipeline management commands."""
from django.core.management.base import BaseCommand, CommandError

from mutual_funds.master_models import MFSourceRun


class PipelineCommand(BaseCommand):
    def report(self, run):
        line = (
            f"{run.command}: status={run.status} parsed={run.rows_parsed} inserted={run.rows_inserted} "
            f"updated={run.rows_updated} skipped={run.rows_skipped} unmatched={run.rows_unmatched} "
            f"failed={run.rows_failed}"
        )
        reason = (run.metadata or {}).get("skip_reason")
        if reason:
            line += f" skip_reason={reason}"
        style = self.style.SUCCESS if run.status in (MFSourceRun.Status.SUCCESS, MFSourceRun.Status.SKIPPED) else self.style.WARNING
        self.stdout.write(style(line))
        for error in (run.errors or [])[:10]:
            self.stdout.write(f"  error: {error}")
        if run.status == MFSourceRun.Status.FAILED:
            raise CommandError(f"{run.command} failed; see mf_source_runs id={run.id}.")
        return run
