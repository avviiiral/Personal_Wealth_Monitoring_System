"""Family-scoped mutual-fund portfolio analytics using the existing PWMS ledger."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from analytics.services.mutual_fund_lookthrough import MutualFundLookThroughService
from investments.models import Holding, Transaction, TransactionType as InvestmentTransactionType
from investments.services.asset_underlying import AssetUnderlyingService
from analytics.services.xirr import XIRRCalculator
from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
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



def _legacy_transaction_cashflow(transaction):
    """Convert legacy investments.Transaction mutual-fund rows to investor cash flows."""
    amount = abs(_decimal(transaction.amount))
    fees = abs(_decimal(transaction.fees))
    if transaction.transaction_type in (InvestmentTransactionType.BUY, InvestmentTransactionType.SIP):
        return -(amount + fees), "purchase"
    if transaction.transaction_type == InvestmentTransactionType.SELL:
        return max(ZERO, amount - fees), "redemption"
    if transaction.transaction_type == InvestmentTransactionType.DIVIDEND:
        return max(ZERO, amount - fees), "dividend"
    return None, None


def _normalised_identity(value):
    return " ".join(str(value or "").strip().casefold().split())


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

    # PWMS has two historical representations of personal mutual-fund positions:
    # the dedicated mutual_funds ledger and legacy Asset/Holding/Transaction rows.
    # Load legacy positions only when the generic transaction subclass explicitly
    # identifies them as mutual funds. They are de-duplicated against dedicated
    # positions by exact ISIN or exact normalized scheme/asset name before use.
    legacy_asset_ids = set(
        Transaction.objects.filter(
            family_id=family.id,
            sub_class__icontains="mutual fund",
        ).values_list("asset_id", flat=True).distinct()
    )
    legacy_positions_all = list(
        Holding.objects.filter(
            family_id=family.id,
            asset__family_id=family.id,
            asset__is_active=True,
            asset_id__in=legacy_asset_ids,
            quantity__gt=0,
        ).select_related("asset", "asset__security_master").order_by("asset__name")
    )

    dedicated_isins = {
        str(value or "").strip().upper()
        for holding in holdings
        for value in (holding.scheme.isin_growth, holding.scheme.isin_dividend)
        if str(value or "").strip()
    }
    dedicated_names = {
        _normalised_identity(holding.scheme.scheme_name)
        for holding in holdings
        if _normalised_identity(holding.scheme.scheme_name)
    }

    legacy_positions = []
    duplicate_legacy_positions = 0
    legacy_positions_without_isin = 0
    for position in legacy_positions_all:
        isin = str(position.asset.isin or "").strip().upper()
        name_key = _normalised_identity(position.asset.name)
        if (isin and isin in dedicated_isins) or (name_key and name_key in dedicated_names):
            duplicate_legacy_positions += 1
            continue
        # Without an ISIN or an exact name match, we cannot prove that the
        # generic row is distinct from an existing dedicated scheme. Exclude
        # it rather than risking a duplicate portfolio valuation.
        if not isin:
            legacy_positions_without_isin += 1
            continue
        legacy_positions.append(position)

    if not holdings and not legacy_positions:
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
            "message": (
                "No positive-unit mutual-fund holdings were found in the active family's "
                "dedicated or safely identifiable legacy mutual-fund ledgers."
            ),
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

    legacy_cashflows_by_asset = defaultdict(list)
    legacy_transactions = list(
        Transaction.objects.filter(
            family_id=family.id,
            asset_id__in=[position.asset_id for position in legacy_positions],
            sub_class__icontains="mutual fund",
        ).order_by("transaction_date", "created_at", "id")
    )
    legacy_category_by_asset = {}
    for tx in legacy_transactions:
        if tx.sub_class:
            legacy_category_by_asset[tx.asset_id] = tx.sub_class.strip()
        cashflow, kind = _legacy_transaction_cashflow(tx)
        if cashflow is None or cashflow == ZERO:
            continue
        legacy_cashflows_by_asset[tx.asset_id].append((tx.transaction_date, cashflow))
        portfolio_cashflows.append((tx.transaction_date, cashflow))
        if kind == "purchase":
            cash_totals[kind] += abs(cashflow)
        else:
            cash_totals[kind] += cashflow

    # Resolve each legacy Asset ISIN against the global AMFI master and use
    # the latest persisted AMFI NAV when available; no provider call is made.
    legacy_isins = {
        str(position.asset.isin or "").strip().upper()
        for position in legacy_positions
        if str(position.asset.isin or "").strip()
    }
    legacy_nav_by_isin = {}
    if legacy_isins:
        isin_query = Q()
        for isin in legacy_isins:
            isin_query |= Q(isin_growth__iexact=isin) | Q(isin_dividend__iexact=isin)
        master_nav = AMFIMasterNAV.objects.filter(
            scheme_id=OuterRef("pk")
        ).order_by("-date", "-id")
        master_schemes = AMFIMasterScheme.objects.filter(
            is_active=True
        ).filter(isin_query).annotate(
            latest_nav_value=Subquery(master_nav.values("nav")[:1]),
            latest_nav_date=Subquery(master_nav.values("date")[:1]),
        )
        for master in master_schemes:
            if master.latest_nav_value is None:
                continue
            for isin in (master.isin_growth, master.isin_dividend):
                key = str(isin or "").strip().upper()
                if not key or key not in legacy_isins:
                    continue
                existing = legacy_nav_by_isin.get(key)
                if existing is None or (
                    master.latest_nav_date and
                    (existing[1] is None or master.latest_nav_date > existing[1])
                ):
                    legacy_nav_by_isin[key] = (
                        _decimal(master.latest_nav_value),
                        master.latest_nav_date,
                    )

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
            "position_source": "mutual_fund_ledger",
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

    legacy_asset_market_values = {}
    for position in legacy_positions:
        asset = position.asset
        units = _decimal(position.quantity)
        invested = _decimal(position.invested_value)
        isin = str(asset.isin or "").strip().upper()
        nav_entry = legacy_nav_by_isin.get(isin)
        if nav_entry:
            nav, nav_date = nav_entry
            market_value = units * nav
            valuation_source = "AMFI master NAV history"
        else:
            nav = _decimal(position.current_price) if _decimal(position.current_price) > 0 else None
            nav_date = None
            stored_value = _decimal(position.current_value)
            if stored_value > 0:
                market_value = stored_value
                valuation_source = "Stored legacy holding valuation"
            else:
                market_value = invested
                valuation_source = "Cost basis fallback (no stored NAV/price)"

        category = (
            legacy_category_by_asset.get(position.asset_id)
            or "Unclassified"
        )
        amc_name = (
            str(asset.institution or "").strip()
            or str(getattr(asset.security_master, "amc_name", "") or "").strip()
            or "Unclassified"
        )
        invested_total += invested
        market_total += market_value
        category_values[category] += market_value
        amc_values[amc_name] += market_value
        legacy_asset_market_values[position.asset_id] = market_value
        if nav_date and (latest_nav_date is None or nav_date > latest_nav_date):
            latest_nav_date = nav_date

        legacy_flows = list(legacy_cashflows_by_asset.get(position.asset_id, []))
        if market_value > 0:
            legacy_flows.append((today, market_value))
        holding_rows.append({
            "scheme_id": asset.id,
            "position_source": "legacy_asset_ledger",
            "scheme_name": asset.name,
            "amc_name": amc_name,
            "category": category,
            "plan": None,
            "option": None,
            "units": str(units.quantize(Decimal("0.000001"))),
            "nav": str(nav) if nav is not None else None,
            "nav_date": nav_date.isoformat() if nav_date else None,
            "valuation_source": valuation_source,
            "invested_value": str(invested.quantize(Decimal("0.01"))),
            "market_value": str(market_value.quantize(Decimal("0.01"))),
            "unrealized_gain": str((market_value - invested).quantize(Decimal("0.01"))),
            "unrealized_gain_pct": str(_percentage(market_value - invested, invested)) if invested > 0 else None,
            "xirr_pct": _xirr_percent(legacy_flows),
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

    # Legacy Asset-based funds use PWMS's existing uploaded-underlying ledger.
    legacy_underlyings = AssetUnderlyingService.latest_by_asset(request.user)
    for asset_id, asset_value in legacy_asset_market_values.items():
        if asset_value <= 0:
            continue
        for row in legacy_underlyings.get(asset_id, []):
            disclosed_pct = _decimal(row.holding_percentage)
            if disclosed_pct <= 0:
                continue
            exposure = asset_value * disclosed_pct / HUNDRED
            sector_values[(row.sector or "").strip() or "Unclassified"] += exposure
            stock_values[(row.stock_name or "").strip() or "Unclassified"] += exposure
            uploaded_at = getattr(row, "uploaded_at", None)
            uploaded_date = uploaded_at.date() if uploaded_at else None
            if uploaded_date and (disclosure_as_of is None or uploaded_date > disclosure_as_of):
                disclosure_as_of = uploaded_date

    return Response({
        "available": True,
        "family_id": family.id,
        "as_of": latest_nav_date.isoformat() if latest_nav_date else None,
        "disclosure_as_of": disclosure_as_of.isoformat() if disclosure_as_of else None,
        "source": "Family-scoped dedicated mutual-fund ledger plus legacy Asset/Holding/Transaction positions, de-duplicated by exact ISIN or exact scheme/asset name; stored AMFI NAV and underlying disclosures.",
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
            f"XIRR treats purchases and SIPs as outflows, redemptions and dividends as inflows. "
            f"Included {len(legacy_positions)} safely identified legacy mutual-fund asset position(s); "
            f"excluded {duplicate_legacy_positions} duplicate legacy position(s) and "
            f"{legacy_positions_without_isin} legacy position(s) without an ISIN because they cannot be safely de-duplicated. "
            "and current market value as the terminal cash flow. Sector and stock exposure uses "
            "the latest stored fund disclosures and any latest uploaded legacy asset-underlying snapshots; "
            "the reported disclosure date is shown separately. "
            "The simple total return is a non-annualized gross-cash-flow return, not CAGR."
        ),
    })
