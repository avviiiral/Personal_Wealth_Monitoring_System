"""Independent checks of the Decimal entitlement calculator (no database)."""
from decimal import Decimal

from django.test import SimpleTestCase

from .entitlements import (
    ROLE_RESULT,
    ROLE_SOURCE,
    STATUS_MANUAL,
    STATUS_NOT_ELIGIBLE,
    STATUS_OK,
    LegInput,
    Terms,
    allocate_whole_shares,
    calculate_entitlement,
)

D = Decimal


def leg(quantity, cost, key="1"):
    return LegInput(key=key, quantity=D(str(quantity)), cost=D(str(cost)))


def entries_of(result, transaction_type=None, role=None):
    return [
        entry for entry in result.entries
        if (transaction_type is None or entry.transaction_type == transaction_type)
        and (role is None or entry.role == role)
    ]


class BonusTests(SimpleTestCase):
    def test_bonus_one_to_two_issues_two_hundred_on_one_hundred(self):
        result = calculate_entitlement(Terms("BONUS", old_units=D("1"), new_units=D("2")), [leg(100, 1000)])
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(result.quantity_delta, D("200"))
        self.assertEqual(result.resulting_quantity, D("300"))
        bonus = entries_of(result, "BONUS")
        self.assertEqual(len(bonus), 1)
        self.assertEqual(bonus[0].quantity, D("200"))
        self.assertEqual(bonus[0].amount, D("0"))
        # Acquisition cost does not increase because bonus shares were issued.
        self.assertEqual(result.resulting_cost_basis, D("1000"))

    def test_fractional_bonus_books_whole_shares_and_reports_the_fraction(self):
        result = calculate_entitlement(Terms("BONUS", old_units=D("2"), new_units=D("1")), [leg(5, 500)])
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(entries_of(result, "BONUS")[0].quantity, D("2"))
        self.assertEqual(result.fractional_entitlement, D("0.5"))
        self.assertTrue(result.cash_in_lieu_pending)
        self.assertIn("cash in lieu", result.explanation)

    def test_only_fractional_bonus_goes_to_manual_review(self):
        result = calculate_entitlement(Terms("BONUS", old_units=D("2"), new_units=D("1")), [leg(1, 100)])
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertEqual(result.fractional_entitlement, D("0.5"))

    def test_bonus_without_ratio_is_never_guessed(self):
        result = calculate_entitlement(Terms("BONUS"), [leg(100, 1000)])
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertEqual(result.entries, [])

    def test_zero_holding_is_not_eligible(self):
        result = calculate_entitlement(Terms("BONUS", old_units=D("1"), new_units=D("1")), [leg(0, 0)])
        self.assertEqual(result.status, STATUS_NOT_ELIGIBLE)


