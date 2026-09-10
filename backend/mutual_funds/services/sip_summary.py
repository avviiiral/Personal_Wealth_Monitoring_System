from decimal import Decimal

from django.db.models import Sum

from mutual_funds.models import SIP, SIPInstallment


class SIPSummaryService:
    @staticmethod
    def _owner_ids(user):
        return [user.pk] if hasattr(user, "pk") else list(user)

    @staticmethod
    def _build_summary(sips, installments):
        executed = installments.filter(status="EXECUTED")
        due = installments.filter(status="DUE")
        skipped = installments.filter(status="SKIPPED")
        failed = installments.filter(status="FAILED")

        actual_invested = executed.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        pending_amount = due.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        next_installment = due.order_by("scheduled_date").first()
        active_sips = sips.filter(is_active=True)
        monthly_commitment = Decimal("0.00")

        for sip in active_sips:
            if sip.frequency == "MONTHLY":
                monthly_commitment += sip.amount
            elif sip.frequency == "WEEKLY":
                monthly_commitment += sip.amount * Decimal("52") / Decimal("12")
            elif sip.frequency == "QUARTERLY":
                monthly_commitment += sip.amount / Decimal("3")
            elif sip.frequency == "YEARLY":
                monthly_commitment += sip.amount / Decimal("12")

        return {
            "total_sips": sips.count(),
            "active_sips": active_sips.count(),
            "total_monthly_commitment": monthly_commitment,
            "installments": {
                "scheduled": installments.count(),
                "executed": executed.count(),
                "due": due.count(),
                "skipped": skipped.count(),
                "failed": failed.count(),
            },
            "actual_sip_invested": actual_invested,
            "pending_sip_amount": pending_amount,
            "next_installment": (
                {
                    "id": next_installment.id,
                    "date": next_installment.scheduled_date,
                    "amount": next_installment.amount,
                    "sip_id": next_installment.sip_id,
                }
                if next_installment
                else None
            ),
        }

    @staticmethod
    def get_summary_for_family(family_group_id):
        if family_group_id is None:
            raise ValueError("family_group_id is required for SIP summary operations.")
        sips = SIP.objects.filter(family_group_id=family_group_id)
        installments = SIPInstallment.objects.filter(sip__family_group_id=family_group_id)
        return SIPSummaryService._build_summary(sips, installments)

    @staticmethod
    def get_summary(user):
        """Legacy owner-based wrapper retained during migration."""
        owner_ids = SIPSummaryService._owner_ids(user)
        sips = SIP.objects.filter(owner_id__in=owner_ids)
        installments = SIPInstallment.objects.filter(sip__owner_id__in=owner_ids)
        return SIPSummaryService._build_summary(sips, installments)
