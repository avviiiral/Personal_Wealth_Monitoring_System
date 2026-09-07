# Market Data & Mutual Funds

## Market prices

PWMS stores market prices in `MarketPrice` records. Current-price resolution follows the application's explicit source-priority logic:

1. Manual price override when present.
2. Latest automatic market price.
3. Unknown when no price is available.

The `MarketPrice` model records the asset, business date, close price, source and audit metadata. Supported source labels include Yahoo Finance, AMFI, Manual and Other.

## Manual prices

Manual prices are intended for securities that cannot be reliably resolved automatically, including private/unlisted investments and other assets without usable market data. The manual price feature is role/family scoped and intended to remain visible as an explicit override rather than silently replacing the source data.

## Market refresh

The repository uses in-process background scheduling for market-price refreshes. Stock/ETF prices use the Yahoo Finance integration. Mutual-fund NAV data uses the AMFI feed.

## Mutual funds

The `mutual_funds` Django app manages:

- mutual-fund schemes
- NAV records
- mutual-fund transactions
- holdings
- SIPs
- SIP installments and due/overdue state

The mutual-fund value is NAV-driven rather than stock-price driven.

## SecurityMaster

`SecurityMaster` stores descriptive and quantitative security metadata used by the portfolio tree, including fields such as sector, market-cap type, AMC, P/E, P/B, ROE, credit rating, YTM, modified duration and average maturity where applicable.

SecurityMaster values should be treated as reference/security metadata, not as transaction-derived portfolio values.

## Import principle

Transaction Excel import writes the transaction records that drive the portfolio calculation layer. After an import, the application refreshes derived portfolio views so the newly imported data appears without changing the underlying calculation model.