class SplitTests(SimpleTestCase):
    def test_split_doubles_quantity_and_keeps_cost(self):
        result = calculate_entitlement(Terms("STOCK_SPLIT", old_units=D("1"), new_units=D("2")), [leg(100, 1000)])
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(result.quantity_delta, D("100"))
        self.assertEqual(result.resulting_quantity, D("200"))
        self.assertEqual(entries_of(result, "SPLIT")[0].quantity, D("100"))
        self.assertEqual(result.resulting_cost_basis, D("1000"))

    def test_reverse_split_is_a_negative_adjustment(self):
        result = calculate_entitlement(Terms("REVERSE_SPLIT", old_units=D("10"), new_units=D("1")), [leg(1000, 50000)])
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(result.quantity_delta, D("-900"))
        self.assertEqual(result.resulting_quantity, D("100"))
        self.assertEqual(entries_of(result, "SPLIT")[0].quantity, D("-900"))
        self.assertIn("average cost 50 → 500", result.explanation)

    def test_reverse_split_fraction_without_cash_in_lieu_price_needs_review(self):
        result = calculate_entitlement(Terms("REVERSE_SPLIT", old_units=D("10"), new_units=D("1")), [leg(1005, 1005)])
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertEqual(result.fractional_entitlement, D("0.5"))
        self.assertIn("cash-in-lieu price", result.reason)

    def test_reverse_split_fraction_with_cash_in_lieu_price_books_sale(self):
        result = calculate_entitlement(
            Terms("REVERSE_SPLIT", old_units=D("10"), new_units=D("1"), cash_in_lieu_price=D("120")),
            [leg(1005, 100500)],
        )
        self.assertEqual(result.status, STATUS_OK)
        split = entries_of(result, "SPLIT")[0]
        sale = entries_of(result, "SELL")[0]
        self.assertEqual(split.quantity, D("-904.5"))
        self.assertEqual(sale.quantity, D("0.5"))
        self.assertEqual(sale.amount, D("60.0000"))
        self.assertEqual(result.resulting_quantity, D("100"))

    def test_ratio_that_contradicts_the_action_type_needs_review(self):
        self.assertEqual(calculate_entitlement(
            Terms("STOCK_SPLIT", old_units=D("2"), new_units=D("1")), [leg(10, 10)]).status, STATUS_MANUAL)
        self.assertEqual(calculate_entitlement(
            Terms("REVERSE_SPLIT", old_units=D("1"), new_units=D("2")), [leg(10, 10)]).status, STATUS_MANUAL)

    def test_whole_shares_are_allocated_by_largest_remainder_across_legs(self):
        # 3 + 3 shares, 2 -> 1 consolidation: the position gets 3 whole shares.
        result = calculate_entitlement(
            Terms("REVERSE_SPLIT", old_units=D("2"), new_units=D("1")),
            [leg(3, 30, key="a"), leg(3, 30, key="b")],
        )
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(result.resulting_quantity, D("3"))
        self.assertEqual(sum(e.quantity for e in result.entries), D("-3"))
        self.assertEqual(allocate_whole_shares({"a": D("1.5"), "b": D("1.5")}, D("3")), {"a": D("2"), "b": D("1")})


class DividendTests(SimpleTestCase):
    def test_dividend_amount_is_quantity_times_dps_and_quantity_unchanged(self):
        result = calculate_entitlement(Terms("CASH_DIVIDEND", cash_per_unit=D("4.35")), [leg(123, 9999)])
        self.assertEqual(result.status, STATUS_OK)
        dividend = entries_of(result, "DIVIDEND")[0]
        self.assertEqual(dividend.amount, D("535.0500"))
        self.assertEqual(dividend.quantity, D("123"))
        self.assertEqual(result.quantity_delta, D("0"))
        self.assertEqual(result.resulting_quantity, D("123"))

    def test_dividend_without_dps_needs_review(self):
        self.assertEqual(calculate_entitlement(Terms("CASH_DIVIDEND"), [leg(10, 10)]).status, STATUS_MANUAL)


