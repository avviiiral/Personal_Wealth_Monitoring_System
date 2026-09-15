from django.db import transaction

from .underlying import MutualFundUnderlyingService
from mutual_funds.models import MutualFundUnderlying


class OfficialMutualFundUnderlyingService(MutualFundUnderlyingService):
    """AMFI/AMC importer that also preserves the disclosed industry/sector."""

    @classmethod
    def _parse_dataframe(cls, dataframe, fallback_date=None):
        dataframe = dataframe.dropna(how="all").copy()
        if dataframe.empty:
            return []
        dataframe.columns = [cls.normalize_text(column) for column in dataframe.columns]
        name_col = cls._find_column(dataframe.columns, "security_name")
        if not name_col:
            return []
        isin_col = cls._find_column(dataframe.columns, "isin")
        quantity_col = cls._find_column(dataframe.columns, "quantity")
        value_col = cls._find_column(dataframe.columns, "market_value")
        pct_col = cls._find_column(dataframe.columns, "percentage_of_nav")
        sector_col = next(
            (
                column for column in dataframe.columns
                if any(token in cls.normalize_column(column) for token in ("industry", "sector"))
            ),
            None,
        )

        rows = []
        for _, row in dataframe.iterrows():
            security_name = cls.normalize_text(row.get(name_col))
            if not security_name:
                continue
            lower = security_name.lower()
            if lower in {"total", "grand total", "total investments"} or lower.startswith("total "):
                continue
            isin = cls.normalize_text(row.get(isin_col)) if isin_col else None
            quantity = cls._decimal(row.get(quantity_col)) if quantity_col else None
            market_value = cls._decimal(row.get(value_col)) if value_col else None
            percentage = cls._decimal(row.get(pct_col)) if pct_col else None
            if percentage is None or percentage < 0 or percentage > 100:
                continue
            sector = cls.normalize_text(row.get(sector_col)) if sector_col else None
            if sector in {"-", "N.A.", "NA", "N/A"}:
                sector = None
            rows.append({
                "security_name": security_name,
                "isin": isin or None,
                "security_key": cls.normalize_security_key(security_name, isin),
                "quantity": quantity,
                "market_value": market_value,
                "percentage_of_nav": percentage,
                "sector": sector,
                "portfolio_date": fallback_date,
            })
        return rows

    @classmethod
    @transaction.atomic
    def import_document(cls, scheme, content, filename, source_reference, fallback_date=None):
        records = cls.parse_document(content, filename, fallback_date=fallback_date)
        if not records:
            raise ValueError(f"No valid portfolio rows found in {filename}.")
        portfolio_date = next((row["portfolio_date"] for row in records if row["portfolio_date"]), None)
        if portfolio_date is None:
            raise ValueError(f"Could not determine portfolio date for {filename}.")

        existing_count = MutualFundUnderlying.objects.filter(
            scheme=scheme,
            portfolio_date=portfolio_date,
            source=cls.SOURCE,
        ).count()
        if existing_count:
            return {"status": "already_imported", "portfolio_date": portfolio_date, "records": existing_count}

        objects = [
            MutualFundUnderlying(
                scheme=scheme,
                security_name=row["security_name"],
                isin=row["isin"],
                security_key=row["security_key"],
                quantity=row["quantity"],
                market_value=row["market_value"],
                percentage_of_nav=row["percentage_of_nav"],
                sector=row.get("sector"),
                portfolio_date=portfolio_date,
                source=cls.SOURCE,
                source_reference=source_reference,
            )
            for row in records
        ]
        MutualFundUnderlying.objects.bulk_create(objects, ignore_conflicts=True, batch_size=500)
        return {"status": "imported", "portfolio_date": portfolio_date, "records": len(objects)}
