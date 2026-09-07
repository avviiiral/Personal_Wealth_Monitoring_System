import logging
from decimal import Decimal

from investments.models import Transaction, TransactionType
from mutual_funds.models import MutualFundTransaction

from .unified_wealth import UnifiedWealthAnalytics


logger = logging.getLogger(__name__)


class InvestmentSummaryService:
    """
    Builds the Dashboard "Investment Summary" table: current value and
    percentage-of-total for every Asset Class in the fixed master
    mapping below, grouped by Asset Category.

    IMPORTANT — this service does NOT recompute valuation. It reuses:

        - UnifiedWealthAnalytics.get_equity_holdings() /
          get_mutual_fund_holdings(), the same querysets that already
          power the Wealth Overview / KPI cards, so Investment
          Summary totals always reconcile with the rest of the
          Dashboard.

    CLASSIFICATION SOURCE:

        - Equities / other investments: the original Excel
          "Sub Class" column, preserved on
          investments.Transaction.sub_class (see the Transaction
          model docstring). A Holding's Asset Class is taken from the
          sub_class of its most recent transaction.

        - Mutual funds: MutualFundScheme.category, populated at
          import time (see
          investments/services/transaction_import.py,
          _get_or_create_mutual_fund_scheme) from that same Excel
          "Sub Class" column, since MutualFundTransaction itself does
          not store asset_class/sub_class.

    Any holding whose classification does not match the master
    mapping (e.g. blank scheme category from data imported before
    this feature existed, or a business sub-class outside the fixed
    list) is never dropped — it is kept under Other / Unlisted and
    logged, so the Investment Summary total always equals the
    portfolio's total current value.

    Each row also returns "raw_asset_classes" — the distinct raw
    Excel Sub Class strings that were normalized into that row (e.g.
    "Commodity ETFs" for the "Commodity" row). The Dashboard uses
    these, not the canonical label, to link to the Portfolio page's
    Sub Class table so the deep link always matches exactly instead
    of guessing at the raw string from the display label.
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

    # Master Asset Category -> Asset Class mapping. This also defines
    # the display order of the Investment Summary table. Do not add,
    # remove, or rename entries here without updating the business
    # requirement this mirrors.
    MASTER_MAPPING = [
        (
            "Other",
            [
                "Unlisted",
            ],
        ),
        (
            "Alternate",
            [
                "Commodity",
                "Private Equity",
                "REITs",
                "InvITs",
            ],
        ),
        (
            "Equities",
            [
                "Direct Equity",
                "Equity PMS",
                "Equity AIF",
                "Equity Mutual Fund",
                "Equity LRS",
            ],
        ),
        (
            "Fixed Income",
            [
                "Debt Mutual Fund",
                "Gold Bond",
            ],
        ),
        (
            "Liquids",
            [
                "Liquid Mutual Fund",
                "Arbitrage Mutual Fund",
            ],
        ),
    ]

    FALLBACK_ASSET_CLASS = "Unlisted"

    # Normalizes raw Excel "Sub Class" / scheme-category text
    # (case-insensitive) to one of the canonical Asset Class names
    # above via an EXACT match. Keys are upper-cased.
    _NORMALIZATION_MAP = {
        "UNLISTED": "Unlisted",
        "COMMODITY": "Commodity",
        "PRIVATE EQUITY": "Private Equity",
        "PE": "Private Equity",
        "REIT": "REITs",
        "REITS": "REITs",
        "REIT'S": "REITs",
        "REITS/INVITS": "REITs",
        "INVIT": "InvITs",
        "INVITS": "InvITs",
        "DIRECT EQUITY": "Direct Equity",
        "EQUITY": "Direct Equity",
        "STOCK": "Direct Equity",
        "EQUITY PMS": "Equity PMS",
        "PMS": "Equity PMS",
        "EQUITY AIF": "Equity AIF",
        "EQUITY AIF (CATEGORY III)": "Equity AIF",
        "AIF": "Equity AIF",
        "EQUITY MUTUAL FUND": "Equity Mutual Fund",
        "EQUITY LRS": "Equity LRS",
        "LRS": "Equity LRS",
        "DEBT MUTUAL FUND": "Debt Mutual Fund",
        "GOLD BOND": "Gold Bond",
        "SGB": "Gold Bond",
        "SOVEREIGN GOLD BOND": "Gold Bond",
        "LIQUID MUTUAL FUND": "Liquid Mutual Fund",
        "LIQUID FUND": "Liquid Mutual Fund",
        "ARBITRAGE MUTUAL FUND": "Arbitrage Mutual Fund",
        "ARBITRAGE FUND": "Arbitrage Mutual Fund",
        "ARBITRAGE": "Arbitrage Mutual Fund",
    }

    # Fallback tier used only when neither an exact class name nor an
    # exact entry in _NORMALIZATION_MAP matched. Real Excel data uses
    # free-text variants (e.g. "Commodity ETFs", "Direct Equity -
    # Large Cap") that an exact-match table can't anticipate, so this
    # checks whether the cleaned, upper-cased value CONTAINS one of
    # these keywords. Order matters — more specific keywords are
    # checked before the generic ones they contain (e.g.
    # "EQUITY MUTUAL FUND" before "MUTUAL FUND", "ARBITRAGE MUTUAL
    # FUND" before "LIQUID"), so add new keywords in the right place
    # rather than at the end.
    _CONTAINS_FALLBACK = [
        ("EQUITY AIF", "Equity AIF"),
        ("AIF", "Equity AIF"),
        ("EQUITY PMS", "Equity PMS"),
        ("EQUITY MUTUAL FUND", "Equity Mutual Fund"),
        ("DEBT MUTUAL FUND", "Debt Mutual Fund"),
        ("ARBITRAGE MUTUAL FUND", "Arbitrage Mutual Fund"),
        ("ARBITRAGE", "Arbitrage Mutual Fund"),
        ("LIQUID MUTUAL FUND", "Liquid Mutual Fund"),
        ("LIQUID", "Liquid Mutual Fund"),
        ("SOVEREIGN GOLD", "Gold Bond"),
        ("GOLD BOND", "Gold Bond"),
        ("PRIVATE EQUITY", "Private Equity"),
        ("REIT", "REITs"),
        ("INVIT", "InvITs"),
        ("COMMODITY", "Commodity"),
        ("EQUITY LRS", "Equity LRS"),
        ("LRS", "Equity LRS"),
        ("DIRECT EQUITY", "Direct Equity"),
        ("UNLISTED", "Unlisted"),
    ]

    @classmethod
    def _valid_asset_classes(cls):
        classes = set()

        for _, asset_classes in cls.MASTER_MAPPING:
            classes.update(asset_classes)

        return classes

    @classmethod
    def _normalize_asset_class(cls, raw_value):
        """
        Map a raw classification string to a canonical Asset Class
        name from MASTER_MAPPING. Falls back to Other / Unlisted for
        anything blank or unrecognised, and logs it rather than
        dropping the value it belongs to.
        """

        cleaned = (raw_value or "").strip()

        if not cleaned:
            logger.warning(
                "Investment Summary: missing asset-class "
                "classification; bucketed under Other / Unlisted."
            )

            return cls.FALLBACK_ASSET_CLASS

        if cleaned in cls._valid_asset_classes():
            return cleaned

        upper = cleaned.upper()

        canonical = cls._NORMALIZATION_MAP.get(upper)

        if canonical:
            return canonical

        for keyword, canonical in cls._CONTAINS_FALLBACK:
            if keyword in upper:
                logger.info(
                    "Investment Summary: %r matched via "
                    "keyword %r -> %r. Consider adding an "
                    "exact entry to _NORMALIZATION_MAP.",
                    raw_value,
                    keyword,
                    canonical,
                )

                return canonical

        logger.warning(
            "Investment Summary: unrecognised asset-class "
            "classification %r; bucketed under Other / Unlisted.",
            raw_value,
        )

        return cls.FALLBACK_ASSET_CLASS

    @staticmethod
    def _equity_asset_class_by_asset_id(user, family_name=None):
        """
        Resolve every asset's raw Asset Class as the sub_class of its
        most recent transaction that has one set.

        family_name:
            Optional. When provided, only that Family Name's
            transactions are considered, so the resolved Asset Class
            reflects how the asset was classified within that family.
        """

        rows_qs = (
            Transaction.objects
            .filter(owner_id__in=InvestmentSummaryService._owner_ids(user))
            .exclude(sub_class__isnull=True)
            .exclude(sub_class__exact="")
        )

        if family_name:
            rows_qs = rows_qs.filter(
                family_name=family_name
            )

        rows = (
            rows_qs
            .order_by(
                "asset_id",
                "-transaction_date",
                "-created_at",
                "-id",
            )
            .values_list(
                "asset_id",
                "sub_class",
            )
        )

        resolved = {}

        for asset_id, sub_class in rows:
            if asset_id not in resolved:
                resolved[asset_id] = sub_class

        return resolved

    @staticmethod
    def _equity_asset_class_weights_by_asset_id(user, family_name=None):
        """
        Resolve every asset's sub_class as a set of WEIGHTS rather
        than a single winner, for assets genuinely held across more
        than one sub_class (e.g. the same stock bought partly
        directly and partly through a PMS — see Bharti Airtel /
        Bajaj Finance in this project's real data, discovered while
        investigating why the Dashboard's Direct Equity / Equity PMS
        split didn't reconcile with the Portfolio page's per-channel
        breakdown).

        _equity_asset_class_by_asset_id (above) picks ONE sub_class
        per asset — whichever transaction was most recent — and
        assigns the asset's ENTIRE current_value to that one class.
        For an asset held through only one channel that's correct
        and cheap. For an asset held through more than one channel,
        it silently misattributes the other channel's share of the
        value to the wrong class. This method instead computes each
        channel's real weight from actual transaction quantities
        (BUY quantity minus SELL quantity, per (asset_id, sub_class)
        pair — the same net-position logic HoldingCalculationEngine
        uses, just grouped by sub_class as well as asset), so a
        Holding's current_value can be split proportionally across
        the classes it actually spans, rather than assigned whole to
        one of them.

        Returns: {asset_id: {sub_class: weight}}, weights summing to
        1.0 per asset. Assets held through only one sub_class get a
        single-entry dict with weight 1.0 — behaviourally identical
        to the old single-class lookup for the common case.
        """

        rows_qs = (
            Transaction.objects
            .filter(owner_id__in=InvestmentSummaryService._owner_ids(user))
            .exclude(sub_class__isnull=True)
            .exclude(sub_class__exact="")
        )

        if family_name:
            rows_qs = rows_qs.filter(
                family_name=family_name
            )

        rows = rows_qs.values_list(
            "asset_id",
            "sub_class",
            "transaction_type",
            "quantity",
        )

        net_quantity = {}

        for asset_id, sub_class, transaction_type, quantity in rows:

            key = (asset_id, sub_class)

            if key not in net_quantity:
                net_quantity[key] = Decimal("0")

            quantity = quantity or Decimal("0")

            if transaction_type == TransactionType.SELL:
                net_quantity[key] -= quantity
            else:
                # BUY, SIP, BONUS, SPLIT, and anything else that adds
                # to the position. DIVIDEND/INTEREST/DEPOSIT/
                # WITHDRAWAL/OTHER don't carry a meaningful quantity
                # for this asset and are excluded upstream by
                # requiring a non-blank sub_class in practice, but
                # are harmless here even if present (quantity is
                # typically 0/null for those).
                net_quantity[key] += quantity

        totals_by_asset = {}

        for (asset_id, sub_class), qty in net_quantity.items():

            if qty <= 0:
                continue

            totals_by_asset.setdefault(
                asset_id, {}
            )[sub_class] = qty

        weights = {}

        for asset_id, class_quantities in totals_by_asset.items():

            asset_total = sum(
                class_quantities.values(),
                Decimal("0"),
            )

            if asset_total <= 0:
                continue

            weights[asset_id] = {
                sub_class: (qty / asset_total)
                for sub_class, qty in class_quantities.items()
            }

        return weights

    @classmethod
    def _family_equity_positions(cls, user, family_name):
        """
        Rebuild open (quantity > 0) equity positions for one exact
        Family Name directly from Transaction, since Holding has no
        family_name (one aggregated row per asset across ALL
        families).

        Quantity/invested value are recomputed with the same
        average-cost method HistoricalWealthAnalytics already uses.
        Current price is read from the asset's existing Holding
        (Asset.holding.current_price) rather than re-derived, since
        price is asset-level market data - identical for every
        family - so this stays consistent with the price already
        shown everywhere else and adds no new price-freshness logic.

        Returns a list of (asset_id, current_value) tuples for assets
        with an open position and a known current price.
        """

        from .historical_wealth import HistoricalWealthAnalytics

        transactions = (
            Transaction.objects
            .filter(
                owner_id__in=InvestmentSummaryService._owner_ids(user),
                family_name=family_name,
            )
            .select_related("asset__holding")
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        positions = {}
        assets_by_id = {}

        for transaction in transactions:
            assets_by_id[transaction.asset_id] = transaction.asset

            position = positions.setdefault(
                transaction.asset_id,
                {
                    "quantity": cls.ZERO,
                    "invested_value": cls.ZERO,
                },
            )

            HistoricalWealthAnalytics._apply_equity_transaction(
                position,
                transaction,
            )

        results = []

        for asset_id, position in positions.items():
            if position["quantity"] <= 0:
                continue

            asset = assets_by_id[asset_id]
            holding = getattr(asset, "holding", None)

            current_price = (
                getattr(holding, "current_price", None)
                if holding
                else None
            )

            if current_price is None:
                continue

            current_value = (
                position["quantity"] * current_price
            )

            results.append((asset_id, current_value))

        return results

    @classmethod
    def _family_mutual_fund_positions(cls, user, family_name):
        """
        Rebuild open (units > 0) mutual-fund positions for one exact
        Family Name directly from MutualFundTransaction, since
        MutualFundHolding has no family_name either.

        Same approach as _family_equity_positions: recompute
        units/invested value from transactions, read current NAV from
        the scheme's existing MutualFundHolding.

        Returns a list of (scheme, current_value) tuples for schemes
        with an open position and a known current NAV.
        """

        from .historical_wealth import HistoricalWealthAnalytics

        transactions = (
            MutualFundTransaction.objects
            .filter(
                owner_id__in=InvestmentSummaryService._owner_ids(user),
                family_name=family_name,
            )
            .select_related("scheme__holding")
            .order_by(
                "scheme_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        positions = {}
        schemes_by_id = {}

        for transaction in transactions:
            schemes_by_id[transaction.scheme_id] = transaction.scheme

            position = positions.setdefault(
                transaction.scheme_id,
                {
                    "units": cls.ZERO,
                    "invested_value": cls.ZERO,
                },
            )

            HistoricalWealthAnalytics._apply_mutual_fund_transaction(
                position,
                transaction,
            )

        results = []

        for scheme_id, position in positions.items():
            if position["units"] <= 0:
                continue

            scheme = schemes_by_id[scheme_id]
            holding = getattr(scheme, "holding", None)

            current_nav = (
                getattr(holding, "current_nav", None)
                if holding
                else None
            )

            if current_nav is None:
                continue

            current_value = (
                position["units"] * current_nav
            )

            results.append((scheme, current_value))

        return results

    @classmethod
    def _build_results(cls, totals, raw_values_by_asset_class):
        """
        Shared tail: turn a filled totals/raw_values_by_asset_class
        pair into the same {results, total_current_value} shape used
        by both the all-families and family-filtered calculate()
        paths, so the two paths can never drift apart in how rows are
        assembled.
        """

        total_current_value = sum(
            totals.values(),
            cls.ZERO,
        )

        results = []

        for category, asset_classes in cls.MASTER_MAPPING:
            for asset_class in asset_classes:
                value = totals[asset_class]

                percentage = (
                    (
                        value
                        / total_current_value
                    ) * 100
                    if total_current_value
                    else cls.ZERO
                )

                results.append({
                    "asset_category": category,
                    "asset_class": asset_class,
                    "current_value": value,
                    "percentage_of_total": round(
                        percentage,
                        2,
                    ),
                    "raw_asset_classes": sorted(
                        raw_values_by_asset_class[asset_class]
                    ),
                })

        return {
            "results": results,
            "total_current_value": total_current_value,
        }

    @classmethod
    def calculate(cls, user, family_name=None):
        """
        Return the Investment Summary rows and the total current
        value they were computed against.

        family_name:
            Optional. When omitted, this is byte-for-byte the
            original all-families calculation (Holding /
            MutualFundHolding based) - unchanged.

            When provided, Holding/MutualFundHolding cannot be used
            (neither carries a family_name), so positions are instead
            rebuilt directly from Transaction / MutualFundTransaction
            for that one family via _family_equity_positions /
            _family_mutual_fund_positions.
        """

        totals = {
            asset_class: cls.ZERO
            for _, asset_classes in cls.MASTER_MAPPING
            for asset_class in asset_classes
        }

        raw_values_by_asset_class = {
            asset_class: set()
            for _, asset_classes in cls.MASTER_MAPPING
            for asset_class in asset_classes
        }

        if not family_name:
            # --------------------------------------------------------
            # EQUITY / OTHER INVESTMENT HOLDINGS
            # --------------------------------------------------------
            asset_class_by_asset_id = (
                cls._equity_asset_class_by_asset_id(user)
            )

            equity_holdings = (
                UnifiedWealthAnalytics
                .get_equity_holdings(user)
            )

            for holding in equity_holdings:
                value = (
                    holding.current_value
                    or cls.ZERO
                )

                raw_class = asset_class_by_asset_id.get(
                    holding.asset_id
                )

                asset_class = cls._normalize_asset_class(
                    raw_class
                )

                totals[asset_class] += value

                if raw_class:
                    raw_values_by_asset_class[asset_class].add(
                        raw_class
                    )

            # --------------------------------------------------------
            # MUTUAL FUND HOLDINGS
            # --------------------------------------------------------
            mutual_fund_holdings = (
                UnifiedWealthAnalytics
                .get_mutual_fund_holdings(user)
            )

            for holding in mutual_fund_holdings:
                value = (
                    holding.current_value
                    or cls.ZERO
                )

                raw_class = getattr(
                    holding.scheme,
                    "category",
                    None,
                )

                asset_class = cls._normalize_asset_class(
                    raw_class
                )

                totals[asset_class] += value

                if raw_class:
                    raw_values_by_asset_class[asset_class].add(
                        raw_class
                    )

            return cls._build_results(
                totals,
                raw_values_by_asset_class,
            )

        # ==================================================
        # FAMILY-FILTERED PATH
        # ==================================================

        asset_class_by_asset_id = (
            cls._equity_asset_class_by_asset_id(
                user,
                family_name=family_name,
            )
        )

        for asset_id, value in cls._family_equity_positions(
            user,
            family_name,
        ):
            raw_class = asset_class_by_asset_id.get(asset_id)

            asset_class = cls._normalize_asset_class(
                raw_class
            )

            totals[asset_class] += value

            if raw_class:
                raw_values_by_asset_class[asset_class].add(
                    raw_class
                )

        for scheme, value in cls._family_mutual_fund_positions(
            user,
            family_name,
        ):
            raw_class = getattr(
                scheme,
                "category",
                None,
            )

            asset_class = cls._normalize_asset_class(
                raw_class
            )

            totals[asset_class] += value

            if raw_class:
                raw_values_by_asset_class[asset_class].add(
                    raw_class
                )

        return cls._build_results(
            totals,
            raw_values_by_asset_class,
        )

    # ==========================================================
    # PERFORMANCE BY SUB CLASS
    # ==========================================================

    @classmethod
    def calculate_performance_by_subclass(cls, user):
        """
        Aggregate invested value, current value, and unrealized P&L
        by Asset Class (the same canonical Sub Class classification
        used above for the Investment Summary table), for the
        Analytics "Investment Performance" chart.

        Unlike calculate(), which reports every Asset Class in
        MASTER_MAPPING (including empty ones, for a stable table
        layout), this only returns Asset Classes that actually hold
        value, since the performance chart should not plot empty
        bars.
        """

        totals = {
            asset_class: {
                "invested": cls.ZERO,
                "current": cls.ZERO,
            }
            for _, asset_classes in cls.MASTER_MAPPING
            for asset_class in asset_classes
        }

        category_by_asset_class = {
            asset_class: category
            for category, asset_classes in cls.MASTER_MAPPING
            for asset_class in asset_classes
        }

        asset_class_by_asset_id = (
            cls._equity_asset_class_by_asset_id(user)
        )

        equity_holdings = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        for holding in equity_holdings:
            raw_class = asset_class_by_asset_id.get(
                holding.asset_id
            )

            asset_class = cls._normalize_asset_class(
                raw_class
            )

            totals[asset_class]["invested"] += (
                holding.invested_value or cls.ZERO
            )

            totals[asset_class]["current"] += (
                holding.current_value or cls.ZERO
            )

        mutual_fund_holdings = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
        )

        for holding in mutual_fund_holdings:
            raw_class = getattr(
                holding.scheme,
                "category",
                None,
            )

            asset_class = cls._normalize_asset_class(
                raw_class
            )

            totals[asset_class]["invested"] += (
                holding.invested_value or cls.ZERO
            )

            totals[asset_class]["current"] += (
                holding.current_value or cls.ZERO
            )

        results = []

        for category, asset_classes in cls.MASTER_MAPPING:
            for asset_class in asset_classes:
                invested = totals[asset_class]["invested"]
                current = totals[asset_class]["current"]

                if not invested and not current:
                    continue

                pnl = current - invested

                pnl_percentage = (
                    (pnl / invested) * 100
                    if invested
                    else cls.ZERO
                )

                results.append({
                    "asset_category": category,
                    "asset_class": asset_class,
                    "invested_value": invested,
                    "current_value": current,
                    "unrealized_pnl": pnl,
                    "pnl_percentage": round(
                        pnl_percentage,
                        2,
                    ),
                })

        return sorted(
            results,
            key=lambda item: item["pnl_percentage"],
            reverse=True,
        )

    # ==========================================================
    # ALLOCATION BY ADVISOR
    # ==========================================================

    UNASSIGNED_ADVISOR = "Unassigned"

    @staticmethod
    def _advisor_by_asset_id(user):
        """
        Resolve every equity/other-investment asset's Advisor as the
        advisors value of its most recent transaction that has one
        set.

        NOTE: Mutual fund holdings are tracked through a separate
        MutualFundTransaction model that does not carry the original
        Excel Advisors column (see the class docstring for the same
        limitation on asset-class classification). Mutual fund value
        is therefore bucketed under UNASSIGNED_ADVISOR below rather
        than dropped.
        """

        rows = (
            Transaction.objects
            .filter(owner_id__in=InvestmentSummaryService._owner_ids(user))
            .exclude(advisors__isnull=True)
            .exclude(advisors__exact="")
            .order_by(
                "asset_id",
                "-transaction_date",
                "-created_at",
                "-id",
            )
            .values_list(
                "asset_id",
                "advisors",
            )
        )

        resolved = {}

        for asset_id, advisor in rows:
            if asset_id not in resolved:
                resolved[asset_id] = advisor

        return resolved

    @classmethod
    def calculate_allocation_by_advisor(cls, user):
        """
        Aggregate current value by Advisor, for the Analytics
        "Allocation by Advisor" pie chart.
        """

        totals = {}

        advisor_by_asset_id = (
            cls._advisor_by_asset_id(user)
        )

        equity_holdings = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        for holding in equity_holdings:
            advisor = (
                advisor_by_asset_id.get(holding.asset_id)
                or ""
            ).strip() or cls.UNASSIGNED_ADVISOR

            value = (
                holding.current_value
                or cls.ZERO
            )

            totals[advisor] = (
                totals.get(advisor, cls.ZERO)
                + value
            )

        mutual_fund_holdings = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
        )

        mutual_fund_value = sum(
            (
                holding.current_value
                or cls.ZERO
            )
            for holding in mutual_fund_holdings
        )

        if mutual_fund_value:
            totals[cls.UNASSIGNED_ADVISOR] = (
                totals.get(
                    cls.UNASSIGNED_ADVISOR,
                    cls.ZERO,
                )
                + mutual_fund_value
            )

        total_value = sum(
            totals.values(),
            cls.ZERO,
        )

        results = []

        for advisor, value in totals.items():
            if value <= 0:
                continue

            percentage = (
                (value / total_value) * 100
                if total_value
                else cls.ZERO
            )

            results.append({
                "advisor": advisor,
                "value": value,
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        return {
            "results": sorted(
                results,
                key=lambda item: item["value"],
                reverse=True,
            ),
            "total_current_value": total_value,
        }

    # ==========================================================
    # PERFORMANCE BY ADVISOR
    # ==========================================================

    @classmethod
    def calculate_performance_by_advisor(cls, user):
        """
        Aggregate invested value, current value, and unrealized P&L
        by Advisor, for the Analytics "Advisor Performance" chart —
        i.e. how much return each advisor's recommendations have
        actually generated, not just how much value they manage.

        Same advisor resolution as calculate_allocation_by_advisor:
        the advisors value on each asset's most recent Transaction.
        Mutual funds have no advisor data in their transaction model
        (see _advisor_by_asset_id), so their invested/current value
        is bucketed under UNASSIGNED_ADVISOR rather than dropped —
        that bucket's return is meaningful (it is the blended return
        of every un-attributed holding), just not attributable to a
        named advisor.
        """

        totals = {}

        advisor_by_asset_id = (
            cls._advisor_by_asset_id(user)
        )

        equity_holdings = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        for holding in equity_holdings:
            advisor = (
                advisor_by_asset_id.get(holding.asset_id)
                or ""
            ).strip() or cls.UNASSIGNED_ADVISOR

            if advisor not in totals:
                totals[advisor] = {
                    "invested": cls.ZERO,
                    "current": cls.ZERO,
                }

            totals[advisor]["invested"] += (
                holding.invested_value or cls.ZERO
            )

            totals[advisor]["current"] += (
                holding.current_value or cls.ZERO
            )

        mutual_fund_holdings = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
        )

        for holding in mutual_fund_holdings:
            advisor = cls.UNASSIGNED_ADVISOR

            if advisor not in totals:
                totals[advisor] = {
                    "invested": cls.ZERO,
                    "current": cls.ZERO,
                }

            totals[advisor]["invested"] += (
                holding.invested_value or cls.ZERO
            )

            totals[advisor]["current"] += (
                holding.current_value or cls.ZERO
            )

        results = []

        for advisor, entry in totals.items():
            invested = entry["invested"]
            current = entry["current"]

            if not invested and not current:
                continue

            pnl = current - invested

            pnl_percentage = (
                (pnl / invested) * 100
                if invested
                else cls.ZERO
            )

            results.append({
                "advisor": advisor,
                "invested_value": invested,
                "current_value": current,
                "unrealized_pnl": pnl,
                "pnl_percentage": round(
                    pnl_percentage,
                    2,
                ),
            })

        return sorted(
            results,
            key=lambda item: item["pnl_percentage"],
            reverse=True,
        )

    # ==========================================================
    # COMPOSITION BY AMC
    #
    # Two distinct real data sources, deliberately not unified into
    # a single AMC model yet (see the note on SecurityMaster.amc_name
    # in investments/models.py — same fragile free-text limitation):
    #
    #   - Mutual fund holdings: MutualFundScheme.amc_name, an
    #     existing, independently populated field.
    #   - Equity/other holdings: SecurityMaster.amc_name, added
    #     alongside credit_rating/pe_ratio/etc — populated via
    #     Django admin, empty until filled in.
    #
    # A holding with no AMC name available from either source is
    # bucketed under UNASSIGNED_AMC rather than dropped, same
    # pattern as UNASSIGNED_ADVISOR above.
    # ==========================================================

    UNASSIGNED_AMC = "Unassigned"

    @staticmethod
    def _amc_by_equity_asset_id(user):
        """
        Resolve every equity/other-investment asset's AMC from its
        linked SecurityMaster row, the same lookup shape as
        _advisor_by_asset_id (dict of asset_id -> value).
        """

        from investments.models import Asset

        rows = (
            Asset.objects
            .filter(
                owner_id__in=InvestmentSummaryService._owner_ids(user),
                security_master__isnull=False,
            )
            .exclude(security_master__amc_name__isnull=True)
            .exclude(security_master__amc_name__exact="")
            .values_list(
                "id",
                "security_master__amc_name",
            )
        )

        return dict(rows)

    @classmethod
    def calculate_composition_by_amc(cls, user):
        """
        Aggregate current value, invested value, and holding count
        by AMC, for Portfolio Composition Analysis (Top AMC
        exposures, AMC concentration).
        """

        totals = {}

        amc_by_asset_id = (
            cls._amc_by_equity_asset_id(user)
        )

        equity_holdings = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        for holding in equity_holdings:
            amc = (
                amc_by_asset_id.get(holding.asset_id)
                or ""
            ).strip() or cls.UNASSIGNED_AMC

            if amc not in totals:
                totals[amc] = {
                    "invested": cls.ZERO,
                    "current": cls.ZERO,
                    "holding_count": 0,
                }

            totals[amc]["invested"] += (
                holding.invested_value or cls.ZERO
            )

            totals[amc]["current"] += (
                holding.current_value or cls.ZERO
            )

            totals[amc]["holding_count"] += 1

        mutual_fund_holdings = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
        )

        for holding in mutual_fund_holdings:
            amc = (
                getattr(
                    holding.scheme,
                    "amc_name",
                    None,
                )
                or ""
            ).strip() or cls.UNASSIGNED_AMC

            if amc not in totals:
                totals[amc] = {
                    "invested": cls.ZERO,
                    "current": cls.ZERO,
                    "holding_count": 0,
                }

            totals[amc]["invested"] += (
                holding.invested_value or cls.ZERO
            )

            totals[amc]["current"] += (
                holding.current_value or cls.ZERO
            )

            totals[amc]["holding_count"] += 1

        grand_total = sum(
            (entry["current"] for entry in totals.values()),
            cls.ZERO,
        )

        results = []

        for amc, entry in totals.items():
            current = entry["current"]

            if current <= 0:
                continue

            percentage = (
                (current / grand_total) * 100
                if grand_total
                else cls.ZERO
            )

            results.append({
                "amc_name": amc,
                "invested_value": entry["invested"],
                "current_value": current,
                "holding_count": entry["holding_count"],
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        return {
            "results": sorted(
                results,
                key=lambda item: item["current_value"],
                reverse=True,
            ),
            "total_current_value": grand_total,
            "number_of_amcs": len(results),
        }


    # ==========================================================
    # EQUITY ANALYSIS
    #
    # Sourced entirely from SecurityMaster (pe_ratio/pb_ratio/roe/
    # cap_type — investments/migrations/0007_...) joined onto
    # equity/other-investment Holdings via Asset.security_master.
    #
    # Only Holding-based (equity/other) positions are considered —
    # mutual fund SIP/scheme holdings do not carry a SecurityMaster
    # link, so they contribute to "Market Value" and the product-
    # category split's own count, but not to the P/E, P/B, ROE
    # weighted averages or market-cap allocation below, since there
    # is no per-holding quant data to weight.
    # ==========================================================

    @staticmethod
    def _security_master_by_asset_id(user):
        """
        Bulk-fetch SecurityMaster fields keyed by asset_id, in one
        query, for every asset owned by the user that has one
        linked — same shape as _advisor_by_asset_id /
        _amc_by_equity_asset_id above.
        """

        from investments.models import Asset

        rows = (
            Asset.objects
            .filter(
                owner_id__in=InvestmentSummaryService._owner_ids(user),
                security_master__isnull=False,
            )
            .values_list(
                "id",
                "security_master__sector",
                "security_master__cap_type",
                "security_master__pe_ratio",
                "security_master__pb_ratio",
                "security_master__roe",
            )
        )

        return {
            asset_id: {
                "sector": sector,
                "cap_type": cap_type,
                "pe_ratio": pe_ratio,
                "pb_ratio": pb_ratio,
                "roe": roe,
            }
            for asset_id, sector, cap_type, pe_ratio, pb_ratio, roe in rows
        }

    @classmethod
    def calculate_equity_analysis(cls, user):
        """
        Return the Equity Analysis view: current value / allocation
        of the overall portfolio, market-cap allocation, and
        value-weighted P/E, P/B, ROE across every equity/other-
        investment Holding that has SecurityMaster quant data.

        Weighting: each holding's ratio is weighted by its
        current_value's share of the total current_value of ONLY
        the holdings that have that specific ratio populated — so a
        handful of populated holdings don't get diluted to near-zero
        by every unpopulated one. This means the three weighted
        averages (P/E, P/B, ROE) may each be computed over a
        different, smaller base than "Current Value" below, and
        that base size is returned explicitly rather than left
        implicit.
        """

        equity_holdings = list(
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        sm_by_asset_id = (
            cls._security_master_by_asset_id(user)
        )

        total_current_value = sum(
            (
                holding.current_value
                or cls.ZERO
            )
            for holding in equity_holdings
        )

        cap_totals = {}

        weighted_sums = {
            "pe_ratio": cls.ZERO,
            "pb_ratio": cls.ZERO,
            "roe": cls.ZERO,
        }

        weighted_bases = {
            "pe_ratio": cls.ZERO,
            "pb_ratio": cls.ZERO,
            "roe": cls.ZERO,
        }

        weighted_counts = {
            "pe_ratio": 0,
            "pb_ratio": 0,
            "roe": 0,
        }

        for holding in equity_holdings:
            current_value = (
                holding.current_value
                or cls.ZERO
            )

            sm = sm_by_asset_id.get(
                holding.asset_id,
                {},
            )

            cap_type = (
                sm.get("cap_type")
                or ""
            ).strip() or "Unclassified"

            cap_totals[cap_type] = (
                cap_totals.get(
                    cap_type,
                    cls.ZERO,
                )
                + current_value
            )

            for field in (
                "pe_ratio",
                "pb_ratio",
                "roe",
            ):
                value = sm.get(field)

                if value is None or current_value <= 0:
                    continue

                weighted_sums[field] += (
                    value * current_value
                )

                weighted_bases[field] += current_value

                weighted_counts[field] += 1

        market_cap_allocation = []

        for cap_type, value in sorted(
            cap_totals.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            percentage = (
                (value / total_current_value) * 100
                if total_current_value
                else cls.ZERO
            )

            market_cap_allocation.append({
                "cap_type": cap_type,
                "current_value": value,
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        def weighted_average(field):
            base = weighted_bases[field]

            if not base:
                return None

            return round(
                weighted_sums[field] / base,
                2,
            )

        return {
            "current_value": total_current_value,
            "number_of_holdings": len(equity_holdings),
            "portfolio_pe": weighted_average("pe_ratio"),
            "portfolio_pe_holding_count": weighted_counts["pe_ratio"],
            "portfolio_pb": weighted_average("pb_ratio"),
            "portfolio_pb_holding_count": weighted_counts["pb_ratio"],
            "portfolio_roe": weighted_average("roe"),
            "portfolio_roe_holding_count": weighted_counts["roe"],
            "market_cap_allocation": market_cap_allocation,
        }

    # ==========================================================
    # FIXED INCOME ANALYSIS
    #
    # Same sourcing/weighting approach as Equity Analysis, but for
    # the Fixed Income quant fields (ytm/modified_duration/
    # average_maturity/credit_rating) — see the same migration.
    # ==========================================================

    @staticmethod
    def _fixed_income_security_master_by_asset_id(user):
        from investments.models import Asset

        rows = (
            Asset.objects
            .filter(
                owner_id__in=InvestmentSummaryService._owner_ids(user),
                security_master__isnull=False,
            )
            .values_list(
                "id",
                "security_master__credit_rating",
                "security_master__ytm",
                "security_master__modified_duration",
                "security_master__average_maturity",
            )
        )

        return {
            asset_id: {
                "credit_rating": credit_rating,
                "ytm": ytm,
                "modified_duration": modified_duration,
                "average_maturity": average_maturity,
            }
            for (
                asset_id,
                credit_rating,
                ytm,
                modified_duration,
                average_maturity,
            ) in rows
        }

    CREDIT_RATING_LABELS = {
        "SOVEREIGN": "Sovereign",
        "AAA": "AAA / AAA+",
        "AA": "AA / AA+",
        "A_AND_BELOW": "A and Below",
        "UNRATED": "Unrated",
    }

    @classmethod
    def calculate_fixed_income_analysis(cls, user):
        """
        Return the Fixed Income Analysis view: current value /
        allocation, credit rating distribution, and value-weighted
        YTM / Modified Duration / Average Maturity — restricted to
        Holdings whose Asset is classified under the Fixed Income
        canonical asset category (see
        InvestmentSummaryService.MASTER_MAPPING), the same
        classification the Dashboard's Investment Summary already
        uses, so this page's "Current Value" always reconciles with
        that table's Fixed Income row.
        """

        asset_class_weights_by_asset_id = (
            cls._equity_asset_class_weights_by_asset_id(user)
        )

        fixed_income_classes = set()

        for category, asset_classes in cls.MASTER_MAPPING:
            if category == "Fixed Income":
                fixed_income_classes.update(asset_classes)

        equity_holdings = list(
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        # (holding, fi_weight) pairs rather than a plain filtered
        # list — fi_weight is the FRACTION of the holding's value
        # genuinely bought through a Fixed-Income sub_class, from
        # real transaction quantities (see
        # _equity_asset_class_weights_by_asset_id). A holding bought
        # entirely through one FI channel gets weight 1.0, same as
        # a simple filter would give — this only differs for a
        # holding split across an FI and a non-FI channel, where the
        # old plain filter would have included/excluded the whole
        # position based on whichever channel's transaction was most
        # recent. No holding in this data is currently split this
        # way, so this is a correctness safeguard, not something
        # that changes today's numbers.
        fi_holdings = []

        for holding in equity_holdings:

            class_weights = asset_class_weights_by_asset_id.get(
                holding.asset_id
            )

            if not class_weights:
                continue

            fi_weight = sum(
                weight
                for raw_class, weight in class_weights.items()
                if cls._normalize_asset_class(raw_class)
                in fixed_income_classes
            )

            if fi_weight > 0:
                fi_holdings.append((holding, fi_weight))

        sm_by_asset_id = (
            cls._fixed_income_security_master_by_asset_id(user)
        )

        total_current_value = sum(
            (
                (holding.current_value or cls.ZERO) * fi_weight
            )
            for holding, fi_weight in fi_holdings
        )

        rating_totals = {}

        weighted_sums = {
            "ytm": cls.ZERO,
            "modified_duration": cls.ZERO,
            "average_maturity": cls.ZERO,
        }

        weighted_bases = {
            "ytm": cls.ZERO,
            "modified_duration": cls.ZERO,
            "average_maturity": cls.ZERO,
        }

        weighted_counts = {
            "ytm": 0,
            "modified_duration": 0,
            "average_maturity": 0,
        }

        for holding, fi_weight in fi_holdings:
            current_value = (
                (holding.current_value or cls.ZERO) * fi_weight
            )

            sm = sm_by_asset_id.get(
                holding.asset_id,
                {},
            )

            raw_rating = sm.get("credit_rating")

            rating_label = (
                cls.CREDIT_RATING_LABELS.get(
                    raw_rating,
                    "Unrated",
                )
            )

            rating_totals[rating_label] = (
                rating_totals.get(
                    rating_label,
                    cls.ZERO,
                )
                + current_value
            )

            for field in (
                "ytm",
                "modified_duration",
                "average_maturity",
            ):
                value = sm.get(field)

                if value is None or current_value <= 0:
                    continue

                weighted_sums[field] += (
                    value * current_value
                )

                weighted_bases[field] += current_value

                weighted_counts[field] += 1

        rating_distribution = []

        for rating, value in sorted(
            rating_totals.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            percentage = (
                (value / total_current_value) * 100
                if total_current_value
                else cls.ZERO
            )

            rating_distribution.append({
                "credit_rating": rating,
                "current_value": value,
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        def weighted_average(field):
            base = weighted_bases[field]

            if not base:
                return None

            return round(
                weighted_sums[field] / base,
                2,
            )

        return {
            "current_value": total_current_value,
            "number_of_holdings": len(fi_holdings),
            "ytm": weighted_average("ytm"),
            "ytm_holding_count": weighted_counts["ytm"],
            "modified_duration": weighted_average("modified_duration"),
            "modified_duration_holding_count": weighted_counts["modified_duration"],
            "average_maturity": weighted_average("average_maturity"),
            "average_maturity_holding_count": weighted_counts["average_maturity"],
            "credit_rating_distribution": rating_distribution,
        }

    # ==========================================================
    # SECTOR ALLOCATION
    #
    # Sourced from SecurityMaster.sector (populated for direct
    # equity/other-investment holdings via refresh_security_master
    # / the AMFI-sourced batches — investments/migrations/0007_...).
    # Covers every equity/other-investment Holding, same population
    # as calculate_equity_analysis — mutual funds routed through the
    # separate MutualFundHolding model are not included here, since
    # a fund holds many sectors at once and SecurityMaster.sector
    # models a single security's sector, not a fund's blend.
    # Holdings with no sector on file are bucketed under
    # "Unclassified" rather than dropped, same pattern as
    # market_cap_allocation / calculate_composition_by_amc.
    # ==========================================================

    @classmethod
    def calculate_sector_allocation(cls, user):
        """
        Return current-value allocation by sector, across every
        equity/other-investment Holding.
        """

        equity_holdings = list(
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        sm_by_asset_id = (
            cls._security_master_by_asset_id(user)
        )

        totals = {}

        for holding in equity_holdings:
            current_value = (
                holding.current_value
                or cls.ZERO
            )

            if current_value <= 0:
                continue

            sm = sm_by_asset_id.get(
                holding.asset_id,
                {},
            )

            sector = (
                sm.get("sector")
                or ""
            ).strip() or "Unclassified"

            totals[sector] = (
                totals.get(
                    sector,
                    cls.ZERO,
                )
                + current_value
            )

        grand_total = sum(
            totals.values(),
            cls.ZERO,
        )

        results = []

        for sector, value in sorted(
            totals.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            percentage = (
                (value / grand_total) * 100
                if grand_total
                else cls.ZERO
            )

            results.append({
                "sector": sector,
                "current_value": value,
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        return {
            "results": results,
            "total_current_value": grand_total,
        }

    # ==========================================================
    # MARKET CAP ALLOCATION (Dashboard donut)
    #
    # Same pattern as calculate_sector_allocation above, keyed on
    # SecurityMaster.cap_type instead of sector — populated for
    # 73 of this project's real stock holdings via the official
    # AMFI stock categorisation batch (see the earlier session's
    # load_security_master_data run), giving noticeably better
    # coverage than sector (which depends on Yahoo Finance's
    # per-stock resolution succeeding). This is a standalone,
    # dashboard-facing duplicate of the market_cap_allocation
    # block already computed inside calculate_equity_analysis —
    # kept separate rather than reusing that method directly so
    # the Dashboard's donut doesn't have to fetch (and wait on)
    # the P/E/P/B/ROE weighted-average computation it doesn't need.
    # ==========================================================

    @classmethod
    def calculate_market_cap_allocation(cls, user):
        """
        Return current-value allocation across every equity/other-
        investment Holding: Large/Mid/Small Cap for holdings with a
        real cap_type on their SecurityMaster row, and — in place of
        a single lumped "Unclassified" bucket — that same value
        broken down by its actual sub_class (Debt Mutual Fund,
        Liquid Mutual Fund, InvITs, REITs, Gold Bond, Private
        Equity, Unlisted, etc.).

        Uses the exact same sub_class classification as
        calculate_non_stock_holding_types (_normalize_asset_class
        over each holding's most recent transaction sub_class), so
        a holding always gets the identical label on both charts -
        this just folds that breakdown directly into the Market Cap
        chart instead of hiding it behind one "Unclassified" slice.
        calculate_non_stock_holding_types is left as-is: it remains
        useful as a focused, zoomed-in view of just that non-stock
        portion.
        """

        equity_holdings = list(
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        sm_by_asset_id = (
            cls._security_master_by_asset_id(user)
        )

        asset_class_by_asset_id = (
            cls._equity_asset_class_by_asset_id(user)
        )

        totals = {}

        for holding in equity_holdings:
            current_value = (
                holding.current_value
                or cls.ZERO
            )

            if current_value <= 0:
                continue

            sm = sm_by_asset_id.get(
                holding.asset_id,
                {},
            )

            cap_type = (
                sm.get("cap_type")
                or ""
            ).strip()

            if cap_type:
                label = cap_type
            else:
                raw_class = asset_class_by_asset_id.get(
                    holding.asset_id
                )

                label = cls._normalize_asset_class(
                    raw_class
                )

            totals[label] = (
                totals.get(
                    label,
                    cls.ZERO,
                )
                + current_value
            )

        grand_total = sum(
            totals.values(),
            cls.ZERO,
        )

        results = []

        for cap_type, value in sorted(
            totals.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            percentage = (
                (value / grand_total) * 100
                if grand_total
                else cls.ZERO
            )

            results.append({
                "cap_type": cap_type,
                "current_value": value,
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        return {
            "results": results,
            "total_current_value": grand_total,
        }

    # ==========================================================
    # NON-STOCK HOLDING TYPES (Dashboard, second donut)
    #
    # Large/Mid/Small Cap only applies to individual listed stocks
    # — mutual funds, ETFs, InvITs/REITs, bonds, and unlisted/
    # private holdings are structurally "Unclassified" on that
    # chart, correctly, since forcing a fund into a single cap
    # bucket would misrepresent it (a fund holds a blend of caps
    # internally). This gives that same set of holdings a REAL,
    # accurate classification instead: their transaction sub_class
    # (Debt Mutual Fund, Liquid Mutual Fund, InvITs, REITs, Gold
    # Bonds, Private Equity, Unlisted holdings, etc.) — already
    # correctly populated for every holding, no new data source
    # needed, unlike sector/cap_type/ratios elsewhere in this file.
    # ==========================================================

    @classmethod
    def calculate_non_stock_holding_types(cls, user):
        """
        Return current-value allocation by sub_class, restricted to
        holdings that have NO cap_type on their SecurityMaster row
        (i.e. exactly the "Unclassified" slice of
        calculate_market_cap_allocation) — the complementary chart
        to that one.
        """

        asset_class_by_asset_id = (
            cls._equity_asset_class_by_asset_id(user)
        )

        sm_by_asset_id = (
            cls._security_master_by_asset_id(user)
        )

        equity_holdings = list(
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        totals = {}

        for holding in equity_holdings:
            current_value = (
                holding.current_value
                or cls.ZERO
            )

            if current_value <= 0:
                continue

            sm = sm_by_asset_id.get(
                holding.asset_id,
                {},
            )

            if sm.get("cap_type"):
                # Has a real cap_type — belongs on the Market Cap
                # chart, not this one.
                continue

            raw_class = asset_class_by_asset_id.get(
                holding.asset_id
            )

            asset_class = cls._normalize_asset_class(
                raw_class
            )

            totals[asset_class] = (
                totals.get(
                    asset_class,
                    cls.ZERO,
                )
                + current_value
            )

        grand_total = sum(
            totals.values(),
            cls.ZERO,
        )

        results = []

        for asset_class, value in sorted(
            totals.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            percentage = (
                (value / grand_total) * 100
                if grand_total
                else cls.ZERO
            )

            results.append({
                "holding_type": asset_class,
                "current_value": value,
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        return {
            "results": results,
            "total_current_value": grand_total,
        }