class MergerTests(SimpleTestCase):
    def test_share_for_share_merger_carries_full_cost(self):
        result = calculate_entitlement(
            Terms("MERGER", old_units=D("5"), new_units=D("2"), resulting_isin="INE000B01011",
                  resulting_security_name="Successor Ltd"),
            [leg(100, 7000)],
        )
        self.assertEqual(result.status, STATUS_OK)
        sell = entries_of(result, "SELL", ROLE_SOURCE)[0]
        buy = entries_of(result, "BUY", ROLE_RESULT)[0]
        self.assertEqual(sell.quantity, D("100"))
        self.assertEqual(sell.amount, D("7000.0000"))
        self.assertEqual(buy.quantity, D("40"))
        self.assertEqual(buy.amount, D("7000.0000"))
        self.assertEqual(result.resulting_quantity, D("0"))
        self.assertEqual(result.new_security_quantity, D("40"))

    def test_merger_with_cash_needs_official_cost_allocation(self):
        result = calculate_entitlement(
            Terms("MERGER", old_units=D("1"), new_units=D("1"), cash_per_unit=D("10"), resulting_isin="INE000B01011"),
            [leg(100, 5000)],
        )
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn("cost allocation", result.reason)

    def test_merger_with_cash_and_allocation_releases_the_rest_of_the_cost(self):
        result = calculate_entitlement(
            Terms("MERGER", old_units=D("1"), new_units=D("1"), cash_per_unit=D("10"),
                  cost_allocation_ratio=D("0.8"), resulting_isin="INE000B01011"),
            [leg(100, 5000)],
        )
        self.assertEqual(result.status, STATUS_OK)
        sell = entries_of(result, "SELL", ROLE_SOURCE)[0]
        buy = entries_of(result, "BUY", ROLE_RESULT)[0]
        self.assertEqual(buy.amount, D("4000.0000"))
        # Proceeds = carried cost + cash, so realized gain = cash - released cost.
        self.assertEqual(sell.amount, D("5000.0000"))
        self.assertEqual(result.cash_amount, D("1000.0000"))

    def test_merger_without_successor_isin_needs_review(self):
        result = calculate_entitlement(Terms("MERGER", old_units=D("1"), new_units=D("1")), [leg(10, 10)])
        self.assertEqual(result.status, STATUS_MANUAL)

    def test_merger_fraction_without_cash_in_lieu_needs_review(self):
        result = calculate_entitlement(
            Terms("MERGER", old_units=D("3"), new_units=D("1"), resulting_isin="INE000B01011"), [leg(10, 10)])
        self.assertEqual(result.status, STATUS_MANUAL)


class DemergerTests(SimpleTestCase):
    def test_demerger_needs_official_cost_allocation(self):
        result = calculate_entitlement(
            Terms("DEMERGER", old_units=D("1"), new_units=D("1"), resulting_isin="INE000C01011"), [leg(100, 10000)])
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertIn("cost allocation", result.reason)

    def test_demerger_allocates_without_duplicating_cost(self):
        result = calculate_entitlement(
            Terms("DEMERGER", old_units=D("2"), new_units=D("1"), cost_allocation_ratio=D("0.25"),
                  resulting_isin="INE000C01011", resulting_security_name="Spinco Ltd"),
            [leg(100, 10000)],
        )
        self.assertEqual(result.status, STATUS_OK)
        source_sell = entries_of(result, "SELL", ROLE_SOURCE)[0]
        source_buy = entries_of(result, "BUY", ROLE_SOURCE)[0]
        child_buy = entries_of(result, "BUY", ROLE_RESULT)[0]
        self.assertEqual(source_sell.amount, D("10000.0000"))
        self.assertEqual(source_buy.amount, D("7500.0000"))
        self.assertEqual(child_buy.amount, D("2500.0000"))
        self.assertEqual(child_buy.quantity, D("50"))
        # Total cost after the event equals the cost before it.
        self.assertEqual(source_buy.amount + child_buy.amount, source_sell.amount)
        self.assertEqual(result.resulting_quantity, D("100"))


