from django.db import transaction
from django.utils import timezone

from mutual_funds.models import (
    MutualFundTransaction,
    SIPInstallment,
    SIPInstallmentStatus,
)

from mutual_funds.services.holding_engine import (
    MutualFundHoldingEngine,
)

from mutual_funds.services.nav_service import (
    MutualFundNAVService,
)

from mutual_funds.services.sip_engine import (
    SIPEngine,
)


class SIPInstallmentExecutionService:

    @staticmethod
    @transaction.atomic
    def execute_installment(
        installment,
    ):
        """
        Execute exactly one SIP installment.

        The installment must already exist and be DUE.

        Creates one MF transaction, links it to the
        installment, marks the installment EXECUTED,
        and rebuilds the MF holding.
        """

        if (
            installment.status
            != SIPInstallmentStatus.DUE
        ):
            raise ValueError(
                "Only DUE SIP installments "
                "can be executed."
            )

        if installment.transaction_id:

            raise ValueError(
                "This SIP installment is already "
                "linked to a transaction."
            )

        sip = installment.sip

        if not sip.is_active:

            raise ValueError(
                "Cannot execute an installment "
                "for an inactive SIP."
            )

        # Resolve NAV using the centralized NAV service.
        #
        # IMPORTANT:
        # The NAV is selected using the SIP scheduled date,
        # not today's latest NAV.
        try:

            nav_record = (
                MutualFundNAVService
                .get_nav_for_date(
                    sip.scheme,
                    installment.scheduled_date,
                )
            )

        except ValueError as exc:

            raise ValueError(
                str(exc)
            )

        nav = nav_record.nav

        if nav <= 0:

            raise ValueError(
                "NAV must be greater than zero."
            )

        units = (
            installment.amount / nav
        )

        mf_transaction = (
            MutualFundTransaction.objects
            .create(
                owner=sip.owner,
                scheme=sip.scheme,
                transaction_type="SIP",
                transaction_date=(
                    installment.scheduled_date
                ),
                units=units,
                nav=nav,
                amount=installment.amount,
                fees=0,
                notes=(
                    f"SIP installment "
                    f"{installment.scheduled_date}"
                ),
            )
        )

        installment.transaction = (
            mf_transaction
        )

        installment.status = (
            SIPInstallmentStatus.EXECUTED
        )

        installment.executed_at = (
            timezone.now()
        )

        installment.notes = (
            "SIP installment executed "
            f"using NAV {nav} from "
            f"{nav_record.date}."
        )

        installment.save()

        # Advance SIP's next installment date
        # only when this installment represents
        # the current next installment.
        if (
            sip.next_installment_date
            == installment.scheduled_date
        ):

            next_date = (
                SIPEngine.calculate_next_date(
                    installment.scheduled_date,
                    sip.frequency,
                )
            )

            if (
                sip.end_date
                and next_date > sip.end_date
            ):

                sip.is_active = False
                sip.next_installment_date = None

            else:

                sip.next_installment_date = (
                    next_date
                )

            sip.save()

        # Rebuild portfolio holding immediately.
        holding = (
            MutualFundHoldingEngine
            .rebuild_holding(
                sip.scheme
            )
        )

        return (
            mf_transaction,
            installment,
            holding,
        )