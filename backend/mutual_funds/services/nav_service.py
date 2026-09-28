from mutual_funds.models import AMFIMasterNAV, MutualFundNAV
from mutual_funds.services.amfi import AMFIService


class MutualFundNAVService:

    @staticmethod
    def get_nav_for_date(scheme, target_date):
        """
        Return the latest NAV on or before target_date.

        The global AMFI master is authoritative. Family-scoped MutualFundNAV
        remains as a compatibility/materialization layer for existing
        portfolio relationships.
        """
        nav = (
            MutualFundNAV.objects
            .filter(scheme=scheme, date__lte=target_date)
            .order_by("-date")
            .first()
        )

        if nav and nav.nav > 0:
            return nav

        master_scheme = (
            AMFIMasterNAV.objects
            .filter(
                scheme__scheme_code=scheme.scheme_code,
                date__lte=target_date,
                source="AMFI",
            )
            .select_related("scheme")
            .order_by("-date")
            .first()
        )

        if master_scheme is None:
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
                )
                .select_related("scheme")
                .order_by("-date")
                .first()
            )

        if master_scheme is None:
            raise ValueError(
                f"No NAV available for {scheme.scheme_name} "
                f"on or before {target_date}."
            )

        if master_scheme.nav <= 0:
            raise ValueError(
                f"Invalid NAV {master_scheme.nav} "
                f"for {scheme.scheme_name} on {master_scheme.date}."
            )

        nav, _ = MutualFundNAV.objects.update_or_create(
            scheme=scheme,
            date=master_scheme.date,
            source="AMFI",
            defaults={"nav": master_scheme.nav},
        )
        return nav
