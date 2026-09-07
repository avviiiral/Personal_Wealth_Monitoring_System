from decimal import Decimal

from dateutil.relativedelta import relativedelta

from django.db import transaction

from mutual_funds.models import (
    MutualFundTransaction,
    MutualFundTransactionType,
    SIP,
    SIPFrequency,
)

from mutual_funds.services.nav_service import (
    MutualFundNAVService,
)


class SIPEngine:

    ZERO = Decimal("0")

    @staticmethod
    def calculate_next_date(
        current_date,
        frequency,
    ):
        """
        Calculate the next scheduled SIP date.
        """

        if frequency == SIPFrequency.WEEKLY:

            return current_date + relativedelta(
                weeks=1
            )

        if frequency == SIPFrequency.MONTHLY:

            return current_date + relativedelta(
                months=1
            )

        if frequency == SIPFrequency.QUARTERLY:

            return current_date + relativedelta(
                months=3
            )

        if frequency == SIPFrequency.YEARLY:

            return current_date + relativedelta(
                years=1
            )

        raise ValueError(
            f"Unsupported SIP frequency: {frequency}"
        )

    @staticmethod
    def get_due_count(sip):
        """
        Calculate how many scheduled installments have
        reached their due date.

        This does NOT create transactions.
        """

        from datetime import date

        today = date.today()

        if not sip.is_active:
            return 0

        if today < sip.start_date:
            return 0

        current_date = (
            sip.next_installment_date
            or sip.start_date
        )

        count = 0

        while current_date <= today:

            if (
                sip.end_date
                and current_date > sip.end_date
            ):
                break

            count += 1

            current_date = (
                SIPEngine.calculate_next_date(
                    current_date,
                    sip.frequency,
                )
            )

            # Safety protection against bad data.
            if count > 10000:
                break

        return count

    @staticmethod
    def is_due(sip):
        """
        Return True when at least one installment is due.
        """

        return (
            SIPEngine.get_due_count(sip)
            > 0
        )

    @staticmethod
    def get_due_sips(user):
        """
        Return active SIPs with at least one
        due installment.
        """

        sips = (
            SIP.objects
            .filter(
                owner=user,
                is_active=True,
            )
            .select_related("scheme")
        )

        return [
            sip
            for sip in sips
            if SIPEngine.is_due(sip)
        ]

    @staticmethod
    def get_sip_status(sip):
        """
        Return useful status information for the dashboard.
        """

        from datetime import date

        today = date.today()

        due_count = (
            SIPEngine.get_due_count(sip)
        )

        if not sip.is_active:

            status = "INACTIVE"

        elif today < sip.start_date:

            status = "UPCOMING"

        elif (
            sip.end_date
            and today > sip.end_date
        ):

            status = "COMPLETED"

        elif due_count > 0:

            status = "DUE"

        else:

            status = "ACTIVE"

        return {
            "status": status,
            "due_count": due_count,
            "next_installment_date": (
                sip.next_installment_date
            ),
        }

    @staticmethod
    @transaction.atomic
    def execute_sip(sip):
        """
        Execute ONE SIP installment.

        The execution is atomic:

        SIP installment
            ↓
        Historical NAV
            ↓
        Transaction
            ↓
        Installment reconciliation
            ↓
        SIP schedule advancement
        """

        from django.utils import timezone

        from mutual_funds.models import SIPInstallment

        if not sip.is_active:

            raise ValueError(
                "Cannot execute an inactive SIP."
            )

        if not SIPEngine.is_due(sip):

            raise ValueError(
                "SIP installment is not due."
            )

        scheduled_date = (
            sip.next_installment_date
            or sip.start_date
        )

        # Lock the exact installment so two execution
        # requests cannot execute it simultaneously.
        installment = (
            SIPInstallment.objects
            .select_for_update()
            .filter(
                sip=sip,
                scheduled_date=scheduled_date,
            )
            .first()
        )

        if not installment:

            raise ValueError(
                f"No SIP installment exists for "
                f"{scheduled_date}."
            )

        # Prevent duplicate execution.
        if installment.status == "EXECUTED":

            raise ValueError(
                f"SIP installment "
                f"{scheduled_date} is already executed."
            )

        if installment.status not in (
            "DUE",
            "SCHEDULED",
        ):

            raise ValueError(
                f"SIP installment "
                f"{scheduled_date} has status "
                f"{installment.status} and cannot "
                f"be executed."
            )

        # Resolve NAV using the centralized NAV service.
        try:

            nav_record = (
                MutualFundNAVService
                .get_nav_for_date(
                    sip.scheme,
                    scheduled_date,
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

        # The installment amount is the authoritative
        # amount for this specific scheduled installment.
        amount = installment.amount

        if amount != sip.amount:

            raise ValueError(
                f"SIP amount mismatch for "
                f"{scheduled_date}: "
                f"SIP={sip.amount}, "
                f"Installment={amount}"
            )

        units = (
            amount / nav
        )

        # Create the mutual fund transaction.
        transaction_record = (
            MutualFundTransaction.objects
            .create(
                owner=sip.owner,
                scheme=sip.scheme,
                transaction_type=(
                    MutualFundTransactionType.SIP
                ),
                transaction_date=scheduled_date,
                units=units,
                nav=nav,
                amount=amount,
                fees=SIPEngine.ZERO,
                notes=(
                    f"SIP installment "
                    f"{scheduled_date}"
                ),
            )
        )

        # Immediately reconcile the installment.
        installment.status = "EXECUTED"

        installment.transaction = (
            transaction_record
        )

        installment.executed_at = (
            timezone.now()
        )

        installment.notes = (
            f"Executed using NAV "
            f"{nav} from {nav_record.date}."
        )

        installment.save(
            update_fields=[
                "status",
                "transaction",
                "executed_at",
                "notes",
                "updated_at",
            ]
        )

        # Advance the SIP schedule.
        next_date = (
            SIPEngine.calculate_next_date(
                scheduled_date,
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

        sip.save(
            update_fields=[
                "is_active",
                "next_installment_date",
                "updated_at",
            ]
        )

        return transaction_record

    @staticmethod
    def execute_one_due_sip(sip):
        """
        Explicitly execute exactly one due installment.
        """

        return SIPEngine.execute_sip(
            sip
        )