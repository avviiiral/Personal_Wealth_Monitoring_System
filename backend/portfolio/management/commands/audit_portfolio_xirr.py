from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from investments.services import cash_flows as cash_flow_service
from portfolio.services.portfolio_calculation_service import PortfolioCalculationService
from portfolio.services.portfolio_tree_service import PortfolioTreeService


class Command(BaseCommand):
    help = (
        "Audit the exact transaction cash flows and terminal valuation used by "
        "Portfolio XIRR for a subclass or asset-name/strategy summary."
    )

    def add_arguments(self, parser):
        parser.add_argument("--family-id", type=int, required=True)
        parser.add_argument("--sub-class", required=True)
        parser.add_argument("--asset-name", default=None)
        parser.add_argument("--family-name", default=None)
        parser.add_argument("--asset-class", default=None)
        parser.add_argument("--advisor", default=None)

    def handle(self, *args, **options):
        family_id = options["family_id"]
        sub_class = options["sub_class"].strip()
        asset_name = (options["asset_name"] or "").strip()
        family_name = (options["family_name"] or "").strip()
        asset_class = (options["asset_class"] or "").strip()
        advisor = (options["advisor"] or "").strip()

        if not sub_class:
            raise CommandError("--sub-class cannot be blank.")

        owner_ids = list(
            PortfolioTreeService._get_transactions([], family_id=family_id)
            .values_list("owner_id", flat=True)
            .distinct()
        )
        if not owner_ids:
            raise CommandError(f"No transactions found for family_id={family_id}.")

        tree = PortfolioTreeService.build(owner=owner_ids, family_id=family_id)
        rows = PortfolioCalculationService._asset_rows(tree)

        matching_rows = []
        for row in rows:
            if row["sub_class"] != sub_class:
                continue
            if asset_name and (row["asset"].get("asset_name") or "").strip() != asset_name:
                continue
            if family_name and row["family_name"] != family_name:
                continue
            if asset_class and row["asset_class"] != asset_class:
                continue
            if advisor and (row["asset"].get("advisors") or "").strip() != advisor:
                continue
            matching_rows.append(row)

        if not matching_rows:
            raise CommandError(
                "No currently held Portfolio rows match the supplied scope. "
                "Check --sub-class, --asset-name, --family-name, --asset-class, "
                "and --family-id."
            )

        transactions = list(
            PortfolioTreeService._get_transactions(owner_ids, family_id=family_id)
        )
        scope_asset_name = asset_name or None
        selected = []
        for tx in transactions:
            tx_family = (tx.family_name or "").strip() or "Unassigned"
            tx_asset_class = (tx.asset_class or "").strip() or "Unassigned"
            tx_sub_class = (tx.sub_class or "").strip() or "Unassigned"
            tx_asset_name = (tx.asset_name or "").strip() or "Unassigned"
            if family_name and tx_family != family_name:
                continue
            if asset_class and tx_asset_class != asset_class:
                continue
            if tx_sub_class != sub_class:
                continue
            if scope_asset_name and tx_asset_name != scope_asset_name:
                continue
            if advisor and (tx.advisors or "").strip() != advisor:
                continue
            selected.append(tx)

        flows = cash_flow_service.build_cash_flows(selected)
        terminal_value = sum(
            (
                Decimal(str(row["asset"].get("current_value") or 0))
                for row in matching_rows
                if row["asset"].get("current_price") is not None
            ),
            Decimal("0"),
        )
        terminal_quantity = sum(
            (
                Decimal(str(row["asset"].get("quantity") or 0))
                for row in matching_rows
            ),
            Decimal("0"),
        )
        if terminal_quantity > 0 and terminal_value > 0:
            flows.append((date.today(), float(terminal_value)))

        self.stdout.write(f"Scope: family_id={family_id}, sub_class={sub_class!r}, asset_name={asset_name or '*'}")
        self.stdout.write(f"Transaction rows selected: {len(selected)}")
        self.stdout.write(f"Cash-flow rows (excluding terminal value): {len(flows) - (1 if terminal_quantity > 0 and terminal_value > 0 else 0)}")
        self.stdout.write(f"Terminal quantity: {terminal_quantity}")
        self.stdout.write(f"Terminal value used: {terminal_value if terminal_quantity > 0 else Decimal('0')}")
        self.stdout.write(f"Terminal valuation date used: {date.today() if terminal_quantity > 0 and terminal_value > 0 else 'not added'}")
        self.stdout.write(f"PWMS XIRR from audited inputs: {cash_flow_service.xirr_percent(flows)}%")
        self.stdout.write("")
        self.stdout.write("DATE\tTYPE\tAMOUNT\tFEES\tSIGNED CASH FLOW\tASSET NAME\tUNDERLYING\tPORTFOLIO")
        for tx in sorted(selected, key=lambda item: (item.transaction_date, item.id)):
            flow = cash_flow_service.transaction_cash_flow(tx)
            signed_amount = "SKIPPED" if flow is None else f"{flow[1]:.2f}"
            self.stdout.write(
                f"{tx.transaction_date}\t{tx.transaction_type}\t{tx.amount or 0}\t{tx.fees or 0}\t"
                f"{signed_amount}\t{tx.asset_name}\t{tx.underlying}\t{tx.portfolio}"
            )
        if terminal_quantity > 0 and terminal_value > 0:
            self.stdout.write(
                f"{date.today()}\tTERMINAL VALUE\t{terminal_value}\t0\t"
                f"{terminal_value:.2f}\t{asset_name or sub_class}\t\t"
            )
