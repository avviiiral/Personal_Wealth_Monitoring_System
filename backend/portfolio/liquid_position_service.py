from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from rest_framework.exceptions import ValidationError

from .services.portfolio_tree_service import PortfolioTreeService


class LiquidPositionService:
    """Build the family-scoped Liquid Position snapshot and backend calculations."""

    ZERO = Decimal("0.00")
    LAKH = Decimal("100000")
    MONEY_QUANTUM = Decimal("0.01")
    MEMBERS = ("daj", "djt")

    DEFAULT_DOCUMENT = {
        "bank": {"daj": "0.00", "djt": "0.00"},
        "commitments": [
            {"id": "proposed-top-up", "label": "Proposed immediate top up -", "daj": "0.00", "djt": "0.00"},
            {"id": "other-aif", "label": "Other AIF Pending Drawdowns*", "daj": "0.00", "djt": "0.00"},
            {"id": "hoabl", "label": "HOABL", "daj": "0.00", "djt": "0.00"},
            {"id": "gift-to-bft", "label": "Gift to BFT (HOABL)", "daj": "0.00", "djt": "0.00"},
            {"id": "club-house", "label": "Club House, etc**", "daj": "0.00", "djt": "0.00"},
        ],
        "deployment": [
            {"id": "abakkus", "label": "Abakkus", "daj": "0.00", "djt": "0.00"},
            {"id": "w-oak-flexi-cap", "label": "W Oak Flexi Cap", "daj": "0.00", "djt": "0.00"},
            {"id": "parag-parikh-flexi-cap", "label": "Parag Parikh Flexi Cap", "daj": "0.00", "djt": "0.00"},
            {"id": "hdfc-focused", "label": "HDFC Focused * Index switch", "daj": "0.00", "djt": "0.00"},
            {"id": "icici-value-fund", "label": "ICICI Value Fund * India Opp switch", "daj": "0.00", "djt": "0.00"},
            {"id": "silver-etf", "label": "Silver ETF", "daj": "0.00", "djt": "0.00"},
        ],
    }

    @classmethod
    def _parse_amount(cls, value, field_name):
        if value in (None, ""):
            return cls.ZERO
        try:
            amount = Decimal(str(value).replace(",", ""))
        except (InvalidOperation, ValueError):
            raise ValidationError({field_name: "Enter a valid numeric amount."})
        if not amount.is_finite():
            raise ValidationError({field_name: "Amount must be a finite number."})
        if abs(amount) > Decimal("999999999999.99"):
            raise ValidationError({field_name: "Amount is above the supported limit."})
        return amount.quantize(cls.MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    @classmethod
    def normalize_document(cls, document):
        """Validate and normalize user-maintained values, all expressed in lakhs."""
        if document is None:
            document = {}
        if not isinstance(document, dict):
            raise ValidationError({"document": "The document must be an object."})

        bank_source = document.get("bank") or {}
        if not isinstance(bank_source, dict):
            raise ValidationError({"bank": "Bank values must be an object."})
        bank = {
            member: str(cls._parse_amount(bank_source.get(member), "bank." + member))
            for member in cls.MEMBERS
        }

        normalized = {"bank": bank}
        for section in ("commitments", "deployment"):
            defaults = cls.DEFAULT_DOCUMENT[section]
            submitted = document.get(section)
            if submitted is None:
                submitted = defaults
            if not isinstance(submitted, list):
                raise ValidationError({section: "Entries must be provided as a list."})
            if len(submitted) > 40:
                raise ValidationError({section: "A maximum of 40 entries is allowed."})

            entries = []
            seen_ids = set()
            for index, item in enumerate(submitted):
                if not isinstance(item, dict):
                    raise ValidationError({section: "Entry " + str(index + 1) + " must be an object."})
                label = str(item.get("label") or "").strip()
                if not label:
                    raise ValidationError({section: "Enter a label for entry " + str(index + 1) + "."})
                if len(label) > 150:
                    raise ValidationError({section: "Entry " + str(index + 1) + " label must be 150 characters or fewer."})

                entry_id = str(item.get("id") or uuid4().hex).strip()
                if not entry_id or len(entry_id) > 80 or entry_id in seen_ids:
                    raise ValidationError({section: "Entry " + str(index + 1) + " has an invalid or duplicate identifier."})
                seen_ids.add(entry_id)

                entries.append({
                    "id": entry_id,
                    "label": label,
                    "daj": str(cls._parse_amount(item.get("daj"), section + "." + entry_id + ".daj")),
                    "djt": str(cls._parse_amount(item.get("djt"), section + "." + entry_id + ".djt")),
                })
            normalized[section] = entries

        return normalized

    @classmethod
    def current_market_values(cls, holdings_tree):
        """Sum current Liquid and Arbitrage MF values from the Our Holdings tree."""
        values = {
            "liquid": {member: Decimal("0") for member in cls.MEMBERS},
            "arbitrage": {member: Decimal("0") for member in cls.MEMBERS},
        }

        for family_row in (holdings_tree or {}).get("families", []):
            member = str(family_row.get("family_name") or "").strip().casefold()
            if member not in cls.MEMBERS:
                continue

            for portfolio in family_row.get("portfolios", []):
                for asset_class in portfolio.get("asset_classes", []):
                    for sub_class in asset_class.get("sub_classes", []):
                        asset_class_name = str(asset_class.get("asset_class") or "").strip().casefold()
                        sub_class_name = str(sub_class.get("sub_class") or "").strip().casefold()
                        classification = asset_class_name + " " + sub_class_name

                        if "mutual fund" not in classification:
                            continue
                        if "arbitrage" in classification:
                            fund_type = "arbitrage"
                        elif "liquid" in classification:
                            fund_type = "liquid"
                        else:
                            continue

                        for holding in sub_class.get("assets", []):
                            raw_value = holding.get("current_value")
                            if raw_value is None:
                                continue
                            try:
                                market_value = Decimal(str(raw_value))
                            except (InvalidOperation, ValueError):
                                continue
                            if market_value.is_finite():
                                values[fund_type][member] += market_value

        return values

    @classmethod
    def _pair(cls, values):
        daj = Decimal(str(values.get("daj", 0) or 0)).quantize(cls.MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        djt = Decimal(str(values.get("djt", 0) or 0)).quantize(cls.MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        return {
            "daj": float(daj),
            "djt": float(djt),
            "total": float((daj + djt).quantize(cls.MONEY_QUANTUM, rounding=ROUND_HALF_UP)),
        }

    @classmethod
    def _line_output(cls, entries):
        output = []
        for entry in entries:
            daj = Decimal(entry["daj"])
            djt = Decimal(entry["djt"])
            output.append({
                "id": entry["id"],
                "label": entry["label"],
                "daj": float(daj),
                "djt": float(djt),
                "total": float(daj + djt),
            })
        return output

    @classmethod
    def calculate_snapshot(cls, market_values_inr, document):
        """Apply the Liquid Position workbook's totals using values in lakhs."""
        normalized = cls.normalize_document(document)

        liquid_values = {
            member: (market_values_inr["liquid"].get(member, Decimal("0")) / cls.LAKH)
            for member in cls.MEMBERS
        }
        arbitrage_values = {
            member: (market_values_inr["arbitrage"].get(member, Decimal("0")) / cls.LAKH)
            for member in cls.MEMBERS
        }

        liquid = cls._pair(liquid_values)
        arbitrage = cls._pair(arbitrage_values)
        bank = cls._pair({member: Decimal(normalized["bank"][member]) for member in cls.MEMBERS})

        available = cls._pair({
            member: Decimal(str(liquid[member])) + Decimal(str(arbitrage[member])) + Decimal(str(bank[member]))
            for member in cls.MEMBERS
        })

        commitments = cls._line_output(normalized["commitments"])
        deployments = cls._line_output(normalized["deployment"])

        commitment_totals = {
            member: sum((Decimal(entry[member]) for entry in normalized["commitments"]), Decimal("0"))
            for member in cls.MEMBERS
        }
        deployment_totals = {
            member: sum((Decimal(entry[member]) for entry in normalized["deployment"]), Decimal("0"))
            for member in cls.MEMBERS
        }

        net_available = cls._pair({
            member: Decimal(str(available[member])) - commitment_totals[member]
            for member in cls.MEMBERS
        })
        total_deployment = cls._pair(deployment_totals)
        balance = cls._pair({
            member: Decimal(str(net_available[member])) - deployment_totals[member]
            for member in cls.MEMBERS
        })

        return {
            "liquid_mv": liquid,
            "arbitrage_mv": arbitrage,
            "bank": bank,
            "available_liquidity": available,
            "commitments": commitments,
            "net_available_amount": net_available,
            "deployment": deployments,
            "total_deployment_plan": total_deployment,
            "balance": balance,
        }

    @classmethod
    def build(cls, family, owner, as_of_date, document):
        """Use the same current holdings and valuation engine as the Our Holdings page."""
        holdings_tree = PortfolioTreeService.build(
            owner=owner,
            family_id=family.id,
        )
        market_values = cls.current_market_values(holdings_tree)
        return cls.calculate_snapshot(market_values, document)
