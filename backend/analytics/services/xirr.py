from datetime import date
from decimal import Decimal


class XIRRCalculator:
    """
    Calculate XIRR using the Newton-Raphson method with
    a bisection fallback.

    Cash flows:
        Negative = investment/outflow
        Positive = withdrawal/value/inflow
    """

    @staticmethod
    def _npv(rate, cash_flows):
        """
        Calculate the NPV of dated cash flows for a given rate.
        """

        first_date = cash_flows[0][0]

        total = 0.0

        for flow_date, amount in cash_flows:

            days = (
                flow_date - first_date
            ).days

            total += float(amount) / (
                (1 + rate) ** (days / 365.0)
            )

        return total

    @staticmethod
    def _derivative(rate, cash_flows):
        """
        Derivative of the XIRR NPV equation.
        """

        first_date = cash_flows[0][0]

        total = 0.0

        for flow_date, amount in cash_flows:

            days = (
                flow_date - first_date
            ).days

            years = days / 365.0

            if years == 0:
                continue

            total -= (
                years
                * float(amount)
                / ((1 + rate) ** (years + 1))
            )

        return total

    @staticmethod
    def calculate(
        cash_flows,
        guess=0.10,
        tolerance=1e-7,
        max_iterations=100,
    ):
        """
        Calculate annualized XIRR.

        cash_flows format:

        [
            (date(2026, 1, 1), Decimal("-10000")),
            (date(2026, 2, 1), Decimal("-5000")),
            (date(2026, 8, 10), Decimal("17000")),
        ]
        """

        if not cash_flows:
            return None

        if len(cash_flows) < 2:
            return None

        has_positive = any(
            amount > 0
            for _, amount in cash_flows
        )

        has_negative = any(
            amount < 0
            for _, amount in cash_flows
        )

        if not has_positive or not has_negative:
            return None

        cash_flows = sorted(
            cash_flows,
            key=lambda item: item[0],
        )

        # --------------------------------------------------
        # Newton-Raphson
        # --------------------------------------------------

        rate = float(guess)

        for _ in range(max_iterations):

            if rate <= -0.999999:
                rate = -0.9999

            npv = XIRRCalculator._npv(
                rate,
                cash_flows,
            )

            derivative = XIRRCalculator._derivative(
                rate,
                cash_flows,
            )

            if abs(npv) < tolerance:
                return rate

            if abs(derivative) < 1e-12:
                break

            new_rate = (
                rate
                - npv / derivative
            )

            if (
                new_rate <= -0.999999
                or new_rate > 1_000_000
            ):
                break

            if abs(new_rate - rate) < tolerance:
                return new_rate

            rate = new_rate

        # --------------------------------------------------
        # Bisection fallback
        # --------------------------------------------------

        low = -0.9999
        high = 10.0

        npv_low = XIRRCalculator._npv(
            low,
            cash_flows,
        )

        npv_high = XIRRCalculator._npv(
            high,
            cash_flows,
        )

        # Expand upper bound if necessary.
        for _ in range(20):

            if npv_low * npv_high <= 0:
                break

            high *= 2

            npv_high = XIRRCalculator._npv(
                high,
                cash_flows,
            )

        if npv_low * npv_high > 0:
            return None

        for _ in range(max_iterations):

            middle = (
                low + high
            ) / 2

            npv_middle = XIRRCalculator._npv(
                middle,
                cash_flows,
            )

            if abs(npv_middle) < tolerance:
                return middle

            if npv_low * npv_middle < 0:

                high = middle
                npv_high = npv_middle

            else:

                low = middle
                npv_low = npv_middle

        return (
            low + high
        ) / 2