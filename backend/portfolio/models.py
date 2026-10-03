from django.conf import settings
from django.db import models


class FamilyMISNotes(models.Model):
    """Family-scoped editable structure and values for the MIS Notes sheet."""

    family = models.OneToOneField(
        "users.FamilyGroup",
        on_delete=models.CASCADE,
        related_name="mis_notes",
    )
    document = models.JSONField(default=dict)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_family_mis_notes",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["family_id"]

    def __str__(self):
        return f"MIS Notes - {self.family.name}"


class FamilyMISNotesChangeLog(models.Model):
    """Audit trail for family-wide MIS Notes edits."""

    family = models.ForeignKey(
        "users.FamilyGroup",
        on_delete=models.CASCADE,
        related_name="mis_notes_change_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mis_notes_changes",
    )
    changes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"MIS Notes change - {self.family.name} - {self.created_at:%Y-%m-%d %H:%M}"
