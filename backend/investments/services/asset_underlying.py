from decimal import Decimal, InvalidOperation
import re

import pandas as pd
from django.db import transaction
from rest_framework.exceptions import ValidationError

from investments.models import Asset, AssetUnderlyingHolding, SecurityMaster
from users.permissions import require_active_family


class AssetUnderlyingImportError(ValueError):
    pass


class AssetUnderlyingImporter:
    """Import a two-column underlying snapshot for one family-owned asset."""

    STOCK_ALIASES = {
        "stock", "stocks", "stock name", "security", "security name", "company", "company name",
    }
    PERCENT_ALIASES = {
        "% holding", "% of holding", "holding %", "holding percentage", "holding %age",
        "percentage", "percentage of holding", "%",
    }

    @staticmethod
    def _normalize_header(value):
        return " ".join(str(value or "").strip().lower().replace("_", " ").split())

    @classmethod
    def _find_column(cls, columns, aliases):
        normalized = {cls._normalize_header(column): column for column in columns}
        for alias in aliases:
            if alias in normalized:
                return normalized[alias]
        return None

    @staticmethod
    def _normalize_name(value):
        value = str(value or "").strip().upper()
        value = re.sub(r"[.&,'’`]", " ", value)
        value = re.sub(r"\b(LIMITED|LTD|LTD\.|PRIVATE|PVT|PLC)\b", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    @classmethod
    def _compact_name(cls, value):
        return re.sub(r"[^A-Z0-9]", "", cls._normalize_name(value))

    @classmethod
    def _classification_maps(cls, family):
        masters = SecurityMaster.objects.filter(family=family).only("asset_name", "isin", "sector", "cap_type")
        by_name = {}
        by_compact_name = {}
        by_isin = {}
        for master in masters:
            name_key = cls._normalize_name(master.asset_name)
            if name_key:
                by_name.setdefault(name_key, master)
                by_compact_name.setdefault(cls._compact_name(master.asset_name), master)
            isin_key = cls._normalize_name(master.isin)
            if isin_key:
                by_isin.setdefault(isin_key, master)
        return by_name, by_compact_name, by_isin

    @classmethod
    def _resolve_classification(cls, stock_name, family, by_name, by_compact_name, by_isin):
        key = cls._normalize_name(stock_name)
        master = by_name.get(key)
        if master is None:
            master = by_compact_name.get(cls._compact_name(stock_name))
        if master is None:
            stock_compact = cls._compact_name(stock_name)
            candidates = [
                candidate for candidate in by_compact_name.values()
                if len(cls._compact_name(candidate.asset_name)) >= 8
                and (
                    stock_compact.startswith(cls._compact_name(candidate.asset_name))
                    or cls._compact_name(candidate.asset_name).startswith(stock_compact)
                )
            ]
            if len(candidates) == 1:
                master = candidates[0]
        if master is None:
            asset = Asset.objects.filter(family=family, name__iexact=str(stock_name).strip()).select_related("security_master").first()
            master = getattr(asset, "security_master", None)
        return (
            (master.sector or "").strip() or None if master else None,
            (master.cap_type or "").strip() or None if master else None,
        )

    @classmethod
    def import_file(cls, file, asset_id, owner):
        family = require_active_family(owner)
        asset = Asset.objects.filter(id=asset_id, family=family, is_active=True).first()
        if asset is None:
            raise AssetUnderlyingImportError("Selected asset was not found in your active family.")

        filename = str(getattr(file, "name", "")).lower()
        if not filename.endswith(".xlsx"):
            raise AssetUnderlyingImportError("Only .xlsx files are supported for underlying uploads.")

        try:
            frame = pd.read_excel(file)
        except Exception as exc:
            raise AssetUnderlyingImportError(f"Unable to read the Excel file: {exc}") from exc

        if frame.empty:
            raise AssetUnderlyingImportError("The Excel file does not contain any underlying rows.")

        stock_column = cls._find_column(frame.columns, cls.STOCK_ALIASES)
        percent_column = cls._find_column(frame.columns, cls.PERCENT_ALIASES)
        if stock_column is None or percent_column is None:
            raise AssetUnderlyingImportError("Excel must contain 'Stocks' and '% Holding' columns.")

        rows = []
        by_name, by_compact_name, by_isin = cls._classification_maps(family)
        for index, raw in frame.iterrows():
            stock_name = str(raw.get(stock_column, "")).strip()
            if not stock_name or stock_name.lower() == "nan":
                continue
            raw_percentage = raw.get(percent_column)
            try:
                if pd.isna(raw_percentage):
                    raise ValueError
                percentage = Decimal(str(raw_percentage).replace("%", "").strip())
                if Decimal("0") <= percentage <= Decimal("1"):
                    percentage *= Decimal("100")
            except (InvalidOperation, TypeError, ValueError):
                raise AssetUnderlyingImportError(f"Invalid holding percentage on Excel row {index + 2}.")
            if percentage < 0 or percentage > 100:
                raise AssetUnderlyingImportError(f"Holding percentage must be between 0 and 100 on Excel row {index + 2}.")
            sector, cap_type = cls._resolve_classification(
                stock_name, family, by_name, by_compact_name, by_isin
            )
            rows.append(
                AssetUnderlyingHolding(
                    owner=owner,
                    family=family,
                    asset=asset,
                    stock_name=stock_name,
                    holding_percentage=percentage.quantize(Decimal("0.0001")),
                    sector=sector,
                    cap_type=cap_type,
                )
            )

        if not rows:
            raise AssetUnderlyingImportError("No valid underlying rows were found in the Excel file.")

        with transaction.atomic():
            AssetUnderlyingHolding.objects.filter(family=family, asset=asset).delete()
            AssetUnderlyingHolding.objects.bulk_create(rows)

        return {
            "asset_id": asset.id,
            "asset_name": asset.name,
            "rows_imported": len(rows),
            "unclassified_rows": sum(1 for row in rows if not row.sector or not row.cap_type),
        }


class AssetUnderlyingService:
    @staticmethod
    def latest_by_asset(user):
        family = require_active_family(user)
        rows = AssetUnderlyingHolding.objects.filter(family=family).select_related("asset")
        result = {}
        for row in rows:
            result.setdefault(row.asset_id, []).append(row)
        return result

    @classmethod
    def allocation_by_classification(cls, user, classification):
        """Return underlying exposure weighted by the current value of each parent asset."""
        rows_by_asset = cls.latest_by_asset(user)
        totals = {}
        for asset_id, rows in rows_by_asset.items():
            holding = getattr(rows[0].asset, "holding", None)
            if holding is None or not holding.current_value or holding.current_value <= 0:
                continue
            parent_value = holding.current_value
            for row in rows:
                label = getattr(row, classification) or "Unclassified"
                exposure = parent_value * (row.holding_percentage or Decimal("0")) / Decimal("100")
                totals[label] = totals.get(label, Decimal("0")) + exposure
        return totals

    @classmethod
    def has_uploaded_underlying(cls, asset_id, family):
        return AssetUnderlyingHolding.objects.filter(asset_id=asset_id, family=family).exists()
