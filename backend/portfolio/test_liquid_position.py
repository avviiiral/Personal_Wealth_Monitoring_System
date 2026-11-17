from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from .liquid_position_service import LiquidPositionService


class LiquidPositionCalculationTests(SimpleTestCase):
    def test_market_values_are_summed_from_our_holdings_tree(self):
        holdings_tree = {
            "families": [
                {
                    "family_name": "DAJ",
                    "portfolios": [
                        {
                            "asset_classes": [
                                {
                                    "asset_class": "Mutual Fund",
                                    "sub_classes": [
                                        {
                                            "sub_class": "Liquid Mutual Funds",
                                            "assets": [
                                                {"current_value": 1250000},
                                                {"current_value": 250000},
                                            ],
                                        },
                                        {
                                            "sub_class": "Arbitrage Mutual Funds",
                                            "assets": [{"current_value": 500000}],
                                        },
                                        {
                                            "sub_class": "Equity Mutual Funds",
                                            "assets": [{"current_value": 9000000}],
                                        },
                                    ],
                                },
                                {
                                    "asset_class": "Equity",
                                    "sub_classes": [
                                        {
                                            "sub_class": "Liquid",
                                            "assets": [{"current_value": 700000}],
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                },
                {
                    "family_name": "DJT",
                    "portfolios": [
                        {
                            "asset_classes": [
                                {
                                    "asset_class": "Mutual Fund",
                                    "sub_classes": [
                                        {
                                            "sub_class": "Arbitrage Mutual Funds",
                                            "assets": [{"current_value": 2500000}],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
            ]
        }

        values = LiquidPositionService.current_market_values(holdings_tree)

        self.assertEqual(values["liquid"]["daj"], Decimal("1500000"))
        self.assertEqual(values["arbitrage"]["daj"], Decimal("500000"))
        self.assertEqual(values["arbitrage"]["djt"], Decimal("2500000"))
        self.assertEqual(values["liquid"]["djt"], Decimal("0"))

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

    def test_negative_amounts_are_accepted_and_calculated(self):
        document = {
            "bank": {"daj": "-1.5", "djt": "0"},
            "commitments": [
                {"id": "c1", "label": "Credit adjustment", "daj": "-2", "djt": "0"},
            ],
            "deployment": [
                {"id": "d1", "label": "Reversal", "daj": "0", "djt": "-3"},
            ],
        }
        market_values = {
            "liquid": {"daj": Decimal("0"), "djt": Decimal("0")},
            "arbitrage": {"daj": Decimal("0"), "djt": Decimal("0")},
        }

        result = LiquidPositionService.calculate_snapshot(market_values, document)

        self.assertEqual(result["bank"]["daj"], -1.5)
        self.assertEqual(result["net_available_amount"]["daj"], 0.5)
        self.assertEqual(result["total_deployment_plan"]["djt"], -3.0)
        self.assertEqual(result["balance"]["total"], 3.5)

    def test_non_finite_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            LiquidPositionService.normalize_document({
                "bank": {"daj": "NaN", "djt": "0"},
                "commitments": [],
                "deployment": [],
            })
