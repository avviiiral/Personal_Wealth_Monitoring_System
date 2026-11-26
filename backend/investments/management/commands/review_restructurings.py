"""Review mergers and demergers held from transaction uploads.

Listing is read-only. Transactions are created only by ``--approve`` with the
cost you confirm; ``--reject`` creates nothing.
"""
from django.core.management.base import BaseCommand, CommandError

from investments.models import PendingRestructuring
from investments.services import restructuring
from investments.services.restructuring import RestructuringError, _money


class Command(BaseCommand):
    help = "List, approve or reject held mergers/demergers (approve creates transactions)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Include applied and rejected items.")
        parser.add_argument("--approve", type=int, metavar="ID", help="Create the transactions for this item.")
        parser.add_argument("--reject", type=int, metavar="ID", help="Discard this item; nothing is created.")
        parser.add_argument("--cost", help="Total cost carried by the old holding (must match the engine unless --force).")
        parser.add_argument("--split", help='Demerger only: "New Co A=0.10,New Co B=0.20" share of cost per new company.')
        parser.add_argument("--force", action="store_true", help="Skip the cost/quantity cross-checks you have verified.")

    def handle(self, *args, **options):
        if options["approve"] and options["reject"]:
            raise CommandError("Use either --approve or --reject.")
        if options["reject"]:
            try:
                restructuring.reject(options["reject"], user=None)
            except (PendingRestructuring.DoesNotExist, RestructuringError) as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(f"Rejected #{options['reject']}. No transactions were created.")
            return
        if options["approve"]:
            self._approve(options)
            return
        self._list(options["all"])

    # ----------------------------------------------------------------- list
    def _list(self, include_all):
        items = PendingRestructuring.objects.select_related("family")
        if not include_all:
            items = items.filter(status="PENDING")
        items = list(items)
        if not items:
            self.stdout.write("No merger/demerger items to review.")
            return
        for pending in items:
            self.stdout.write(self._describe(pending))
        self.stdout.write("Nothing has been created. Approve with: "
                          "python manage.py review_restructurings --approve ID --cost AMOUNT [--split ...]")

    def _describe(self, pending):
        proposal = restructuring.build_proposal(pending)
        lines = [f"#{pending.id} {pending.kind} {pending.event_date} {pending.family_name} / {pending.asset_name} [{pending.status}]"]
        for leg in sorted(pending.legs, key=lambda item: item["row"]):
            lines.append(f"    row {leg['row']} {leg['role']:<6} {leg['underlying']}: qty {leg['quantity']} amount {leg['amount']}")
        holding = proposal["holding"]
        if holding is not None:
            lines.append(f"    cost this holding carries today: {_money(holding['cost'])} for {holding['quantity']} units")
        elif proposal["asset"] is not None:
            lines.append("    cost this holding carries today: not available (held elsewhere too, or later activity); pass --cost yourself")
        if pending.kind == "DEMERGER":
            lines.append("    needs --split with the cost share of each new company")
        for problem in proposal["problems"]:
            lines.append(f"    ! {problem}")
        return "\n".join(lines)

    # -------------------------------------------------------------- approve
    def _approve(self, options):
        try:
            pending = PendingRestructuring.objects.get(pk=options["approve"])
        except PendingRestructuring.DoesNotExist as exc:
            raise CommandError(f"No item #{options['approve']}.") from exc
        proposal = restructuring.build_proposal(pending)
        if pending.status != "PENDING":
            raise CommandError(f"#{pending.id} is already {pending.status.lower()}.")
        if not options["cost"]:
            holding = proposal["holding"]
            self.stdout.write(self._describe(pending))
            if holding is None:
                raise CommandError("--cost is required: the carried cost could not be derived for this holding.")
            self.stdout.write(f"Preview only, nothing created. To approve, add: --cost {_money(holding['cost'])}")
            return
        try:
            split = restructuring.parse_split(options["split"])
            ids = restructuring.apply(pending.id, user=None, cost=options["cost"], split=split, force=options["force"])
        except RestructuringError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Approved #{pending.id}. Created {len(ids)} transactions: {ids}")
