from mutual_funds.models import MutualFundNAV

from mutual_funds.services.amfi import (
    AMFIService,
)


class MutualFundNAVService:

    @staticmethod
    def get_nav_for_date(
        scheme,
        target_date,
    ):
        """
        Return the latest available NAV on or before
        the requested date.

        If the NAV does not already exist, automatically
        download the historical AMFI NAV for the requested
        date and then try again.

        Returns:
            MutualFundNAV instance

        Raises:
            ValueError if no NAV is available.
        """

        nav = (
            MutualFundNAV.objects
            .filter(
                scheme=scheme,
                date__lte=target_date,
            )
            .order_by("-date")
            .first()
        )

        if nav:

            if nav.nav <= 0:

                raise ValueError(
                    f"Invalid NAV {nav.nav} "
                    f"for {scheme.scheme_name} "
                    f"on {nav.date}."
                )

            return nav

        # --------------------------------------------------
        # NAV is missing.
        #
        # Automatically download historical AMFI NAV
        # for the SIP's required date.
        # --------------------------------------------------

        try:

            AMFIService.import_historical_navs(
                scheme.owner,
                target_date,
                target_date,
            )

        except Exception as exc:

            raise ValueError(
                f"Unable to fetch historical NAV "
                f"for {scheme.scheme_name} "
                f"on {target_date}: {exc}"
            )

        # --------------------------------------------------
        # Try again after AMFI import.
        # --------------------------------------------------

        nav = (
            MutualFundNAV.objects
            .filter(
                scheme=scheme,
                date__lte=target_date,
            )
            .order_by("-date")
            .first()
        )

        if not nav:

            raise ValueError(
                f"No NAV available for "
                f"{scheme.scheme_name} "
                f"on or before {target_date}."
            )

        if nav.nav <= 0:

            raise ValueError(
                f"Invalid NAV {nav.nav} "
                f"for {scheme.scheme_name} "
                f"on {nav.date}."
            )

        return nav