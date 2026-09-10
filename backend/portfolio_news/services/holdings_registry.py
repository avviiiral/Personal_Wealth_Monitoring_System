import re

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List

from investments.models import Holding
from mutual_funds.models import MutualFundHolding
from users.permissions import get_active_family_group_id

from ..constants import HoldingType


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
    return _CORPORATE_SUFFIX_PATTERN.sub("", name).strip()


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
    """One active holding from the explicitly selected family."""

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
        sector=(scheme.category or "").strip(),
        current_value=holding.current_value,
        portfolio_weight=portfolio_weight,
    )


def get_monitored_holdings(user) -> List[MonitoredHolding]:
    """
    Build monitored holdings from the user's explicitly active family.

    Holding and MutualFundHolding already inherit family ownership from
    the family-scoped engines, so this service filters directly by
    family_group_id instead of using legacy owner visibility.
    """
    family_group_id = get_active_family_group_id(user)

    if family_group_id is None:
        return []

    equity_holdings = list(
        Holding.objects
        .filter(
            family_group_id=family_group_id,
            asset__is_active=True,
            quantity__gt=0,
        )
        .select_related("asset", "asset__security_master")
    )

    mutual_fund_holdings = list(
        MutualFundHolding.objects
        .filter(
            family_group_id=family_group_id,
            scheme__is_active=True,
            units__gt=0,
        )
        .select_related("scheme")
    )

    total_current_value = sum(
        (
            holding.current_value or Decimal("0")
            for holding in equity_holdings
        ),
        Decimal("0"),
    ) + sum(
        (
            holding.current_value or Decimal("0")
            for holding in mutual_fund_holdings
        ),
        Decimal("0"),
    )

    monitored_holdings = []

    for holding in equity_holdings:
        weight = (
            float(holding.current_value / total_current_value * 100)
            if total_current_value
            else 0.0
        )
        monitored_holdings.append(
            _build_equity_holding(holding, weight)
        )

    for holding in mutual_fund_holdings:
        weight = (
            float(holding.current_value / total_current_value * 100)
            if total_current_value
            else 0.0
        )
        monitored_holdings.append(
            _build_mutual_fund_holding(holding, weight)
        )

    return monitored_holdings
