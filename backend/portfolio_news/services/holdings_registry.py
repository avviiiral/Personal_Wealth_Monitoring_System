import re

from dataclasses import (
    dataclass,
    field,
)

from decimal import Decimal

from typing import List

from analytics.services.unified_wealth import UnifiedWealthAnalytics

from ..constants import HoldingType


# Generic corporate-form suffixes stripped to derive a company
# alias. This is structural (works for any company name), not a
# hardcoded per-company alias table - it never encodes anyone's
# actual holdings.
_CORPORATE_SUFFIX_PATTERN = re.compile(
    r"\s+"
    r"(limited|ltd\.?|"
    r"private\s+limited|pvt\.?\s+ltd\.?|"
    r"incorporated|inc\.?|"
    r"corporation|corp\.?|"
    r"company|co\.?|"
    r"plc)"
    r"\s*$",
    re.IGNORECASE,
)

# Mutual fund scheme names carry plan/option boilerplate,
# often as multiple " - " separated segments (e.g.
# "Fund - Direct Plan - Growth"), that add no matching value
# against news headlines.
_MF_BOILERPLATE_TOKENS = {
    "direct",
    "regular",
    "plan",
    "growth",
    "dividend",
    "idcw",
    "payout",
    "reinvestment",
}


def _strip_corporate_suffix(name: str) -> str:
    stripped = _CORPORATE_SUFFIX_PATTERN.sub("", name).strip()

    return stripped


def _strip_mf_boilerplate(name: str) -> str:

    segments = [segment.strip() for segment in name.split(" - ")]

    while len(segments) > 1:

        last_words = segments[-1].lower().split()

        if last_words and all(
            word in _MF_BOILERPLATE_TOKENS for word in last_words
        ):
            segments.pop()
        else:
            break

    return " - ".join(segments).strip()


@dataclass
class MonitoredHolding:
    """
    A single holding to monitor for news, with everything the
    query builder and holding matcher need. Built fresh from
    the user's live portfolio on every run - holdings that are
    sold off simply stop appearing here, so monitoring adapts
    automatically.
    """

    holding_type: str

    holding_id: int

    display_name: str

    aliases: List[str] = field(default_factory=list)

    symbol: str = ""

    isin: str = ""

    amc_name: str = ""

    scheme_code: str = ""

    sector: str = ""

    current_value: Decimal = Decimal("0")

    portfolio_weight: float = 0.0

    def identifier_terms(self) -> List[str]:
        """
        All name-like terms usable for search/matching,
        deduplicated and with empties removed.
        """

        terms = [self.display_name] + list(self.aliases)

        return list(
            dict.fromkeys(
                term.strip() for term in terms if term and term.strip()
            )
        )


def _build_equity_holding(holding, portfolio_weight: float) -> MonitoredHolding:

    asset = holding.asset

    aliases = []

    stripped = _strip_corporate_suffix(asset.name)

    if stripped and stripped.lower() != asset.name.lower():
        aliases.append(stripped)

    sector = ""

    if asset.security_master is not None:
        sector = (asset.security_master.sector or "").strip()

    return MonitoredHolding(
        holding_type=HoldingType.EQUITY,
        holding_id=asset.id,
        display_name=asset.name,
        aliases=aliases,
        symbol=(asset.symbol or "").strip(),
        isin=(asset.isin or "").strip(),
        sector=sector,
        current_value=holding.current_value,
        portfolio_weight=portfolio_weight,
    )


def _build_mutual_fund_holding(
    holding,
    portfolio_weight: float,
) -> MonitoredHolding:

    scheme = holding.scheme

    aliases = []

    without_boilerplate = _strip_mf_boilerplate(scheme.scheme_name)

    if (
        without_boilerplate
        and without_boilerplate.lower() != scheme.scheme_name.lower()
    ):
        aliases.append(without_boilerplate)

    return MonitoredHolding(
        holding_type=HoldingType.MUTUAL_FUND,
        holding_id=scheme.id,
        display_name=scheme.scheme_name,
        aliases=aliases,
        isin=(scheme.isin_growth or scheme.isin_dividend or "").strip(),
        amc_name=(scheme.amc_name or "").strip(),
        scheme_code=(scheme.scheme_code or "").strip(),
        # Mutual funds have no GICS-style sector; scheme category
        # (e.g. "Banking", "Pharma & Healthcare", "Technology") is
        # the closest available proxy and is what powers sector/
        # macro query generation for fund holdings. Broad categories
        # like "Equity"/"Debt" simply won't match any entry in
        # QueryBuilder.MACRO_TOPICS_BY_SECTOR, which is harmless -
        # no macro query gets added for those, same as an equity
        # holding with an unclassified sector.
        sector=(scheme.category or "").strip(),
        current_value=holding.current_value,
        portfolio_weight=portfolio_weight,
    )


def get_monitored_holdings(user) -> List[MonitoredHolding]:
    """
    Build the list of holdings to monitor for the given user,
    directly from their live PWMS portfolio.

    Nothing here is hardcoded: if the user's holdings change,
    the next call reflects that automatically. Zero-quantity /
    fully exited positions are excluded even if still marked
    active, since there's nothing left to protect an alert
    against.
    """

    summary = UnifiedWealthAnalytics.calculate_summary(user)

    total_current_value = summary.get(
        "total_current_value",
        Decimal("0"),
    ) or Decimal("0")

    monitored_holdings = []

    equity_holdings = UnifiedWealthAnalytics.get_equity_holdings(user)

    for holding in equity_holdings:

        if holding.quantity <= 0:
            continue

        weight = (
            float(holding.current_value / total_current_value * 100)
            if total_current_value
            else 0.0
        )

        monitored_holdings.append(
            _build_equity_holding(holding, weight)
        )

    mutual_fund_holdings = (
        UnifiedWealthAnalytics.get_mutual_fund_holdings(user)
    )

    for holding in mutual_fund_holdings:

        if holding.units <= 0:
            continue

        weight = (
            float(holding.current_value / total_current_value * 100)
            if total_current_value
            else 0.0
        )

        monitored_holdings.append(
            _build_mutual_fund_holding(holding, weight)
        )

    return monitored_holdings