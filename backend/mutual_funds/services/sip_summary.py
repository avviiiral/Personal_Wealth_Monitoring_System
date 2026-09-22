from decimal import Decimal

from django.db.models import Count, Q, Sum
from users.permissions import require_active_family

from mutual_funds.models import SIP, SIPInstallment


class SIPSummaryService:

    @staticmethod
    def _owner_ids(user):
        """Normalize to a list of owner ids (single User or an
        iterable of ids - see users.permissions.get_visible_owner_ids)."""

        return [user.pk] if hasattr(user, "pk") else list(user)

    @staticmethod
    def get_summary(user, family_id=None):

        family_id = family_id or require_active_family(user).id

        sip_totals = SIP.objects.filter(
            family_id=family_id
        ).aggregate(
            total_sips=Count("id"),
            active_sips=Count(
                "id",
                filter=Q(is_active=True),
            ),
        )

        active_sip_amounts = SIP.objects.filter(
            family_id=family_id,
            is_active=True,
        ).values_list("frequency", "amount")

        installment_totals = SIPInstallment.objects.filter(
            sip__family_id=family_id
        ).aggregate(
            scheduled=Count("id"),
            executed=Count(
                "id",
                filter=Q(status="EXECUTED"),
            ),
            due=Count(
                "id",
                filter=Q(status="DUE"),
            ),
            skipped=Count(
                "id",
                filter=Q(status="SKIPPED"),
            ),
            failed=Count(
                "id",
                filter=Q(status="FAILED"),
            ),
            actual_invested=Sum(
                "amount",
                filter=Q(status="EXECUTED"),
            ),
            pending_amount=Sum(
                "amount",
                filter=Q(status="DUE"),
            ),
        )

        next_installment = (
            SIPInstallment.objects
            .filter(
                sip__family_id=family_id,
                status="DUE",
            )
            .order_by("scheduled_date")
            .first()
        )

        monthly_commitment = Decimal("0.00")

        for frequency, amount in active_sip_amounts:

            if sip.frequency == "MONTHLY":

                monthly_commitment += (
                    sip.amount
                )

            elif sip.frequency == "WEEKLY":

                monthly_commitment += (
                    sip.amount * Decimal("52")
                    / Decimal("12")
                )

            elif sip.frequency == "QUARTERLY":

                monthly_commitment += (
                    sip.amount
                    / Decimal("3")
                )

            elif sip.frequency == "YEARLY":

                monthly_commitment += (
                    sip.amount
                    / Decimal("12")
                )

        return {
            "total_sips": sip_totals["total_sips"],

            "active_sips": sip_totals["active_sips"],

            "total_monthly_commitment": (
                monthly_commitment
            ),

            "installments": {
                "scheduled": installment_totals["scheduled"],

                "executed": installment_totals["executed"],

                "due": installment_totals["due"],

                "skipped": installment_totals["skipped"],

                "failed": installment_totals["failed"],
            },

            "actual_sip_invested": (
                installment_totals["actual_invested"]
                or Decimal("0.00")
            ),

            "pending_sip_amount": (
                installment_totals["pending_amount"]
                or Decimal("0.00")
            ),

            "next_installment": (
                {
                    "id": next_installment.id,
                    "date": (
                        next_installment
                        .scheduled_date
                    ),
                    "amount": (
                        next_installment.amount
                    ),
                    "sip_id": (
                        next_installment.sip_id
                    ),
                }
                if next_installment
                else None
            ),
        }