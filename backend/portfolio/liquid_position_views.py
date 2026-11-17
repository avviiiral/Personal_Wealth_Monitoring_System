from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.permissions import (
    CanWriteFinancialData,
    is_admin_or_above,
    is_system_owner,
    require_active_family,
)

from .liquid_position_service import LiquidPositionService
from .models import FamilyLiquidPosition


def _active_family_for_request(user):
    family = require_active_family(user)
    if not is_system_owner(user) and not user.profile.family_groups.filter(pk=family.pk).exists():
        raise PermissionDenied("The selected family is not available to this user.")
    return family


def _reporting_date(request):
    value = request.query_params.get("as_of")
    if not value:
        return timezone.localdate()
    try:
        reporting_date = date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"as_of": "Date must use YYYY-MM-DD format."})
    if reporting_date > timezone.localdate():
        raise ValidationError({"as_of": "Reporting date cannot be in the future."})
    return reporting_date


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def liquid_position(request):
    """Read or save the active family's Liquid Position for a reporting date."""
    family = _active_family_for_request(request.user)
    as_of_date = _reporting_date(request)

    position = FamilyLiquidPosition.objects.filter(
        family=family,
        reporting_date=as_of_date,
    ).first()

    if request.method == "PUT":
        document = LiquidPositionService.normalize_document(request.data.get("document"))
        with transaction.atomic():
            position, _created = FamilyLiquidPosition.objects.update_or_create(
                family=family,
                reporting_date=as_of_date,
                defaults={
                    "document": document,
                    "updated_by": request.user,
                },
            )
    else:
        document = position.document if position else LiquidPositionService.DEFAULT_DOCUMENT

    snapshot = LiquidPositionService.build(family, as_of_date, document)

    return Response({
        "as_of_date": as_of_date.isoformat(),
        "unit_label": "Rs in lakhs",
        "can_edit": is_admin_or_above(request.user),
        "updated_at": position.updated_at.isoformat() if position else None,
        **snapshot,
    })
