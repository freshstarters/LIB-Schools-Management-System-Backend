"""Gojo Institute profile, campuses, and the academic calendar."""

import uuid

from django.db import models


class Division(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class GradeLevel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=32, unique=True)
    division = models.ForeignKey(
        Division,
        on_delete=models.PROTECT,
        related_name="grade_levels",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class AcademicYear(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(max_length=32, unique=True)
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-label"]

    def __str__(self):
        return self.label


class MarkingPeriod(models.Model):
    class Code(models.TextChoices):
        P1 = "P1", "Period 1"
        P2 = "P2", "Period 2"
        P3 = "P3", "Period 3"
        P4 = "P4", "Period 4"
        P5 = "P5", "Period 5"
        P6 = "P6", "Period 6"
        SEM1_EXAM = "SEM1_EXAM", "3rd Period Exam"
        SEM2_EXAM = "SEM2_EXAM", "6th Period Exam"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="marking_periods",
    )
    code = models.CharField(max_length=16, choices=Code.choices)
    semester = models.PositiveSmallIntegerField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_locked = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["academic_year", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "code"],
                name="one_marking_period_code_per_year",
            )
        ]

    def __str__(self):
        return f"{self.academic_year_id} {self.code}"


class SchoolConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    singleton = models.BooleanField(default=True, editable=False, unique=True)
    school_name = models.CharField(max_length=200)
    moe_registration_code = models.CharField(max_length=64, blank=True)
    lrd_per_usd = models.DecimalField(max_digits=12, decimal_places=2)
    pass_threshold = models.DecimalField(max_digits=5, decimal_places=2)
    honor_roll_threshold = models.DecimalField(max_digits=5, decimal_places=2)
    marking_period_weight = models.DecimalField(max_digits=5, decimal_places=2)
    semester_exam_weight = models.DecimalField(max_digits=5, decimal_places=2)
    academic_year = models.ForeignKey(
        AcademicYear,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="school_configs",
    )
    active_marking_period = models.ForeignKey(
        MarkingPeriod,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="school_configs",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.school_name


class Campus(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    catchment_zone = models.CharField(max_length=200, blank=True)
    lead_staff = models.CharField(max_length=200, blank=True)
    grades_served = models.ManyToManyField(GradeLevel, related_name="campuses", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
