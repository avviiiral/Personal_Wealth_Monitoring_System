from datetime import date
import math


class XIRRCalculator:
    """
    Calculate XIRR for irregular cash flows.

    Positive cash flow:
        Money received by the investor.

    Negative cash flow:
        Money invested by the investor.

    Uses Newton-Raphson with a bisection fallback.
    No external scipy dependency is required.
    """

    @staticmethod
    def _year_fraction(
        start_date: date,
        current_date: date,
    ) -> float:
        return (
            current_date - start_date
        ).days / 365.0

    @staticmethod
    def _npv(
        rate: float,
        cash_flows,
    ) -> float:

        if rate <= -1:
            return float("inf")

        start_date = cash_flows[0][0]

        total = 0.0

        for flow_date, amount in cash_flows:

            years = (
                XIRRCalculator
                ._year_fraction(
                    start_date,
                    flow_date,
                )
            )

            total += (
                float(amount)
                / ((1.0 + rate) ** years)
            )

        return total

    @staticmethod
    def calculate(
        cash_flows,
        guess: float = 0.10,
    ):
        """
        Return XIRR as a percentage.

        Example:
            12.45 means 12.45%.

        Returns None if a valid XIRR
        cannot be calculated.
        """

        if not cash_flows:
            return None

        if len(cash_flows) < 2:
            return None

        cash_flows = sorted(
            cash_flows,
            key=lambda item: item[0],
        )

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

        # --------------------------------------------------
        # Newton-Raphson
        # --------------------------------------------------

        rate = float(guess)

        for _ in range(100):

            start_date = cash_flows[0][0]

            npv = 0.0
            derivative = 0.0

            for flow_date, amount in cash_flows:

                years = (
                    XIRRCalculator
                    ._year_fraction(
                        start_date,
                        flow_date,
                    )
                )

                base = 1.0 + rate

                if base <= 0:
                    break

                denominator = (
                    base ** years
                )

                npv += (
                    float(amount)
                    / denominator
                )

                derivative -= (
                    years
                    * float(amount)
                    / (
                        base
                        ** (years + 1)
                    )
                )

            else:

                if abs(npv) < 1e-8:
                    return round(
                        rate * 100,
                        2,
                    )

                if derivative != 0:

                    new_rate = (
                        rate
                        - npv / derivative
                    )

                    if (
                        not math.isnan(new_rate)
                        and not math.isinf(new_rate)
                        and new_rate > -0.999999
                        and new_rate <= 1000000
                    ):

                        if abs(
                            new_rate - rate
                        ) < 1e-10:

                            return round(
                                new_rate * 100,
                                2,
                            )

                        rate = new_rate
                        continue

            break

        # --------------------------------------------------
        # Bisection fallback
        # --------------------------------------------------

        lower = -0.9999
        upper = 10.0

        lower_npv = (
            XIRRCalculator
            ._npv(
                lower,
                cash_flows,
            )
        )

        upper_npv = (
            XIRRCalculator
            ._npv(
                upper,
                cash_flows,
            )
        )

        expansion_count = 0

        while (
            lower_npv * upper_npv > 0
            and expansion_count < 20
        ):

            upper *= 2

            upper_npv = (
                XIRRCalculator
                ._npv(
                    upper,
                    cash_flows,
                )
            )

            expansion_count += 1

        if lower_npv * upper_npv > 0:
            return None

        for _ in range(200):

            middle = (
                lower + upper
            ) / 2.0

            middle_npv = (
                XIRRCalculator
                ._npv(
                    middle,
                    cash_flows,
                )
            )

            if abs(middle_npv) < 1e-8:

                return round(
                    middle * 100,
                    2,
                )

            if lower_npv * middle_npv <= 0:

                upper = middle
                upper_npv = middle_npv

            else:

                lower = middle
                lower_npv = middle_npv

        return round(
            ((lower + upper) / 2.0) * 100,
            2,
        )