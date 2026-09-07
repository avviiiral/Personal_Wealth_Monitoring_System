# Portfolio Calculations

## Source of truth

Transaction records are the source of truth for portfolio positions. The portfolio tree groups transactions by:

```text
Family
  -> Portfolio
     -> Asset Class
        -> Sub Class
           -> Asset
```

This prevents the same security from being incorrectly aggregated across different strategies/sub-classes.

## Position calculation

For a transaction group:

- BUY and SIP increase quantity and invested value.
- SELL reduces quantity and removes invested cost using the position's average cost.
- BONUS and SPLIT increase quantity without adding purchase cost.
- Quantity and invested value are calculated from the exact transaction group supplied to the tree service.

Average cost is:

```text
invested_value / quantity
```

when quantity is positive.

## Current value and P&L

Current price is resolved using the configured price hierarchy. Current value and P&L are then calculated for the strategy-specific quantity:

```text
current_value = quantity * current_price
pnl           = current_value - invested_value
pnl_percent   = pnl / invested_value * 100
```

When a current price is genuinely unavailable, the system preserves an unknown value rather than fabricating a zero price.

## XIRR

XIRR uses irregular dated cash flows:

- BUY/SIP -> negative investor cash flow
- SELL -> positive investor cash flow
- dividend-reinvestment transactions marked `DIVIDEND REINVESTMENT` are excluded as fresh external cash
- current portfolio value is added as the terminal positive cash flow when applicable

The project uses a built-in Newton-Raphson solver with a bisection fallback, so an external SciPy dependency is not required for the XIRR calculation.

## Derived holdings

The `Holding` / `PortfolioPosition` data is derived and rebuildable. Transaction data remains the authoritative input.

## Performance note

The portfolio tree is designed to calculate strategy-specific values correctly. Performance optimizations must preserve the same grouping, price-source priority, P&L arithmetic and XIRR cash-flow semantics; optimizations should reduce repeated database queries rather than alter financial logic.
