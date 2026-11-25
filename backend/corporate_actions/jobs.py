"""Nightly NSE corporate-action scan (03:30), once per day.

Runs the existing monitor_corporate_actions command. The scan only records
events, exposures, reconciliation state and notifications for review; it
never creates or changes a transaction.
"""
import re
from io import StringIO

from django.core.management import call_command

from config.pwms_config import get as get_pwms_config
from config.scheduling.clock import localize
from config.scheduling.framework import DailySchedule, ScheduledJob

_SUMMARY_PAIR = re.compile(r"(\w+)=(\d+)")


class CorporateActionsJob(ScheduledJob):
    def __init__(self):
        self.hhmm = get_pwms_config("corporate_actions", "daily_run_time", "03:30")
        enabled = bool(get_pwms_config("corporate_actions", "enabled", True)) and bool(
            get_pwms_config("corporate_actions", "daily_scheduler_enabled", True)
        )
        super().__init__(name="corporate_actions", schedule=DailySchedule(self.hhmm), enabled=enabled)

    def is_current(self, now, *, last_success, at_startup):
        if last_success is not None and localize(last_success).date() == localize(now).date():
            return True, f"today's scan ran at {localize(last_success):%H:%M}"
        return False, "today's scan has not run"

    def run(self, now):
        from corporate_actions.automation import scan_lock

        out = StringIO()
        # Serialize with scans started after a transaction upload.
        with scan_lock():
            call_command("monitor_corporate_actions", source="nse", stdout=out)
        summary = out.getvalue().strip().splitlines()
        return {key: int(value) for key, value in _SUMMARY_PAIR.findall(summary[-1] if summary else "")}
