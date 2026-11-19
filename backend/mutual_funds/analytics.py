"""Family-scoped mutual-fund portfolio analytics using the existing PWMS ledger."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import OuterRef, Subquery
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from analytics.services.mutual_fund_lookthrough import MutualFundLookThroughService
from analytics.services.xirr import XIRRCalculator
from users.permissions import require_active_family

from .models import MutualFundHolding, MutualFundNAV, MutualFundTransaction, MutualFundTransactionType


ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _decimal(value):
    if value in (None, ""):
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def _transaction_cashflow(transaction):
    """Investor purchases are negative; proceeds and dividends are positive."""
    amount = abs(_decimal(transaction.amount))
    fees = abs(_decimal(transaction.fees))
    if transaction.transaction_type in (MutualFundTransactionType.PURCHASE, MutualFundTransactionType.SIP):
        return -(amount + fees), "purchase"
    if transaction.transaction_type == MutualFundTransactionType.REDEMPTION:
        return max(ZERO, amount - fees), "redemption"
    if transaction.transaction_type == MutualFundTransactionType.DIVIDEND:
        return max(ZERO, amount - fees), "dividend"
    return None, None


def _percentage(numerator, denominator):
    if denominator <= 0:
        return None
    return (numerator / denominator * HUNDRED).quantize(Decimal("0.01"))


def _xirr_percent(cashflows):
    try:
        value = XIRRCalculator.calculate(cashflows)
    except (ArithmeticError, OverflowError, ValueError):
        value = None
    return round(value * 100, 2) if value is not None else None


def _distribution(values, total):
    rows = []
    for name, value in sorted(values.items(), key=lambda item: item[1], reverse=True):
        if value <= 0:
            continue
        rows.append({
            "name": name,
            "market_value": str(value.quantize(Decimal("0.01"))),
            "weight_pct": str(_percentage(value, total)) if total > 0 else None,
        })
    return rows


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_mutual_fund_portfolio_analytics(request):
    """Return active-family positions, XIRR and latest stored look-through data.

    Only MutualFundHolding and MutualFundTransaction are read. The generic
    investment Transaction ledger is deliberately not queried or combined.
    """
    family = require_active_family(request.user)
    today = timezone.localdate()

    latest_nav = MutualFundNAV.objects.filter(
        scheme_id=OuterRef("scheme_id")
    ).order_by("-date", "-id")
    holdings = list(
        MutualFundHolding.objects.filter(
            family_id=family.id,
            scheme__family_id=family.id,
            scheme__is_active=True,
            units__gt=0,
        )
        .select_related("scheme")
        .annotate(
            latest_nav_value=Subquery(latest_nav.values("nav")[:1]),
            latest_nav_date=Subquery(latest_nav.values("date")[:1]),
        )
        .order_by("scheme__scheme_name")
    )

    if not holdings:
        return Response({
            "available": False,
            "family_id": family.id,
            "as_of": None,
            "disclosure_as_of": None,
            "summary": {
                "holdings_count": 0,
                "invested_value": "0.00",
                "market_value": "0.00",
                "unrealized_gain": "0.00",
                "unrealized_gain_pct": None,
                "gross_purchase_outflow": "0.00",
                "redemption_proceeds": "0.00",
                "dividend_income": "0.00",
                "simple_total_return_pct": None,
                "xirr_pct": None,
            },
            "holdings": [],
            "allocations": {"categories": [], "amcs": [], "sectors": [], "top_stocks": []},
            "message": "No positive-unit mutual-fund holdings are recorded for the active family.",
        })

    scheme_ids = [holding.scheme_id for holding in holdings]
    transactions = list(
        MutualFundTransaction.objects.filter(family_id=family.id, scheme_id__in=scheme_ids)
        .select_related("scheme")
        .order_by("transaction_date", "created_at", "id")
    )
    cashflows_by_scheme = defaultdict(list)
    portfolio_cashflows = []
    cash_totals = {"purchase": ZERO, "redemption": ZERO, "dividend": ZERO}
    for tx in transactions:
        cashflow, kind = _transaction_cashflow(tx)
        if cashflow is None or cashflow == ZERO:
            continue
        cashflows_by_scheme[tx.scheme_id].append((tx.transaction_date, cashflow))
        portfolio_cashflows.append((tx.transaction_date, cashflow))
        if kind == "purchase":
            cash_totals[kind] += abs(cashflow)
        else:
            cash_totals[kind] += cashflow

    holding_rows = []
    market_values = {}
    category_values = defaultdict(lambda: ZERO)
    amc_values = defaultdict(lambda: ZERO)
    invested_total = ZERO
    market_total = ZERO
    latest_nav_date = None

    for holding in holdings:
        scheme = holding.scheme
        units = _decimal(holding.units)
        invested = _decimal(holding.invested_value)
        if holding.latest_nav_value is not None:
            nav = _decimal(holding.latest_nav_value)
            market_value = units * nav
            valuation_source = "AMFI NAV history"
        else:
            nav = _decimal(holding.current_nav) if _decimal(holding.current_nav) > 0 else None
            market_value = _decimal(holding.current_value)
            valuation_source = "Stored holding valuation"

        unrealized = market_value - invested
        market_values[holding.scheme_id] = market_value
        invested_total += invested
        market_total += market_value
        category_values[(scheme.category or "").strip() or "Unclassified"] += market_value
        amc_values[(scheme.amc_name or "").strip() or "Unclassified"] += market_value
        nav_date = holding.latest_nav_date
        if nav_date and (latest_nav_date is None or nav_date > latest_nav_date):
            latest_nav_date = nav_date

        scheme_flows = list(cashflows_by_scheme.get(holding.scheme_id, []))
        if market_value > 0:
            scheme_flows.append((today, market_value))
        holding_rows.append({
            "scheme_id": scheme.id,
            "scheme_name": scheme.scheme_name,
            "amc_name": scheme.amc_name,
            "category": scheme.category or "Unclassified",
            "plan": scheme.plan,
            "option": scheme.option,
            "units": str(units.quantize(Decimal("0.000001"))),
            "nav": str(nav) if nav is not None else None,
            "nav_date": nav_date.isoformat() if nav_date else None,
            "valuation_source": valuation_source,
            "invested_value": str(invested.quantize(Decimal("0.01"))),
            "market_value": str(market_value.quantize(Decimal("0.01"))),
            "unrealized_gain": str(unrealized.quantize(Decimal("0.01"))),
            "unrealized_gain_pct": str(_percentage(unrealized, invested)) if invested > 0 else None,
            "xirr_pct": _xirr_percent(scheme_flows),
        })

    if market_total > 0:
        portfolio_cashflows.append((today, market_total))
    total_return = market_total + cash_totals["redemption"] + cash_totals["dividend"] - cash_totals["purchase"]
    simple_return_pct = _percentage(total_return, cash_totals["purchase"])

    lookthrough = MutualFundLookThroughService.latest_underlyings(request.user)
    sector_values = defaultdict(lambda: ZERO)
    stock_values = defaultdict(lambda: ZERO)
    disclosure_as_of = None
    for scheme_id, disclosure in lookthrough.items():
        scheme_value = market_values.get(scheme_id, ZERO)
        if scheme_value <= 0:
            continue
        disclosure_date = disclosure.get("portfolio_date")
        if disclosure_date and (disclosure_as_of is None or disclosure_date > disclosure_as_of):
            disclosure_as_of = disclosure_date
        for row in disclosure.get("rows", []):
            disclosed_pct = _decimal(row.percentage_of_nav)
            if disclosed_pct <= 0:
                continue
            exposure = scheme_value * disclosed_pct / HUNDRED
            sector_values[(row.sector or "").strip() or "Unclassified"] += exposure
            stock_values[(row.security_name or "").strip() or "Unclassified"] += exposure

    return Response({
        "available": True,
        "family_id": family.id,
        "as_of": latest_nav_date.isoformat() if latest_nav_date else None,
        "disclosure_as_of": disclosure_as_of.isoformat() if disclosure_as_of else None,
        "source": "Existing family-scoped MutualFundHolding and MutualFundTransaction ledgers; latest stored AMFI NAV and portfolio disclosures.",
        "summary": {
            "holdings_count": len(holding_rows),
            "invested_value": str(invested_total.quantize(Decimal("0.01"))),
            "market_value": str(market_total.quantize(Decimal("0.01"))),
            "unrealized_gain": str((market_total - invested_total).quantize(Decimal("0.01"))),
            "unrealized_gain_pct": str(_percentage(market_total - invested_total, invested_total)) if invested_total > 0 else None,
            "gross_purchase_outflow": str(cash_totals["purchase"].quantize(Decimal("0.01"))),
            "redemption_proceeds": str(cash_totals["redemption"].quantize(Decimal("0.01"))),
            "dividend_income": str(cash_totals["dividend"].quantize(Decimal("0.01"))),
            "simple_total_return_pct": str(simple_return_pct) if simple_return_pct is not None else None,
            "xirr_pct": _xirr_percent(portfolio_cashflows),
        },
        "holdings": holding_rows,
        "allocations": {
            "categories": _distribution(category_values, market_total),
            "amcs": _distribution(amc_values, market_total),
            "sectors": _distribution(sector_values, market_total),
            "top_stocks": _distribution(stock_values, market_total)[:20],
        },
        "message": (
            "XIRR treats purchases and SIPs as outflows, redemptions and dividends as inflows, "
            "and current market value as the terminal cash flow. Sector and stock exposure uses "
            "the latest stored fund portfolio disclosures; their date is shown separately. "
            "The simple total return is a non-annualized gross-cash-flow return, not CAGR."
        ),
    })
