from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from mf_intelligence.models import MFScheme
from mutual_funds.models import AMFIMasterNAV, MutualFundNAV
from mutual_funds.services.amfi import AMFIService


class MutualFundNAVService:

    @staticmethod
    def get_nav_for_date(scheme, target_date):
        """
        Return the latest NAV on or before target_date.

        Use the newest eligible observation from family history, the global
        AMFI master and a recent MF Intelligence NAV. AMFI wins same-date ties;
        synced NAVs are read-through only and are not copied into family history.
        """
        today = timezone.localdate()
        family_nav = (
            MutualFundNAV.objects
            .filter(scheme=scheme, date__lte=target_date, nav__gt=0)
            .order_by("-date", "-id")
            .first()
        )
        master_scheme = (
            AMFIMasterNAV.objects
            .filter(
                scheme__scheme_code=scheme.scheme_code,
                date__lte=target_date,
                source="AMFI",
                nav__gt=0,
            )
            .select_related("scheme")
            .order_by("-date", "-id")
            .first()
        )

        intelligence_scheme = None
        if (
            scheme.scheme_code
            and target_date <= today
            and target_date >= today - timedelta(days=7)
        ):
            intelligence_scheme = (
                MFScheme.objects.filter(
                    amfi_code=scheme.scheme_code,
                    latest_nav__gt=0,
                    latest_nav_date__lte=target_date,
                    latest_nav_date__gte=today - timedelta(days=7),
                )
                .order_by("-latest_nav_date")
                .first()
            )

        # Pick the newest dated observation available. AMFI master wins ties;
        # MF Intelligence fills gaps and can supersede an older family copy.
        candidates = []
        if family_nav is not None:
            candidates.append((family_nav.date, 0, "family", family_nav))
        if master_scheme is not None:
            candidates.append((master_scheme.date, 2, "amfi", master_scheme))
        if intelligence_scheme is not None:
            candidates.append((
                intelligence_scheme.latest_nav_date,
                1,
                "intelligence",
                intelligence_scheme,
            ))

        if candidates:
            _as_of, _priority, source, selected = max(
                candidates,
                key=lambda item: (item[0], item[1]),
            )
            if source == "family":
                return selected
            if source == "amfi":
                nav, _ = MutualFundNAV.objects.update_or_create(
                    scheme=scheme,
                    date=selected.date,
                    source="AMFI",
                    defaults={"nav": selected.nav},
                )
                return nav
            return SimpleNamespace(
                nav=selected.latest_nav,
                date=selected.latest_nav_date,
                source="MF_INTELLIGENCE",
            )

        try:
            AMFIService.import_historical_master_navs(
                target_date,
                target_date,
            )
        except Exception as exc:
            raise ValueError(
                f"Unable to fetch historical NAV for "
                f"{scheme.scheme_name} on {target_date}: {exc}"
            )

        master_scheme = (
            AMFIMasterNAV.objects
            .filter(
                scheme__scheme_code=scheme.scheme_code,
                date__lte=target_date,
                source="AMFI",
                nav__gt=0,
            )
            .select_related("scheme")
            .order_by("-date", "-id")
            .first()
        )
        if master_scheme is None:
            raise ValueError(
                f"No NAV available for {scheme.scheme_name} "
                f"on or before {target_date}."
            )

        nav, _ = MutualFundNAV.objects.update_or_create(
            scheme=scheme,
            date=master_scheme.date,
            source="AMFI",
            defaults={"nav": master_scheme.nav},
        )
        return nav
