from django.db.models import Q
from investments.models import Asset
from mutual_funds.models import AMFIMasterScheme, AMFIMasterNAV
from mutual_funds.services.amfi_asset_resolver import AMFIAssetResolver

assets = list(Asset.objects.filter(is_active=True))
assets_by_id = {a.id: a for a in assets}

amfi_ids = AMFIAssetResolver.amfi_asset_ids(assets_by_id)

print("\nTOTAL ACTIVE ASSETS:", len(assets))
print("AMFI RESOLVER ASSETS:", len(amfi_ids))

mf_assets = [
    a for a in assets
    if a.id in amfi_ids
]

print("\n========== RESOLVED MUTUAL FUNDS ==========")

isins = {
    str(a.isin).strip().upper()
    for a in mf_assets
    if a.isin
}

schemes = AMFIMasterScheme.objects.filter(
    is_active=True
).filter(
    Q(isin_growth__in=isins) |
    Q(isin_dividend__in=isins)
)

scheme_map = {}

for scheme in schemes:
    for isin in (scheme.isin_growth, scheme.isin_dividend):
        if isin:
            scheme_map[isin.strip().upper()] = scheme

matched = 0
unmatched = 0

for asset in mf_assets:
    isin = (asset.isin or "").strip().upper()
    scheme = scheme_map.get(isin)

    if not scheme:
        unmatched += 1
        print("NO MATCH:", asset.id, asset.name, isin)
        continue

    matched += 1

    nav = AMFIMasterNAV.objects.filter(
        scheme=scheme,
        nav__gt=0,
        source="AMFI"
    ).order_by("-date", "-id").first()

    print(
        "MATCH:",
        asset.id,
        asset.name,
        isin,
        scheme.scheme_code,
        nav.nav if nav else None,
        nav.date if nav else None
    )

print("\n========== FINAL SUMMARY ==========")
print("Resolved MF assets:", len(mf_assets))
print("ISIN matched:", matched)
print("ISIN unmatched:", unmatched)
