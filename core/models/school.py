"""Thin school catalog used by tenant provisioning. Later stories extend these models."""

from django.core.exceptions import ValidationError
from django.db import models

from core.models.platform import TenantScopedModel


class Division(TenantScopedModel):
    code = models.SlugField(max_length=64)
    name = models.CharField(max_length=128)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"],
                name="unique_division_code_per_school",
            )
        ]

    def __str__(self):
        return self.code


class Subject(TenantScopedModel):
    code = models.SlugField(max_length=64)
    name = models.CharField(max_length=128)
    division = models.ForeignKey(
        Division,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="subjects",
    )

    class Meta(TenantScopedModel.Meta):
        abstract = False
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"],
                name="unique_subject_code_per_school",
            )
        ]

    def __str__(self):
        return self.code

    def clean(self):
        super().clean()
        if not self.division_id:
            return
        division = Division.objects.filter(pk=self.division_id).first()
        if division is None or division.school_id != self.school_id:
            raise ValidationError(
                {"division": "Division belongs to another school."}
            )
