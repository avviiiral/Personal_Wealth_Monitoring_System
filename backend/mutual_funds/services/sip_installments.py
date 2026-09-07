from datetime import date

from mutual_funds.models import (
    SIP,
    SIPInstallment,
    SIPInstallmentStatus,
)

from .sip_engine import SIPEngine


class SIPInstallmentService:

    @staticmethod
    def generate_installments(sip):
        """
        Generate missing SIP installment records.

        This creates schedule records only.
        It does NOT create investment transactions.
        """

        today = date.today()

        current_date = (
            sip.start_date
        )

        created = []

        while current_date <= today:

            if (
                sip.end_date
                and current_date > sip.end_date
            ):
                break

            installment, was_created = (
                SIPInstallment.objects
                .get_or_create(
                    sip=sip,
                    scheduled_date=current_date,
                    defaults={
                        "amount": sip.amount,
                        "status": (
                            SIPInstallmentStatus.SCHEDULED
                        ),
                    },
                )
            )

            if was_created:
                created.append(
                    installment
                )

            current_date = (
                SIPEngine.calculate_next_date(
                    current_date,
                    sip.frequency,
                )
            )

            if len(created) > 10000:
                break

        return created

    @staticmethod
    def update_due_status(sip):
        """
        Change scheduled installments whose date
        has arrived to DUE.

        Already executed/skipped/failed installments
        are not changed.
        """

        today = date.today()

        installments = (
            SIPInstallment.objects
            .filter(
                sip=sip,
                scheduled_date__lte=today,
                status=SIPInstallmentStatus.SCHEDULED,
            )
        )

        updated = installments.update(
            status=SIPInstallmentStatus.DUE
        )

        return updated

    @staticmethod
    def synchronize_sip(sip):
        """
        Generate missing installments and update
        their due status.
        """

        created = (
            SIPInstallmentService
            .generate_installments(sip)
        )

        updated = (
            SIPInstallmentService
            .update_due_status(sip)
        )

        return {
            "created": len(created),
            "updated_to_due": updated,
        }