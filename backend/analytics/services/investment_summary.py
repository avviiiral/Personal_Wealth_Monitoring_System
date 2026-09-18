import logging
from decimal import Decimal

from django.db.models import Q

from investments.models import Transaction, TransactionType
from mutual_funds.models import MutualFundTransaction

from .unified_wealth import UnifiedWealthAnalytics
from users.permissions import get_active_family_group, is_system_owner


logger = logging.getLogger(__name__)


class InvestmentSummaryService:
    """
    Builds the Dashboard "Investment Summary" table: current value and
    percentage-of-total for every Asset Class in the fixed master
    mapping below, grouped by Asset Category.
    """

    ZERO = Decimal("0")

    @staticmethod
    def _scope_q(user):
        if is_system_owner(user):
            return Q()
        family = get_active_family_group(user)
        if family is None:
            return Q(pk__in=[])
        owner_ids = InvestmentSummaryService._owner_ids(user)
        return Q(family_id=family.id) | Q(family_id__isnull=True, owner_id__in=owner_ids)

    @staticmethod
    def _owner_ids(user):
        return [user.pk] if hasattr(user, "pk") else list(user)

    MASTER_MAPPING = [
        (
            "Other",
            ["Unlisted"],
        ),
        (
            "Alternate",
            ["Commodity", "Private Equity", "REITs", "InvITs"],
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
            ["Debt Mutual Fund", "Gold Bond"],
        ),
        (
            "Liquids",
            ["Liquid Mutual Fund", "Arbitrage Mutual Fund"],
        ),
    ]

    EQUITY_ASSET_CLASSES = {
        "Direct Equity",
        "Equity PMS",
        "Equity AIF",
        "Equity Mutual Fund",
        "Equity LRS",
    }

    FALLBACK_ASSET_CLASS = "Unlisted"

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
                    "Investment Summary: %r matched via keyword %r -> %r.",
                    raw_value,
                    keyword,
                    canonical,
                )
                return canonical

        logger.warning(
            "Investment Summary: unrecognised asset-class classification %r; "
            "bucketed under Other / Unlisted.",
            raw_value,
        )
        return cls.FALLBACK_ASSET_CLASS

    @staticmethod
    def _equity_asset_class_by_asset_id(user, family_name=None):
        rows_qs = (
            Transaction.objects
            .filter(InvestmentSummaryService._scope_q(user))
            .exclude(sub_class__isnull=True)
            .exclude(sub_class__exact="")
        )

        if family_name:
            rows_qs = rows_qs.filter(family_name=family_name)

        rows = (
            rows_qs
            .order_by("asset_id", "-transaction_date", "-created_at", "-id")
            .values_list("asset_id", "sub_class")
        )

        resolved = {}
        for asset_id, sub_class in rows:
            if asset_id not in resolved:
                resolved[asset_id] = sub_class
        return resolved

    @staticmethod
    def _equity_asset_class_weights_by_asset_id(user, family_name=None):
        rows_qs = (
            Transaction.objects
            .filter(InvestmentSummaryService._scope_q(user))
            .exclude(sub_class__isnull=True)
            .exclude(sub_class__exact="")
        )

        if family_name:
            rows_qs = rows_qs.filter(family_name=family_name)

        rows = rows_qs.values_list(
            "asset_id", "sub_class", "transaction_type", "quantity"
        )

        net_quantity = {}
        for asset_id, sub_class, transaction_type, quantity in rows:
            key = (asset_id, sub_class)
            net_quantity.setdefault(key, Decimal("0"))
            quantity = quantity or Decimal("0")
            if transaction_type == TransactionType.SELL:
                net_quantity[key] -= quantity
            else:
                net_quantity[key] += quantity

        totals_by_asset = {}
        for (asset_id, sub_class), qty in net_quantity.items():
            if qty <= 0:
                continue
            totals_by_asset.setdefault(asset_id, {})[sub_class] = qty

        weights = {}
        for asset_id, class_quantities in totals_by_asset.items():
            asset_total = sum(class_quantities.values(), Decimal("0"))
            if asset_total <= 0:
                continue
            weights[asset_id] = {
                sub_class: qty / asset_total
                for sub_class, qty in class_quantities.items()
            }
        return weights

    @classmethod
    def _family_equity_positions(cls, user, family_name):
        from .historical_wealth import HistoricalWealthAnalytics

        transactions = (
            Transaction.objects
            .filter(
                InvestmentSummaryService._scope_q(user),
                family_name=family_name,
            )
            .select_related("asset__holding")
            .order_by("asset_id", "transaction_date", "created_at", "id")
        )

        positions = {}
        assets_by_id = {}

        for transaction in transactions:
            assets_by_id[transaction.asset_id] = transaction.asset
            position = positions.setdefault(
                transaction.asset_id,
                {"quantity": cls.ZERO, "invested_value": cls.ZERO},
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
            current_price = getattr(holding, "current_price", None) if holding else None
            if current_price is None:
                continue
            results.append((asset_id, position["quantity"] * current_price))

        return results

    @classmethod
    def _family_mutual_fund_positions(cls, user, family_name):
        from .historical_wealth import HistoricalWealthAnalytics

        transactions = (
            MutualFundTransaction.objects
            .filter(
                InvestmentSummaryService._scope_q(user),
                family_name=family_name,
            )
            .select_related("scheme__holding")
            .order_by("scheme_id", "transaction_date", "created_at", "id")
        )

        positions = {}
        schemes_by_id = {}

        for transaction in transactions:
            schemes_by_id[transaction.scheme_id] = transaction.scheme
            position = positions.setdefault(
                transaction.scheme_id,
                {"units": cls.ZERO, "invested_value": cls.ZERO},
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
            current_nav = getattr(holding, "current_nav", None) if holding else None
            if current_nav is None:
                continue
            results.append((scheme, position["units"] * current_nav))

        return results

    @classmethod
    def _build_results(cls, totals, raw_values_by_asset_class):
        total_current_value = sum(totals.values(), cls.ZERO)
        results = []

        for category, asset_classes in cls.MASTER_MAPPING:
            for asset_class in asset_classes:
                value = totals[asset_class]
                percentage = (
                    (value / total_current_value) * 100
                    if total_current_value
                    else cls.ZERO
                )
                results.append({
                    "asset_category": category,
                    "asset_class": asset_class,
                    "current_value": value,
                    "percentage_of_total": round(percentage, 2),
                    "raw_asset_classes": sorted(raw_values_by_asset_class[asset_class]),
                })

        return {
            "results": results,
            "total_current_value": total_current_value,
        }

    @classmethod
    def calculate(cls, user, family_name=None):
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
            asset_class_by_asset_id = cls._equity_asset_class_by_asset_id(user)
            for holding in UnifiedWealthAnalytics.get_equity_holdings(user):
                value = holding.current_value or cls.ZERO
                raw_class = asset_class_by_asset_id.get(holding.asset_id)
                asset_class = cls._normalize_asset_class(raw_class)
                totals[asset_class] += value
                if raw_class:
                    raw_values_by_asset_class[asset_class].add(raw_class)

            for holding in UnifiedWealthAnalytics.get_mutual_fund_holdings(user):
                value = holding.current_value or cls.ZERO
                raw_class = getattr(holding.scheme, "category", None)
                asset_class = cls._normalize_asset_class(raw_class)
                totals[asset_class] += value
                if raw_class:
                    raw_values_by_asset_class[asset_class].add(raw_class)
            return cls._build_results(totals, raw_values_by_asset_class)

        asset_class_by_asset_id = cls._equity_asset_class_by_asset_id(
            user, family_name=family_name
        )
        for asset_id, value in cls._family_equity_positions(user, family_name):
            raw_class = asset_class_by_asset_id.get(asset_id)
            asset_class = cls._normalize_asset_class(raw_class)
            totals[asset_class] += value
            if raw_class:
                raw_values_by_asset_class[asset_class].add(raw_class)

        for scheme, value in cls._family_mutual_fund_positions(user, family_name):
            raw_class = getattr(scheme, "category", None)
            asset_class = cls._normalize_asset_class(raw_class)
            totals[asset_class] += value
            if raw_class:
                raw_values_by_asset_class[asset_class].add(raw_class)

        return cls._build_results(totals, raw_values_by_asset_class)

    @classmethod
    def calculate_performance_by_subclass(cls, user):
        totals = {
            asset_class: {"invested": cls.ZERO, "current": cls.ZERO}
            for _, asset_classes in cls.MASTER_MAPPING
            for asset_class in asset_classes
        }
        category_by_asset_class = {
            asset_class: category
            for category, asset_classes in cls.MASTER_MAPPING
            for asset_class in asset_classes
        }
        asset_class_by_asset_id = cls._equity_asset_class_by_asset_id(user)

        for holding in UnifiedWealthAnalytics.get_equity_holdings(user):
            raw_class = asset_class_by_asset_id.get(holding.asset_id)
            asset_class = cls._normalize_asset_class(raw_class)
            totals[asset_class]["invested"] += holding.invested_value or cls.ZERO
            totals[asset_class]["current"] += holding.current_value or cls.ZERO

        for holding in UnifiedWealthAnalytics.get_mutual_fund_holdings(user):
            raw_class = getattr(holding.scheme, "category", None)
            asset_class = cls._normalize_asset_class(raw_class)
            totals[asset_class]["invested"] += holding.invested_value or cls.ZERO
            totals[asset_class]["current"] += holding.current_value or cls.ZERO

        results = []
        for category, asset_classes in cls.MASTER_MAPPING:
            for asset_class in asset_classes:
                invested = totals[asset_class]["invested"]
                current = totals[asset_class]["current"]
                if not invested and not current:
                    continue
                pnl = current - invested
                pnl_percentage = (pnl / invested) * 100 if invested else cls.ZERO
                results.append({
                    "asset_category": category,
                    "asset_class": asset_class,
                    "invested_value": invested,
                    "current_value": current,
                    "unrealized_pnl": pnl,
                    "pnl_percentage": round(pnl_percentage, 2),
                })
        return sorted(results, key=lambda item: item["pnl_percentage"], reverse=True)

    UNASSIGNED_ADVISOR = "Unassigned"

    @staticmethod
    def _advisor_by_asset_id(user):
        rows = (
            Transaction.objects
            .filter(InvestmentSummaryService._scope_q(user))
            .exclude(advisors__isnull=True)
            .exclude(advisors__exact="")
            .order_by("asset_id", "-transaction_date", "-created_at", "-id")
            .values_list("asset_id", "advisors")
        )
        resolved = {}
        for asset_id, advisor in rows:
            if asset_id not in resolved:
                resolved[asset_id] = advisor
        return resolved

    @classmethod
    def calculate_allocation_by_advisor(cls, user):
        totals = {}
        advisor_by_asset_id = cls._advisor_by_asset_id(user)
        for holding in UnifiedWealthAnalytics.get_equity_holdings(user):
            advisor = (advisor_by_asset_id.get(holding.asset_id) or "").strip() or cls.UNASSIGNED_ADVISOR
            totals[advisor] = totals.get(advisor, cls.ZERO) + (holding.current_value or cls.ZERO)

        mutual_fund_value = sum(
            (holding.current_value or cls.ZERO)
            for holding in UnifiedWealthAnalytics.get_mutual_fund_holdings(user)
        )
        if mutual_fund_value:
            totals[cls.UNASSIGNED_ADVISOR] = totals.get(cls.UNASSIGNED_ADVISOR, cls.ZERO) + mutual_fund_value

        total_value = sum(totals.values(), cls.ZERO)
        results = []
        for advisor, value in totals.items():
            if value <= 0:
                continue
            percentage = (value / total_value) * 100 if total_value else cls.ZERO
            results.append({"advisor": advisor, "value": value, "percentage": round(percentage, 2)})

        return {
            "results": sorted(results, key=lambda item: item["value"], reverse=True),
            "total_current_value": total_value,
        }

    @classmethod
    def calculate_performance_by_advisor(cls, user):
        totals = {}
        advisor_by_asset_id = cls._advisor_by_asset_id(user)
        for holding in UnifiedWealthAnalytics.get_equity_holdings(user):
            advisor = (advisor_by_asset_id.get(holding.asset_id) or "").strip() or cls.UNASSIGNED_ADVISOR
            totals.setdefault(advisor, {"invested": cls.ZERO, "current": cls.ZERO})
            totals[advisor]["invested"] += holding.invested_value or cls.ZERO
            totals[advisor]["current"] += holding.current_value or cls.ZERO

        for holding in UnifiedWealthAnalytics.get_mutual_fund_holdings(user):
            totals.setdefault(cls.UNASSIGNED_ADVISOR, {"invested": cls.ZERO, "current": cls.ZERO})
            totals[cls.UNASSIGNED_ADVISOR]["invested"] += holding.invested_value or cls.ZERO
            totals[cls.UNASSIGNED_ADVISOR]["current"] += holding.current_value or cls.ZERO

        results = []
        for advisor, entry in totals.items():
            invested = entry["invested"]
            current = entry["current"]
            if not invested and not current:
                continue
            pnl = current - invested
            pnl_percentage = (pnl / invested) * 100 if invested else cls.ZERO
            results.append({
                "advisor": advisor,
                "invested_value": invested,
                "current_value": current,
                "unrealized_pnl": pnl,
                "pnl_percentage": round(pnl_percentage, 2),
            })
        return sorted(results, key=lambda item: item["pnl_percentage"], reverse=True)

    UNASSIGNED_AMC = "Unassigned"

    @staticmethod
    def _amc_by_equity_asset_id(user):
        from investments.models import Asset
        rows = (
            Asset.objects
            .filter(
                InvestmentSummaryService._scope_q(user),
                security_master__amc_name__isnull=False,
            )
            .exclude(security_master__amc_name__exact="")
            .values_list("id", "security_master__amc_name")
        )
        return dict(rows)

    @classmethod
    def calculate_composition_by_amc(cls, user):
        totals = {}
        amc_by_asset_id = cls._amc_by_equity_asset_id(user)
        for holding in UnifiedWealthAnalytics.get_equity_holdings(user):
            amc = (amc_by_asset_id.get(holding.asset_id) or "").strip() or cls.UNASSIGNED_AMC
            totals.setdefault(amc, {"invested": cls.ZERO, "current": cls.ZERO, "holding_count": 0})
            totals[amc]["invested"] += holding.invested_value or cls.ZERO
            totals[amc]["current"] += holding.current_value or cls.ZERO
            totals[amc]["holding_count"] += 1

        for holding in UnifiedWealthAnalytics.get_mutual_fund_holdings(user):
            amc = (getattr(holding.scheme, "amc_name", None) or "").strip() or cls.UNASSIGNED_AMC
            totals.setdefault(amc, {"invested": cls.ZERO, "current": cls.ZERO, "holding_count": 0})
            totals[amc]["invested"] += holding.invested_value or cls.ZERO
            totals[amc]["current"] += holding.current_value or cls.ZERO
            totals[amc]["holding_count"] += 1

        grand_total = sum((entry["current"] for entry in totals.values()), cls.ZERO)
        results = []
        for amc, entry in totals.items():
            current = entry["current"]
            if current <= 0:
                continue
            percentage = (current / grand_total) * 100 if grand_total else cls.ZERO
            results.append({
                "amc_name": amc,
                "invested_value": entry["invested"],
                "current_value": current,
                "holding_count": entry["holding_count"],
                "percentage": round(percentage, 2),
            })
        return {
            "results": sorted(results, key=lambda item: item["current_value"], reverse=True),
            "total_current_value": grand_total,
            "number_of_amcs": len(results),
        }

    @staticmethod
    def _security_master_by_asset_id(user):
        from investments.models import Asset
        rows = (
            Asset.objects
            .filter(
                InvestmentSummaryService._scope_q(user),
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
        equity_holdings = list(UnifiedWealthAnalytics.get_equity_holdings(user))
        sm_by_asset_id = cls._security_master_by_asset_id(user)
        total_current_value = sum((holding.current_value or cls.ZERO) for holding in equity_holdings)
        cap_totals = {}
        weighted_sums = {"pe_ratio": cls.ZERO, "pb_ratio": cls.ZERO, "roe": cls.ZERO}
        weighted_bases = {"pe_ratio": cls.ZERO, "pb_ratio": cls.ZERO, "roe": cls.ZERO}
        weighted_counts = {"pe_ratio": 0, "pb_ratio": 0, "roe": 0}

        for holding in equity_holdings:
            current_value = holding.current_value or cls.ZERO
            sm = sm_by_asset_id.get(holding.asset_id, {})
            cap_type = (sm.get("cap_type") or "").strip() or "Unclassified"
            cap_totals[cap_type] = cap_totals.get(cap_type, cls.ZERO) + current_value
            for field in ("pe_ratio", "pb_ratio", "roe"):
                value = sm.get(field)
                if value is None or current_value <= 0:
                    continue
                weighted_sums[field] += value * current_value
                weighted_bases[field] += current_value
                weighted_counts[field] += 1

        market_cap_allocation = []
        for cap_type, value in sorted(cap_totals.items(), key=lambda item: item[1], reverse=True):
            percentage = (value / total_current_value) * 100 if total_current_value else cls.ZERO
            market_cap_allocation.append({
                "cap_type": cap_type,
                "current_value": value,
                "percentage": round(percentage, 2),
            })

        def weighted_average(field):
            base = weighted_bases[field]
            if not base:
                return None
            return round(weighted_sums[field] / base, 2)

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

    @staticmethod
    def _fixed_income_security_master_by_asset_id(user):
        from investments.models import Asset
        rows = (
            Asset.objects
            .filter(
                InvestmentSummaryService._scope_q(user),
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
            for asset_id, credit_rating, ytm, modified_duration, average_maturity in rows
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
        asset_class_weights_by_asset_id = cls._equity_asset_class_weights_by_asset_id(user)
        fixed_income_classes = set()
        for category, asset_classes in cls.MASTER_MAPPING:
            if category == "Fixed Income":
                fixed_income_classes.update(asset_classes)

        equity_holdings = list(UnifiedWealthAnalytics.get_equity_holdings(user))
        fi_holdings = []
        for holding in equity_holdings:
            class_weights = asset_class_weights_by_asset_id.get(holding.asset_id)
            if not class_weights:
                continue
            fi_weight = sum(
                weight
                for raw_class, weight in class_weights.items()
                if cls._normalize_asset_class(raw_class) in fixed_income_classes
            )
            if fi_weight > 0:
                fi_holdings.append((holding, fi_weight))

        sm_by_asset_id = cls._fixed_income_security_master_by_asset_id(user)
        total_current_value = sum(
            (holding.current_value or cls.ZERO) * fi_weight
            for holding, fi_weight in fi_holdings
        )
        rating_totals = {}
        weighted_sums = {"ytm": cls.ZERO, "modified_duration": cls.ZERO, "average_maturity": cls.ZERO}
        weighted_bases = {"ytm": cls.ZERO, "modified_duration": cls.ZERO, "average_maturity": cls.ZERO}
        weighted_counts = {"ytm": 0, "modified_duration": 0, "average_maturity": 0}

        for holding, fi_weight in fi_holdings:
            current_value = (holding.current_value or cls.ZERO) * fi_weight
            sm = sm_by_asset_id.get(holding.asset_id, {})
            rating_label = cls.CREDIT_RATING_LABELS.get(sm.get("credit_rating"), "Unrated")
            rating_totals[rating_label] = rating_totals.get(rating_label, cls.ZERO) + current_value

            for field in ("ytm", "modified_duration", "average_maturity"):
                value = sm.get(field)
                if value is None or current_value <= 0:
                    continue
                weighted_sums[field] += value * current_value
                weighted_bases[field] += current_value
                weighted_counts[field] += 1

        rating_distribution = []
        for rating, value in sorted(rating_totals.items(), key=lambda item: item[1], reverse=True):
            percentage = (value / total_current_value) * 100 if total_current_value else cls.ZERO
            rating_distribution.append({
                "credit_rating": rating,
                "current_value": value,
                "percentage": round(percentage, 2),
            })

        def weighted_average(field):
            base = weighted_bases[field]
            if not base:
                return None
            return round(weighted_sums[field] / base, 2)

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

    @classmethod
    def calculate_sector_allocation(cls, user):
        """Return current-value sector allocation for Asset Class Equity only."""
        asset_class_by_asset_id = cls._equity_asset_class_by_asset_id(user)
        equity_holdings = list(UnifiedWealthAnalytics.get_equity_holdings(user))
        sm_by_asset_id = cls._security_master_by_asset_id(user)
        totals = {}

        for holding in equity_holdings:
            current_value = holding.current_value or cls.ZERO
            if current_value <= 0:
                continue

            asset_class = cls._normalize_asset_class(
                asset_class_by_asset_id.get(holding.asset_id)
            )
            if asset_class not in cls.EQUITY_ASSET_CLASSES:
                continue

            sm = sm_by_asset_id.get(holding.asset_id, {})
            sector = (sm.get("sector") or "").strip() or "Unclassified"
            totals[sector] = totals.get(sector, cls.ZERO) + current_value

        grand_total = sum(totals.values(), cls.ZERO)
        results = []
        for sector, value in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            percentage = (value / grand_total) * 100 if grand_total else cls.ZERO
            results.append({
                "sector": sector,
                "current_value": value,
                "percentage": round(percentage, 2),
            })
        return {"results": results, "total_current_value": grand_total}

    @classmethod
    def calculate_market_cap_allocation(cls, user):
        """Return current-value market-cap allocation for Asset Class Equity only."""
        asset_class_by_asset_id = cls._equity_asset_class_by_asset_id(user)
        equity_holdings = list(UnifiedWealthAnalytics.get_equity_holdings(user))
        sm_by_asset_id = cls._security_master_by_asset_id(user)
        totals = {}

        for holding in equity_holdings:
            current_value = holding.current_value or cls.ZERO
            if current_value <= 0:
                continue

            asset_class = cls._normalize_asset_class(
                asset_class_by_asset_id.get(holding.asset_id)
            )
            if asset_class not in cls.EQUITY_ASSET_CLASSES:
                continue

            sm = sm_by_asset_id.get(holding.asset_id, {})
            cap_type = (sm.get("cap_type") or "").strip() or "Unclassified"
            totals[cap_type] = totals.get(cap_type, cls.ZERO) + current_value

        grand_total = sum(totals.values(), cls.ZERO)
        results = []
        for cap_type, value in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            percentage = (value / grand_total) * 100 if grand_total else cls.ZERO
            results.append({
                "cap_type": cap_type,
                "current_value": value,
                "percentage": round(percentage, 2),
            })
        return {"results": results, "total_current_value": grand_total}

    @classmethod
    def calculate_non_stock_holding_types(cls, user):
        asset_class_by_asset_id = cls._equity_asset_class_by_asset_id(user)
        sm_by_asset_id = cls._security_master_by_asset_id(user)
        equity_holdings = list(UnifiedWealthAnalytics.get_equity_holdings(user))
        totals = {}

        for holding in equity_holdings:
            current_value = holding.current_value or cls.ZERO
            if current_value <= 0:
                continue
            sm = sm_by_asset_id.get(holding.asset_id, {})
            if sm.get("cap_type"):
                continue
            asset_class = cls._normalize_asset_class(asset_class_by_asset_id.get(holding.asset_id))
            totals[asset_class] = totals.get(asset_class, cls.ZERO) + current_value

        grand_total = sum(totals.values(), cls.ZERO)
        results = []
        for asset_class, value in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            percentage = (value / grand_total) * 100 if grand_total else cls.ZERO
            results.append({
                "holding_type": asset_class,
                "current_value": value,
                "percentage": round(percentage, 2),
            })
        return {"results": results, "total_current_value": grand_total}
