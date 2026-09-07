from mutual_funds.models import (
    MutualFundTransaction,
    SIP,
    SIPInstallment,
    SIPInstallmentStatus,
)


class SIPInstallmentReconciliationService:

    @staticmethod
    def reconcile_sip(sip):
        """
        Match existing SIP transactions to scheduled
        SIP installments.

        This does NOT create transactions.
        It only links existing transactions.
        """

        installments = (
            SIPInstallment.objects
            .filter(
                sip=sip,
            )
            .order_by(
                "scheduled_date",
            )
        )

        transactions = (
            MutualFundTransaction.objects
            .filter(
                owner=sip.owner,
                scheme=sip.scheme,
                transaction_type="SIP",
            )
            .order_by(
                "transaction_date",
                "id",
            )
        )

        transaction_list = list(
            transactions
        )

        matched_transactions = set()

        matched_count = 0

        for installment in installments:

            # Already reconciled.
            if installment.transaction_id:
                continue

            matching_transaction = None

            for tx in transaction_list:

                if tx.id in matched_transactions:
                    continue

                if (
                    tx.transaction_date
                    == installment.scheduled_date
                ):

                    if (
                        tx.amount
                        == installment.amount
                    ):

                        matching_transaction = tx
                        break

            if not matching_transaction:
                continue

            installment.transaction = (
                matching_transaction
            )

            installment.status = (
                SIPInstallmentStatus.EXECUTED
            )

            installment.executed_at = (
                matching_transaction.created_at
            )

            installment.notes = (
                "Matched with existing "
                "SIP transaction."
            )

            installment.save()

            matched_transactions.add(
                matching_transaction.id
            )

            matched_count += 1

        return matched_count