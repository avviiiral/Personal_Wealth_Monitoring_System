"""Pluggable per-AMC portfolio-disclosure parsers.

Every AMC publishes its SEBI-mandated monthly portfolio in its own workbook
layout. Each AMC gets one parser class with a common interface:

    parser.parse(payload, filename, portfolio_date) -> list[PortfolioRow]

PortfolioRow carries the fields the spec asks for (scheme ISIN, security name,
security ISIN, sector, asset type, quantity, market value, % of NAV, as-of
date) plus scheme code/name and debt attributes when the file provides them.

The five registered AMCs reuse the column-alias parser already used by
``sync_mf_portfolios`` (masterdata.parse_portfolio_rows). To support a new
AMC, subclass AMCPortfolioParser, implement parse() for its layout, and
decorate it with @register.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

from mutual_funds.services.masterdata import parse_portfolio_rows


@dataclass
class PortfolioRow:
    scheme_isin: str
    security_name: str
    security_isin: str
    sector: str
    asset_type: str
    quantity: Decimal | None
    market_value: Decimal | None
    pct_of_nav: Decimal | None
    as_of_date: date
    scheme_code: str = ""
    scheme_name: str = ""
    extra: dict = field(default_factory=dict)

    def to_persist_dict(self) -> dict:
        """Shape expected by masterdata.persist_portfolio_rows."""
        return {
            "security_name": self.security_name, "isin": self.security_isin,
            "instrument_type": self.asset_type or "OTHER", "sector": self.sector,
            "quantity": self.quantity, "market_value": self.market_value,
            "pct_of_nav": self.pct_of_nav, **self.extra,
        }

    def as_dict(self) -> dict:
        return asdict(self)


REGISTRY: dict[str, "AMCPortfolioParser"] = {}


def register(cls):
    REGISTRY[cls.code] = cls()
    return cls


class AMCPortfolioParser:
    code = ""
    canonical_name = ""
    aliases: tuple[str, ...] = ()

    def matches(self, amc_name: str) -> bool:
        value = (amc_name or "").casefold()
        return bool(value) and (
            value == self.canonical_name.casefold() or any(alias in value for alias in self.aliases)
        )

    def parse(self, payload: bytes, filename: str, portfolio_date: date) -> list[PortfolioRow]:
        raise NotImplementedError


class ColumnAliasPortfolioParser(AMCPortfolioParser):
    """Header-alias parser for the common SEBI disclosure columns."""

    def parse(self, payload, filename, portfolio_date):
        rows = parse_portfolio_rows(payload, filename=filename, amc=self.canonical_name, portfolio_date=portfolio_date)
        return [
            PortfolioRow(
                scheme_isin=row.get("scheme_isin", ""), security_name=row["security_name"],
                security_isin=row.get("isin", ""), sector=row.get("sector", ""),
                asset_type=row.get("instrument_type", "OTHER"), quantity=row.get("quantity"),
                market_value=row.get("market_value"), pct_of_nav=row.get("pct_of_nav"),
                as_of_date=portfolio_date, scheme_code=row.get("scheme_code", ""),
                scheme_name=row.get("scheme_name", ""),
                extra={key: row.get(key) for key in ("industry", "rating", "rating_agency", "maturity_date", "ytm_pct")},
            )
            for row in rows
        ]


def _adapter(code):
    from mutual_funds.management.commands.sync_mf_portfolios import AMC_ADAPTERS
    return AMC_ADAPTERS[code]


@register
class HDFCPortfolioParser(ColumnAliasPortfolioParser):
    code = "hdfc"
    canonical_name = _adapter("hdfc")["canonical"]
    aliases = _adapter("hdfc")["aliases"]


@register
class ICICIPrudentialPortfolioParser(ColumnAliasPortfolioParser):
    code = "icici_prudential"
    canonical_name = _adapter("icici_prudential")["canonical"]
    aliases = _adapter("icici_prudential")["aliases"]


@register
class SBIPortfolioParser(ColumnAliasPortfolioParser):
    code = "sbi"
    canonical_name = _adapter("sbi")["canonical"]
    aliases = _adapter("sbi")["aliases"]


@register
class NipponIndiaPortfolioParser(ColumnAliasPortfolioParser):
    code = "nippon_india"
    canonical_name = _adapter("nippon_india")["canonical"]
    aliases = _adapter("nippon_india")["aliases"]


@register
class UTIPortfolioParser(ColumnAliasPortfolioParser):
    code = "uti"
    canonical_name = _adapter("uti")["canonical"]
    aliases = _adapter("uti")["aliases"]


def parser_for_amc(amc_name: str) -> AMCPortfolioParser | None:
    for parser in REGISTRY.values():
        if parser.matches(amc_name):
            return parser
    return None
