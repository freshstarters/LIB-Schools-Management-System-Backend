"""Classrooms, MoE subjects, weekly timetable slots, and substitutions."""

import uuid

from django.conf import settings
from django.db import models


class Subject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    division = models.ForeignKey(
        "demo.Division",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="subjects",
    )
    is_core = models.BooleanField(default=False)
    is_waec_lab = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Classroom(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campus = models.ForeignKey(
        "demo.Campus",
        on_delete=models.PROTECT,
        related_name="classrooms",
    )
    academic_year = models.ForeignKey(
        "demo.AcademicYear",
        on_delete=models.PROTECT,
        related_name="classrooms",
    )
    grade_level = models.ForeignKey(
        "demo.GradeLevel",
        on_delete=models.PROTECT,
        related_name="classrooms",
    )
    section_name = models.CharField(max_length=32)
    homeroom_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homeroom_classrooms",
    )
    capacity = models.PositiveSmallIntegerField(default=35)
    room_label = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["grade_level__sort_order", "section_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["campus", "academic_year", "grade_level", "section_name"],
                name="one_classroom_section_per_campus_year_grade",
            )
        ]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        grade = self.grade_level.name
        section = self.section_name.strip()
        if grade.startswith("Grade ") and section and len(section) <= 8:
            return f"{grade}{section}"
        if section:
            return f"{grade} {section}"
        return grade


class ClassSubjectAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.CASCADE,
        related_name="subject_assignments",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="classroom_assignments",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subject_assignments",
    )

    class Meta:
        ordering = ["subject__sort_order", "subject__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["classroom", "subject"],
                name="one_subject_per_classroom",
            )
        ]

    def __str__(self):
        return f"{self.classroom_id} {self.subject_id}"


class TimetableSlot(models.Model):
    class Day(models.TextChoices):
        MONDAY = "monday", "Monday"
        TUESDAY = "tuesday", "Tuesday"
        WEDNESDAY = "wednesday", "Wednesday"
        THURSDAY = "thursday", "Thursday"
        FRIDAY = "friday", "Friday"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.CASCADE,
        related_name="timetable_slots",
    )
    day_of_week = models.CharField(max_length=16, choices=Day.choices)
    period_number = models.PositiveSmallIntegerField()
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="timetable_slots",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timetable_slots",
    )
    room_label = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day_of_week", "period_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["classroom", "day_of_week", "period_number"],
                name="one_slot_per_classroom_day_period",
            ),
            models.CheckConstraint(
                condition=models.Q(period_number__gte=1, period_number__lte=8),
                name="timetable_period_between_1_and_8",
            ),
        ]

    def __str__(self):
        return f"{self.classroom_id} {self.day_of_week} P{self.period_number}"


class TeacherAbsence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teacher_absences",
    )
    date = models.DateField()
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "date"],
                name="one_absence_per_teacher_date",
            )
        ]

    def __str__(self):
        return f"{self.teacher_id} {self.date}"


class TeacherSubstitution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slot = models.ForeignKey(
        TimetableSlot,
        on_delete=models.CASCADE,
        related_name="substitutions",
    )
    date = models.DateField()
    substitute_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="substitutions",
    )
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["slot", "date"],
                name="one_substitution_per_slot_date",
            )
        ]

    def __str__(self):
        return f"{self.slot_id} {self.date}"
