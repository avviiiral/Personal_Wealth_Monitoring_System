from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from .liquid_position_service import LiquidPositionService


class LiquidPositionCalculationTests(SimpleTestCase):
    def test_market_values_only_include_liquid_and_arbitrage_mutual_funds(self):
        rows = [
            {
                "asset_class": "Mutual Funds",
                "sub_class": "Liquid Fund",
                "family_name": "DAJ",
                "closing_amount": Decimal("1250000"),
            },
            {
                "asset_class": "Mutual Funds",
                "sub_class": "Arbitrage Fund",
                "family_name": "DJT",
                "closing_amount": Decimal("2500000"),
            },
            {
                "asset_class": "Mutual Funds",
                "sub_class": "Equity Fund",
                "family_name": "DAJ",
                "closing_amount": Decimal("9000000"),
            },
            {
                "asset_class": "Direct Equity",
                "sub_class": "Liquid",
                "family_name": "DAJ",
                "closing_amount": Decimal("700000"),
            },
        ]

        values = LiquidPositionService.current_market_values(rows)

        self.assertEqual(values["liquid"]["daj"], Decimal("1250000"))
        self.assertEqual(values["arbitrage"]["djt"], Decimal("2500000"))
        self.assertEqual(values["liquid"]["djt"], Decimal("0"))
        self.assertEqual(values["arbitrage"]["daj"], Decimal("0"))

    def test_totals_are_calculated_in_backend_for_commitments_and_deployments(self):
        document = {
            "bank": {"daj": "10", "djt": "5"},
            "commitments": [
                {"id": "c1", "label": "Pending commitment", "daj": "2", "djt": "1"},
            ],
            "deployment": [
                {"id": "d1", "label": "Planned investment", "daj": "3", "djt": "2"},
            ],
        }
        market_values = {
            "liquid": {"daj": Decimal("1000000"), "djt": Decimal("2000000")},
            "arbitrage": {"daj": Decimal("500000"), "djt": Decimal("1000000")},
        }

        result = LiquidPositionService.calculate_snapshot(market_values, document)

        self.assertEqual(result["liquid_mv"]["total"], 30.0)
        self.assertEqual(result["arbitrage_mv"]["total"], 15.0)
        self.assertEqual(result["available_liquidity"]["daj"], 25.0)
        self.assertEqual(result["available_liquidity"]["djt"], 35.0)
        self.assertEqual(result["net_available_amount"]["daj"], 23.0)
        self.assertEqual(result["net_available_amount"]["djt"], 34.0)
        self.assertEqual(result["total_deployment_plan"]["total"], 5.0)
        self.assertEqual(result["balance"]["total"], 52.0)

    def test_invalid_negative_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            LiquidPositionService.normalize_document({
                "bank": {"daj": "-1", "djt": "0"},
                "commitments": [],
                "deployment": [],
            })
