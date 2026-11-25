"""Find and repair mutual funds that older imports stored as BOND/STOCK/CASH.

The importer used to check the broad asset class (Debt, Equity, Cash) before
the "Mutual Fund" sub-class, so a debt fund became category BOND and was sent
to the NSE bond provider every 15 minutes. The import is fixed; this module
corrects assets that already exist.

Functions take the model classes as arguments so the same code serves the data
migration (historical models) and the read-only report command.

Only ``Asset.category`` changes. Transactions, names, ISINs, holdings and
prices are untouched. Not changed (reported as conflicts instead):

* ETFs (category ETF, or " ETF"/"BEES" in the name);
* an asset whose family already has a MUTUAL_FUND asset with the same ISIN,
  because changing it would create two fund assets for one ISIN; merge those
  by hand.
"""

LEGACY_CATEGORIES = ("BOND", "STOCK", "CASH")


def _is_etf(asset):
    name = f" {(asset.name or '').upper()}"
    return asset.category == "ETF" or " ETF" in name or "BEES" in name


def find_mislabelled_funds(Asset, Transaction):
    fund_asset_ids = set(
        Transaction.objects.filter(sub_class__icontains="mutual fund").values_list("asset_id", flat=True)
    )
    candidates = (
        Asset.objects.filter(id__in=fund_asset_ids, category__in=LEGACY_CATEGORIES)
        .order_by("family_id", "name", "id")
    )
    existing_funds = set(
        Asset.objects.filter(category="MUTUAL_FUND")
        .exclude(isin__isnull=True)
        .exclude(isin="")
        .values_list("family_id", "isin")
    )
    change, conflicts = [], []
    for asset in candidates:
        item = {
            "asset_id": asset.id,
            "family_id": asset.family_id,
            "name": asset.name,
            "isin": asset.isin or "",
            "old_category": asset.category,
            "new_category": "MUTUAL_FUND",
        }
        if _is_etf(asset):
            conflicts.append({**item, "reason": "looks like an ETF by name; left unchanged"})
        elif asset.isin and (asset.family_id, asset.isin) in existing_funds:
            conflicts.append({**item, "reason": "family already has a MUTUAL_FUND asset with this ISIN; merge by hand"})
        else:
            change.append(item)
    return {"change": change, "conflicts": conflicts}


def apply_fund_category_repair(Asset, Transaction):
    """Apply the repair. Returns the number of assets changed."""
    changes = find_mislabelled_funds(Asset, Transaction)["change"]
    ids = [item["asset_id"] for item in changes]
    if not ids:
        return 0
    return Asset.objects.filter(id__in=ids, category__in=LEGACY_CATEGORIES).update(category="MUTUAL_FUND")


def find_likely_repaired_funds(Asset, Transaction, applied_at):
    """Read-only: MUTUAL_FUND assets that migration 0111 most likely changed.

    0111 used ``QuerySet.update()``, which leaves no trace on the row. An asset
    is listed when it was created before 0111 was applied (``applied_at``) and
    the pre-fix importer would have given its first transaction a BOND, STOCK
    or CASH category. Assets created since then are left out: the fixed
    importer made them MUTUAL_FUND directly.
    """
    from investments.services.transaction_import import TransactionImporter

    listed = []
    funds = Asset.objects.filter(category="MUTUAL_FUND", created_at__lt=applied_at).order_by("family_id", "name", "id")
    first_rows = {}
    for asset_id, asset_class, sub_class in (
        Transaction.objects.filter(asset__in=funds).order_by("asset_id", "id").values_list("asset_id", "asset_class", "sub_class")
    ):
        first_rows.setdefault(asset_id, (asset_class, sub_class))
    for asset in funds:
        if asset.id not in first_rows:
            continue
        legacy = TransactionImporter._legacy_internal_category(*first_rows[asset.id])
        if legacy in LEGACY_CATEGORIES:
            listed.append({
                "asset_id": asset.id,
                "family_id": asset.family_id,
                "name": asset.name,
                "isin": asset.isin or "",
                "old_category": legacy,
                "new_category": "MUTUAL_FUND",
            })
    return listed
