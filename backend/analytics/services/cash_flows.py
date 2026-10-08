"""Single source of truth for investor cash flows used by every XIRR.

Sign convention (investor's point of view):
    money paid out  -> negative  (BUY, SIP, DEPOSIT, fees on those)
    money received  -> positive  (SELL, DIVIDEND, INTEREST, WITHDRAWAL, net of fees)

A BUY imported as "DIVIDEND REINVESTMENT" is not fresh external cash (the
dividend never left the investment), so it is skipped.
"""

from decimal import Decimal

from investments.models import TransactionType

from .xirr import XIRRCalculator

ZERO = Decimal("0")
DIVIDEND_REINVESTMENT_NOTE = "DIVIDEND REINVESTMENT"

OUTFLOW_TYPES = (
    TransactionType.BUY,
    TransactionType.SIP,
    TransactionType.DEPOSIT,
)
INFLOW_TYPES = (
    TransactionType.SELL,
    TransactionType.DIVIDEND,
    TransactionType.INTEREST,
    TransactionType.WITHDRAWAL,
)


def transaction_cash_flow(tx, scale=1.0):
    """Return ``(date, signed_amount)`` for one equity transaction or ``None``.

    ``scale`` lets callers apportion a flow (for example an underlying
    holding percentage of a fund).
    """
    if tx.notes == DIVIDEND_REINVESTMENT_NOTE:
        return None

    amount = tx.amount or ZERO
    fees = tx.fees or ZERO

    if tx.transaction_type in OUTFLOW_TYPES:
        value = -(amount + fees)
    elif tx.transaction_type in INFLOW_TYPES:
        value = amount - fees
    else:
        return None

    return (tx.transaction_date, float(value) * scale)


def build_cash_flows(transactions, scale=1.0):
    """Dated investor cash flows for ``transactions`` (terminal value excluded)."""
    flows = []
    for tx in transactions:
        flow = transaction_cash_flow(tx, scale=scale)
        if flow is not None:
            flows.append(flow)
    return flows


def xirr_percent(cash_flows):
    """XIRR as a percentage rounded to 2 dp, or ``None`` if undefined."""
    if len(cash_flows) < 2:
        return None
    rate = XIRRCalculator.calculate(cash_flows)
    if rate is None:
        return None
    return round(rate * 100, 2)
