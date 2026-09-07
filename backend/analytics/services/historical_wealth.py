from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from investments.models import (
    Asset,
    Transaction,
    TransactionType,
)
from market_data.models import ManualAssetPrice, MarketPrice
from mutual_funds.models import (
    MutualFundNAV,
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
)


class HistoricalWealthAnalytics:
    """
    Historical unified wealth analytics.

    Optimized implementation:
        - Loads transactions in bulk.
        - Loads historical prices/NAVs in bulk.
        - Calculates positions in memory.
        - Avoids database queries inside the daily/holding loops.

    The existing API response structure is preserved.
    """

    ZERO = Decimal("0")

    @staticmethod
    def _owner_ids(user):
        """
        Normalize `user` to a list of owner ids to filter by.

        Accepts either a single User instance (existing,
        single-owner behavior - unchanged) or an iterable of user
        ids, for combining data across a shared-visibility group
        (see users.permissions.get_visible_owner_ids).
        """

        return [user.pk] if hasattr(user, "pk") else list(user)

    # ==========================================================
    # EQUITY TRANSACTION HELPER
    # ==========================================================

    @staticmethod
    def _apply_equity_transaction(
        position,
        transaction,
    ):
        quantity = (
            transaction.quantity
            or HistoricalWealthAnalytics.ZERO
        )

        amount = (
            transaction.amount
            or HistoricalWealthAnalytics.ZERO
        )

        if transaction.transaction_type in (
            TransactionType.BUY,
            TransactionType.SIP,
        ):
            position["quantity"] += quantity

            # IMPORTANT:
            # Invested value intentionally excludes fees, matching
            # HoldingCalculationEngine.calculate_position() (the
            # engine behind Holding.invested_value / the "All
            # Families" figures) - so both agree on what "invested
            # value" means instead of a Family filter shifting the
            # number purely from a fee-accounting difference.
            position["invested_value"] += amount

        elif transaction.transaction_type == TransactionType.SELL:
            if (
                position["quantity"] <= 0
                or quantity <= 0
            ):
                return

            average_cost = (
                position["invested_value"]
                / position["quantity"]
            )

            cost_of_sale = (
                average_cost * quantity
            )

            position["quantity"] -= quantity
            position["invested_value"] -= cost_of_sale

            if position["quantity"] <= 0:
                position["quantity"] = (
                    HistoricalWealthAnalytics.ZERO
                )
                position["invested_value"] = (
                    HistoricalWealthAnalytics.ZERO
                )

    # ==========================================================
    # MUTUAL FUND TRANSACTION HELPER
    # ==========================================================

    @staticmethod
    def _apply_mutual_fund_transaction(
        position,
        transaction,
    ):
        units = (
            transaction.units
            or HistoricalWealthAnalytics.ZERO
        )

        amount = (
            transaction.amount
            or HistoricalWealthAnalytics.ZERO
        )

        if transaction.transaction_type in (
            MutualFundTransactionType.PURCHASE,
            MutualFundTransactionType.SIP,
        ):
            position["units"] += units

            # Excludes fees - see the matching comment in
            # _apply_equity_transaction above.
            position["invested_value"] += amount

        elif transaction.transaction_type == (
            MutualFundTransactionType.REDEMPTION
        ):
            if (
                position["units"] <= 0
                or units <= 0
            ):
                return

            average_cost = (
                position["invested_value"]
                / position["units"]
            )

            cost_of_redemption = (
                average_cost * units
            )

            position["units"] -= units
            position["invested_value"] -= (
                cost_of_redemption
            )

            if position["units"] <= 0:
                position["units"] = (
                    HistoricalWealthAnalytics.ZERO
                )
                position["invested_value"] = (
                    HistoricalWealthAnalytics.ZERO
                )

    # ==========================================================
    # EQUITY PRICE MAP
    # ==========================================================

    @staticmethod
    def _build_price_map(
        assets,
        start_date,
        end_date,
    ):
        """
        Load historical equity prices needed for the
        requested date range.

        Prices are sorted by asset/date so that the
        latest available price can be maintained in memory.
        """

        asset_ids = [
            asset.pk
            for asset in assets
        ]

        if not asset_ids:
            return {}

        prices_by_asset = defaultdict(list)

        # ------------------------------------------------------
        # Prices inside requested range
        # ------------------------------------------------------

        prices = (
            MarketPrice.objects
            .filter(
                asset_id__in=asset_ids,
                date__gte=start_date,
                date__lte=end_date,
            )
            .order_by(
                "asset_id",
                "date",
                "id",
            )
            .only(
                "asset_id",
                "date",
                "close_price",
            )
        )

        for price in prices:
            prices_by_asset[
                price.asset_id
            ].append(
                (
                    price.date,
                    price.close_price,
                )
            )

        # ------------------------------------------------------
        # Latest price before requested range
        # ------------------------------------------------------

        for asset in assets:

            previous_price = (
                MarketPrice.objects
                .filter(
                    asset_id=asset.pk,
                    date__lt=start_date,
                )
                .order_by(
                    "-date",
                    "-id",
                )
                .only(
                    "date",
                    "close_price",
                )
                .first()
            )

            if previous_price is not None:
                prices_by_asset[
                    asset.pk
                ].insert(
                    0,
                    (
                        previous_price.date,
                        previous_price.close_price,
                    ),
                )

        # ------------------------------------------------------
        # MANUAL OVERRIDE (ManualAssetPrice)
        #
        # IMPORTANT:
        # This is a SEPARATE table from MarketPrice(source=MANUAL) -
        # used for assets where automatic market data is
        # unavailable (AIFs, PMS, unlisted, etc). It holds exactly
        # one row per asset, no history, and
        # PortfolioMetricsService.get_current_price() - the price
        # source behind Holding / the "All Families" figures -
        # already gives it unconditional top priority over
        # MarketPrice, regardless of date.
        #
        # To keep this calculation consistent with that, any asset
        # with a ManualAssetPrice entry gets its MarketPrice series
        # REPLACED with that single price. _get_value_for_date()
        # already falls back to a single point's value for every
        # date (before OR after it), so this one override price is
        # used across the entire requested range - the same
        # date-agnostic behavior get_current_price() has.
        # ------------------------------------------------------

        manual_prices = (
            ManualAssetPrice.objects
            .filter(
                asset_id__in=asset_ids,
            )
            .only(
                "asset_id",
                "price",
                "price_date",
            )
        )

        for manual_price in manual_prices:
            prices_by_asset[manual_price.asset_id] = [
                (
                    manual_price.price_date,
                    manual_price.price,
                )
            ]

        return dict(prices_by_asset)

    # ==========================================================
    # MUTUAL FUND NAV MAP
    # ==========================================================

    @staticmethod
    def _build_nav_map(
        schemes,
        start_date,
        end_date,
    ):
        """
        Load historical mutual-fund NAVs needed for the
        requested date range.
        """

        scheme_ids = [
            scheme.pk
            for scheme in schemes
        ]

        if not scheme_ids:
            return {}

        navs_by_scheme = defaultdict(list)

        # ------------------------------------------------------
        # NAVs inside requested range
        # ------------------------------------------------------

        navs = (
            MutualFundNAV.objects
            .filter(
                scheme_id__in=scheme_ids,
                date__gte=start_date,
                date__lte=end_date,
            )
            .order_by(
                "scheme_id",
                "date",
                "id",
            )
            .only(
                "scheme_id",
                "date",
                "nav",
            )
        )

        for nav in navs:
            navs_by_scheme[
                nav.scheme_id
            ].append(
                (
                    nav.date,
                    nav.nav,
                )
            )

        # ------------------------------------------------------
        # Latest NAV before requested range
        # ------------------------------------------------------

        for scheme in schemes:

            previous_nav = (
                MutualFundNAV.objects
                .filter(
                    scheme_id=scheme.pk,
                    date__lt=start_date,
                )
                .order_by(
                    "-date",
                    "-id",
                )
                .only(
                    "date",
                    "nav",
                )
                .first()
            )

            if previous_nav is not None:
                navs_by_scheme[
                    scheme.pk
                ].insert(
                    0,
                    (
                        previous_nav.date,
                        previous_nav.nav,
                    ),
                )

        return dict(navs_by_scheme)

    # ==========================================================
    # LATEST VALUE FROM SORTED HISTORY
    # ==========================================================

    @staticmethod
    def _get_value_for_date(
        values,
        target_date,
        pointer,
    ):
        """
        Return the latest value on or before target_date.

        values:
            [(date, value), ...]

        pointer:
            Current index in the sorted list.

        Returns:
            (value, updated_pointer)

        IMPORTANT:

        When target_date is BEFORE the earliest known value
        (pointer stays at -1), fall back to that earliest known
        value instead of returning None.

        This matters for manually-priced holdings (AIF, PMS,
        Commodity ETFs, etc.) where MarketPrice only ever stores
        ONE snapshot dated to whenever the price was last edited -
        older manual snapshots are deleted on every update. Without
        this fallback, every day before that single snapshot's date
        has no price at all, so the asset is silently valued at ₹0
        for that stretch even though invested_value already counts
        it in full - producing a false "sudden jump" on the chart
        the moment the snapshot date is reached, rather than an
        actual portfolio change.

        Using the earliest known price as a flat estimate for
        earlier days is the best available data point - it reflects
        an actual entered/observed price, not a fabricated one.
        """

        if not values:
            return None, pointer

        while (
            pointer + 1 < len(values)
            and values[pointer + 1][0] <= target_date
        ):
            pointer += 1

        if (
            pointer >= 0
            and values[pointer][0] <= target_date
        ):
            return (
                values[pointer][1],
                pointer,
            )

        # Before the earliest known value: use it as the best
        # available estimate rather than treating it as unknown.
        return (
            values[0][1],
            pointer,
        )

    # ==========================================================
    # LEGACY EQUITY POSITION
    # ==========================================================

    @staticmethod
    def calculate_equity_position_as_of(
        user,
        asset,
        target_date,
    ):
        """
        Calculate an equity position as it existed
        on target_date.

        Uses average-cost methodology.

        Kept for compatibility with existing callers/tests.
        """

        transactions = (
            Transaction.objects
            .filter(
                owner=user,
                asset=asset,
                transaction_date__lte=target_date,
            )
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        position = {
            "quantity": (
                HistoricalWealthAnalytics.ZERO
            ),
            "invested_value": (
                HistoricalWealthAnalytics.ZERO
            ),
        }

        for transaction in transactions:

            HistoricalWealthAnalytics._apply_equity_transaction(
                position,
                transaction,
            )

        return position

    # ==========================================================
    # LEGACY EQUITY PRICE
    # ==========================================================

    @staticmethod
    def get_equity_price_as_of(
        asset,
        target_date,
    ):
        """
        Get latest available equity market price
        on or before target_date.

        Kept for compatibility with existing callers/tests.
        """

        price = (
            MarketPrice.objects
            .filter(
                asset=asset,
                date__lte=target_date,
            )
            .order_by(
                "-date",
                "-id",
            )
            .first()
        )

        return price

    # ==========================================================
    # LEGACY MUTUAL FUND POSITION
    # ==========================================================

    @staticmethod
    def calculate_mutual_fund_position_as_of(
        user,
        scheme,
        target_date,
    ):
        """
        Calculate a mutual-fund position as it existed
        on target_date.

        Uses average-cost methodology.

        Kept for compatibility with existing callers/tests.
        """

        transactions = (
            MutualFundTransaction.objects
            .filter(
                owner=user,
                scheme=scheme,
                transaction_date__lte=target_date,
            )
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        position = {
            "units": (
                HistoricalWealthAnalytics.ZERO
            ),
            "invested_value": (
                HistoricalWealthAnalytics.ZERO
            ),
        }

        for transaction in transactions:

            HistoricalWealthAnalytics._apply_mutual_fund_transaction(
                position,
                transaction,
            )

        return position

    # ==========================================================
    # LEGACY MUTUAL FUND NAV
    # ==========================================================

    @staticmethod
    def get_mutual_fund_nav_as_of(
        scheme,
        target_date,
    ):
        """
        Get latest available mutual-fund NAV
        on or before target_date.

        Kept for compatibility with existing callers/tests.
        """

        nav = (
            MutualFundNAV.objects
            .filter(
                scheme=scheme,
                date__lte=target_date,
            )
            .order_by(
                "-date",
                "-id",
            )
            .first()
        )

        return nav

    # ==========================================================
    # SINGLE-DATE HISTORICAL VALUE
    # ==========================================================

    @staticmethod
    def calculate_historical_value(
        user,
        target_date,
    ):
        """
        Calculate complete unified portfolio value
        for one historical date.

        This preserves the original single-date behavior.
        """

        total_invested = (
            HistoricalWealthAnalytics.ZERO
        )

        total_value = (
            HistoricalWealthAnalytics.ZERO
        )

        equity_invested = (
            HistoricalWealthAnalytics.ZERO
        )

        equity_value = (
            HistoricalWealthAnalytics.ZERO
        )

        mutual_fund_invested = (
            HistoricalWealthAnalytics.ZERO
        )

        mutual_fund_value = (
            HistoricalWealthAnalytics.ZERO
        )

        # ======================================================
        # EQUITIES
        # ======================================================

        equity_asset_ids = (
            Transaction.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                transaction_date__lte=target_date,
            )
            .values_list(
                "asset_id",
                flat=True,
            )
            .distinct()
        )

        assets = (
            Asset.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                is_active=True,
                id__in=equity_asset_ids,
            )
            .order_by("id")
        )

        for asset in assets:

            # Use this asset's OWN real owner, never the `user`
            # argument above - when `user` represents a
            # shared-visibility group, different assets in this
            # loop may belong to different actual owners, and the
            # position lookup below must match the transactions
            # that asset's real owner actually made.
            position = (
                HistoricalWealthAnalytics
                .calculate_equity_position_as_of(
                    asset.owner,
                    asset,
                    target_date,
                )
            )

            quantity = position["quantity"]

            invested_value = position[
                "invested_value"
            ]

            if quantity <= 0:
                continue

            price = (
                HistoricalWealthAnalytics
                .get_equity_price_as_of(
                    asset,
                    target_date,
                )
            )

            if price is None:
                continue

            current_value = (
                quantity
                * price.close_price
            )

            equity_invested += invested_value
            equity_value += current_value

        # ======================================================
        # MUTUAL FUNDS
        # ======================================================

        mutual_fund_scheme_ids = (
            MutualFundTransaction.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                transaction_date__lte=target_date,
            )
            .values_list(
                "scheme_id",
                flat=True,
            )
            .distinct()
        )

        schemes = (
            MutualFundScheme.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                is_active=True,
                id__in=mutual_fund_scheme_ids,
            )
            .order_by("id")
        )

        for scheme in schemes:

            # Same reasoning as the equity loop above: use this
            # scheme's own real owner, not the outer `user`.
            position = (
                HistoricalWealthAnalytics
                .calculate_mutual_fund_position_as_of(
                    scheme.owner,
                    scheme,
                    target_date,
                )
            )

            units = position["units"]

            invested_value = position[
                "invested_value"
            ]

            if units <= 0:
                continue

            nav = (
                HistoricalWealthAnalytics
                .get_mutual_fund_nav_as_of(
                    scheme,
                    target_date,
                )
            )

            if nav is None:
                continue

            current_value = (
                units
                * nav.nav
            )

            mutual_fund_invested += (
                invested_value
            )

            mutual_fund_value += (
                current_value
            )

        # ======================================================
        # TOTALS
        # ======================================================

        total_invested = (
            equity_invested
            + mutual_fund_invested
        )

        total_value = (
            equity_value
            + mutual_fund_value
        )

        unrealized_pnl = (
            total_value
            - total_invested
        )

        return {
            "date": target_date,
            "invested_value": total_invested,
            "portfolio_value": total_value,
            "pnl": unrealized_pnl,
            "equity": {
                "invested_value": equity_invested,
                "portfolio_value": equity_value,
                "pnl": (
                    equity_value
                    - equity_invested
                ),
            },
            "mutual_funds": {
                "invested_value": (
                    mutual_fund_invested
                ),
                "portfolio_value": (
                    mutual_fund_value
                ),
                "pnl": (
                    mutual_fund_value
                    - mutual_fund_invested
                ),
            },
        }

    # ==========================================================
    # OPTIMIZED HISTORICAL RANGE
    # ==========================================================

    @staticmethod
    def calculate_history(
        user,
        start_date,
        end_date,
        family_name=None,
    ):
        """
        Optimized historical wealth calculation.

        The previous implementation performed database
        queries for every date and every holding.

        This implementation:

            1. Loads transactions once.
            2. Loads active assets/schemes once.
            3. Loads prices/NAVs once.
            4. Builds positions in memory.
            5. Calculates daily values in memory.

        The API response structure remains unchanged.

        family_name:
            Optional. When provided, scopes both the equity and
            mutual-fund transaction querysets to that exact Family
            Name (Transaction.family_name / MutualFundTransaction.
            family_name), so every downstream step - active
            assets/schemes, prices/NAVs, positions, daily totals -
            naturally narrows to that family. Leaving it unset
            preserves the original all-families calculation exactly.
        """

        if start_date > end_date:
            raise ValueError(
                "start_date cannot be after end_date"
            )

        # ======================================================
        # EQUITY TRANSACTIONS
        # ======================================================

        equity_transactions_qs = (
            Transaction.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                transaction_date__lte=end_date,
            )
        )

        if family_name:
            equity_transactions_qs = (
                equity_transactions_qs
                .filter(family_name=family_name)
            )

        equity_transactions = (
            equity_transactions_qs
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        equity_transactions_by_date = (
            defaultdict(list)
        )

        equity_asset_ids = set()

        for transaction in equity_transactions:

            equity_asset_ids.add(
                transaction.asset_id
            )

            equity_transactions_by_date[
                transaction.transaction_date
            ].append(transaction)

        # ======================================================
        # ACTIVE EQUITY ASSETS
        # ======================================================

        assets = list(
            Asset.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                is_active=True,
                id__in=equity_asset_ids,
            )
            .order_by("id")
        )

        active_asset_ids = {
            asset.pk
            for asset in assets
        }

        # Only retain transactions belonging to
        # active assets.
        filtered_equity_transactions_by_date = (
            defaultdict(list)
        )

        for (
            transaction_date,
            transactions,
        ) in equity_transactions_by_date.items():

            for transaction in transactions:

                if transaction.asset_id in active_asset_ids:
                    filtered_equity_transactions_by_date[
                        transaction_date
                    ].append(transaction)

        equity_transactions_by_date = (
            filtered_equity_transactions_by_date
        )

        # ======================================================
        # EQUITY PRICES
        # ======================================================

        prices_by_asset = (
            HistoricalWealthAnalytics
            ._build_price_map(
                assets,
                start_date,
                end_date,
            )
        )

        # ======================================================
        # MUTUAL FUND TRANSACTIONS
        # ======================================================

        mutual_fund_transactions_qs = (
            MutualFundTransaction.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                transaction_date__lte=end_date,
            )
        )

        if family_name:
            mutual_fund_transactions_qs = (
                mutual_fund_transactions_qs
                .filter(family_name=family_name)
            )

        mutual_fund_transactions = (
            mutual_fund_transactions_qs
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        mutual_fund_transactions_by_date = (
            defaultdict(list)
        )

        mutual_fund_scheme_ids = set()

        for transaction in mutual_fund_transactions:

            mutual_fund_scheme_ids.add(
                transaction.scheme_id
            )

            mutual_fund_transactions_by_date[
                transaction.transaction_date
            ].append(transaction)

        # ======================================================
        # ACTIVE MUTUAL FUND SCHEMES
        # ======================================================

        schemes = list(
            MutualFundScheme.objects
            .filter(
                owner_id__in=HistoricalWealthAnalytics._owner_ids(user),
                is_active=True,
                id__in=mutual_fund_scheme_ids,
            )
            .order_by("id")
        )

        active_scheme_ids = {
            scheme.pk
            for scheme in schemes
        }

        # ------------------------------------------------------
        # IMPORTANT:
        #
        # Only transactions belonging to active schemes are
        # retained.
        #
        # This prevents a transaction from referencing a scheme
        # that is not represented in mutual_fund_positions.
        # ------------------------------------------------------

        filtered_mutual_fund_transactions_by_date = (
            defaultdict(list)
        )

        for (
            transaction_date,
            transactions,
        ) in mutual_fund_transactions_by_date.items():

            for transaction in transactions:

                if transaction.scheme_id in active_scheme_ids:
                    filtered_mutual_fund_transactions_by_date[
                        transaction_date
                    ].append(transaction)

        mutual_fund_transactions_by_date = (
            filtered_mutual_fund_transactions_by_date
        )

        # ======================================================
        # MUTUAL FUND NAVs
        # ======================================================

        navs_by_scheme = (
            HistoricalWealthAnalytics
            ._build_nav_map(
                schemes,
                start_date,
                end_date,
            )
        )

        # ======================================================
        # INITIALIZE EQUITY POSITIONS
        # ======================================================

        equity_positions = {}

        for asset in assets:

            equity_positions[asset.pk] = {
                "quantity": (
                    HistoricalWealthAnalytics.ZERO
                ),
                "invested_value": (
                    HistoricalWealthAnalytics.ZERO
                ),
            }

        # ======================================================
        # INITIALIZE MUTUAL FUND POSITIONS
        # ======================================================

        mutual_fund_positions = {}

        for scheme in schemes:

            mutual_fund_positions[scheme.pk] = {
                "units": (
                    HistoricalWealthAnalytics.ZERO
                ),
                "invested_value": (
                    HistoricalWealthAnalytics.ZERO
                ),
            }

        # ======================================================
        # APPLY TRANSACTIONS BEFORE START DATE
        # ======================================================
        #
        # If the requested period starts after an existing
        # transaction, that transaction must already be part
        # of the opening position.
        #
        # Example:
        #
        # BUY on Jan 1
        # History starts Jan 10
        #
        # The Jan 1 BUY must already be reflected on Jan 10.
        #

        for (
            transaction_date,
            transactions,
        ) in equity_transactions_by_date.items():

            if transaction_date >= start_date:
                continue

            for transaction in transactions:

                position = equity_positions.get(
                    transaction.asset_id
                )

                if position is None:
                    continue

                HistoricalWealthAnalytics._apply_equity_transaction(
                    position,
                    transaction,
                )

        for (
            transaction_date,
            transactions,
        ) in mutual_fund_transactions_by_date.items():

            if transaction_date >= start_date:
                continue

            for transaction in transactions:

                position = mutual_fund_positions.get(
                    transaction.scheme_id
                )

                if position is None:
                    continue

                HistoricalWealthAnalytics._apply_mutual_fund_transaction(
                    position,
                    transaction,
                )

        # ======================================================
        # PRICE/NAV POINTERS
        # ======================================================

        price_pointers = {
            asset.pk: -1
            for asset in assets
        }

        nav_pointers = {
            scheme.pk: -1
            for scheme in schemes
        }

        # ======================================================
        # DAILY CALCULATION
        # ======================================================

        results = []

        current_date = start_date

        while current_date <= end_date:

            # --------------------------------------------------
            # EQUITY TRANSACTIONS FOR CURRENT DATE
            # --------------------------------------------------

            for transaction in (
                equity_transactions_by_date.get(
                    current_date,
                    [],
                )
            ):

                position = equity_positions.get(
                    transaction.asset_id
                )

                if position is None:
                    continue

                HistoricalWealthAnalytics._apply_equity_transaction(
                    position,
                    transaction,
                )

            # --------------------------------------------------
            # MUTUAL FUND TRANSACTIONS FOR CURRENT DATE
            # --------------------------------------------------

            for transaction in (
                mutual_fund_transactions_by_date.get(
                    current_date,
                    [],
                )
            ):

                # Defensive lookup.
                #
                # Even if database data contains a transaction
                # referring to a missing/inactive scheme, the
                # historical endpoint must not crash.
                position = mutual_fund_positions.get(
                    transaction.scheme_id
                )

                if position is None:
                    continue

                HistoricalWealthAnalytics._apply_mutual_fund_transaction(
                    position,
                    transaction,
                )

            # --------------------------------------------------
            # EQUITY VALUE
            # --------------------------------------------------

            equity_invested = (
                HistoricalWealthAnalytics.ZERO
            )

            equity_value = (
                HistoricalWealthAnalytics.ZERO
            )

            for asset in assets:

                position = equity_positions.get(
                    asset.pk
                )

                if position is None:
                    continue

                quantity = position["quantity"]

                if quantity <= 0:
                    continue

                equity_invested += (
                    position["invested_value"]
                )

                price_values = prices_by_asset.get(
                    asset.pk,
                    [],
                )

                price, pointer = (
                    HistoricalWealthAnalytics
                    ._get_value_for_date(
                        price_values,
                        current_date,
                        price_pointers.get(
                            asset.pk,
                            -1,
                        ),
                    )
                )

                price_pointers[asset.pk] = pointer

                if price is None:
                    continue

                equity_value += (
                    quantity * price
                )

            # --------------------------------------------------
            # MUTUAL FUND VALUE
            # --------------------------------------------------

            mutual_fund_invested = (
                HistoricalWealthAnalytics.ZERO
            )

            mutual_fund_value = (
                HistoricalWealthAnalytics.ZERO
            )

            for scheme in schemes:

                position = mutual_fund_positions.get(
                    scheme.pk
                )

                if position is None:
                    continue

                units = position["units"]

                if units <= 0:
                    continue

                mutual_fund_invested += (
                    position["invested_value"]
                )

                nav_values = navs_by_scheme.get(
                    scheme.pk,
                    [],
                )

                nav, pointer = (
                    HistoricalWealthAnalytics
                    ._get_value_for_date(
                        nav_values,
                        current_date,
                        nav_pointers.get(
                            scheme.pk,
                            -1,
                        ),
                    )
                )

                nav_pointers[scheme.pk] = pointer

                if nav is None:
                    continue

                mutual_fund_value += (
                    units * nav
                )

            # --------------------------------------------------
            # TOTALS
            # --------------------------------------------------

            total_invested = (
                equity_invested
                + mutual_fund_invested
            )

            total_value = (
                equity_value
                + mutual_fund_value
            )

            unrealized_pnl = (
                total_value
                - total_invested
            )

            results.append({
                "date": current_date,
                "invested_value": total_invested,
                "portfolio_value": total_value,
                "pnl": unrealized_pnl,
                "equity": {
                    "invested_value": equity_invested,
                    "portfolio_value": equity_value,
                    "pnl": (
                        equity_value
                        - equity_invested
                    ),
                },
                "mutual_funds": {
                    "invested_value": (
                        mutual_fund_invested
                    ),
                    "portfolio_value": (
                        mutual_fund_value
                    ),
                    "pnl": (
                        mutual_fund_value
                        - mutual_fund_invested
                    ),
                },
            })

            current_date += timedelta(days=1)

        return results

    # ==========================================================
    # COMMON DATE RANGE
    # ==========================================================

    @staticmethod
    def calculate_last_days(
        user,
        days=30,
        family_name=None,
    ):
        """
        Calculate the last N calendar days including today.

        family_name is passed straight through to calculate_history -
        see its docstring.
        """

        if days < 1:
            raise ValueError(
                "days must be at least 1"
            )

        end_date = date.today()

        start_date = (
            end_date
            - timedelta(days=days - 1)
        )

        return (
            HistoricalWealthAnalytics
            .calculate_history(
                user,
                start_date,
                end_date,
                family_name=family_name,
            )
        )