class EmptyAndNullInputTests(SimpleTestCase):
    def test_empty_legs_list_is_not_eligible_and_books_nothing(self):
        for action_type in ("BONUS", "STOCK_SPLIT", "REVERSE_SPLIT", "CASH_DIVIDEND", "MERGER", "DEMERGER"):
            with self.subTest(action_type=action_type):
                result = calculate_entitlement(
                    Terms(action_type, old_units=D("1"), new_units=D("2"), cash_per_unit=D("1"),
                          cost_allocation_ratio=D("0.5"), resulting_isin="INE000C01011"),
                    [],
                )
                self.assertEqual(result.status, STATUS_NOT_ELIGIBLE)
                self.assertEqual(result.entries, [])
                self.assertEqual(result.eligible_quantity, D("0"))

    def test_empty_legs_for_a_manual_only_type_stays_manual(self):
        result = calculate_entitlement(Terms("RIGHTS_ISSUE"), [])
        self.assertEqual(result.status, STATUS_MANUAL)
        self.assertEqual(result.entries, [])

    def test_leg_with_none_quantity_is_ignored(self):
        legs = [LegInput(key="none", quantity=None, cost=D("0")), leg(10, 100, key="real")]
        result = calculate_entitlement(Terms("STOCK_SPLIT", old_units=D("1"), new_units=D("2")), legs)
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual([entry.leg_key for entry in result.entries], ["real"])
        self.assertEqual(result.eligible_quantity, D("10"))

    def test_only_none_quantity_legs_are_not_eligible(self):
        result = calculate_entitlement(
            Terms("BONUS", old_units=D("1"), new_units=D("1")), [LegInput(key="x", quantity=None, cost=D("0"))])
        self.assertEqual(result.status, STATUS_NOT_ELIGIBLE)

    def test_one_missing_side_of_the_ratio_is_never_guessed(self):
        for old_units, new_units in ((None, D("2")), (D("1"), None), (None, None)):
            for action_type in ("BONUS", "STOCK_SPLIT", "REVERSE_SPLIT", "MERGER", "DEMERGER"):
                with self.subTest(action_type=action_type, old=old_units, new=new_units):
                    result = calculate_entitlement(
                        Terms(action_type, old_units=old_units, new_units=new_units,
                              resulting_isin="INE000C01011", cost_allocation_ratio=D("0.5")),
                        [leg(10, 100)],
                    )
                    self.assertEqual(result.status, STATUS_MANUAL)
                    self.assertEqual(result.entries, [])

    def test_zero_or_negative_ratio_is_rejected(self):
        for old_units, new_units in ((D("0"), D("1")), (D("1"), D("0")), (D("-1"), D("2"))):
            with self.subTest(old=old_units, new=new_units):
                result = calculate_entitlement(
                    Terms("BONUS", old_units=old_units, new_units=new_units), [leg(10, 100)])
                self.assertEqual(result.status, STATUS_MANUAL)

    def test_zero_dividend_per_share_needs_review(self):
        result = calculate_entitlement(Terms("CASH_DIVIDEND", cash_per_unit=D("0")), [leg(10, 100)])
        self.assertEqual(result.status, STATUS_MANUAL)

    def test_none_cash_in_lieu_price_leaves_bonus_fraction_pending_without_cash_amount(self):
        result = calculate_entitlement(
            Terms("BONUS", old_units=D("2"), new_units=D("1"), cash_in_lieu_price=None), [leg(3, 30)])
        self.assertEqual(result.status, STATUS_OK)
        self.assertTrue(result.cash_in_lieu_pending)
        self.assertIsNone(result.cash_amount)

    def test_unknown_or_blank_action_type_needs_review(self):
        for action_type in ("", "SOMETHING_NEW"):
            with self.subTest(action_type=action_type):
                result = calculate_entitlement(Terms(action_type), [leg(10, 100)])
                self.assertEqual(result.status, STATUS_MANUAL)

    def test_terms_from_action_with_none_fields(self):
        class Bare:
            action_type = "STOCK_SPLIT"
            old_units = None
            new_units = None
            cash_per_unit = None
            resulting_isin = None
            resulting_symbol = None

        terms = Terms.from_action(Bare())
        self.assertIsNone(terms.cost_allocation_ratio)
        self.assertIsNone(terms.cash_in_lieu_price)
        self.assertEqual(terms.resulting_isin, "")
        self.assertEqual(terms.ratio_text, "")
        self.assertEqual(calculate_entitlement(terms, [leg(10, 100)]).status, STATUS_MANUAL)


class OtherTypeTests(SimpleTestCase):
    def test_types_without_a_safe_automatic_treatment_go_to_manual_review(self):
        # Buyback and tender offer now have their own workflow (test_auto_terms_and_buyback).
        for action_type in ("RIGHTS_ISSUE", "WARRANT_CONVERSION", "DELISTING",
                            "TICKER_CHANGE", "ISIN_CHANGE", "CASH_IN_LIEU", "MF_DISTRIBUTION", "MF_UNIT_ADJUSTMENT"):
            with self.subTest(action_type=action_type):
                result = calculate_entitlement(Terms(action_type, old_units=D("1"), new_units=D("1")), [leg(10, 10)])
                self.assertEqual(result.status, STATUS_MANUAL)
                self.assertEqual(result.entries, [])
