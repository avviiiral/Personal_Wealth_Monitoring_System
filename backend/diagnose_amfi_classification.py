from investments.models import Asset
from mutual_funds.services.amfi_asset_resolver import AMFIAssetResolver
from django.db.models import Count

assets = list(
    Asset.objects.filter(is_active=True).select_related("security_master")
)
assets_by_id = {a.id: a for a in assets}
amfi_ids = AMFIAssetResolver.amfi_asset_ids(assets_by_id)

print("\n========== AMFI ASSET CLASSIFICATION ==========")
for asset in assets:
    if asset.id not in amfi_ids:
        continue
    print({
        "id": asset.id,
        "name": asset.name,
        "category": asset.category,
        "isin": asset.isin,
        "security_master_isin": (
            asset.security_master.isin
            if asset.security_master_id else None
        ),
    })

print("\n========== ALL ACTIVE ASSET CATEGORIES ==========")
for row in (
    Asset.objects.filter(is_active=True)
    .values("category")
    .annotate(count=Count("id"))
    .order_by("category")
):
    print(row)
