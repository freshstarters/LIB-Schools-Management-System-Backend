"""Students, welfare, authorized pickup, and conduct notes."""

import uuid

from django.conf import settings
from django.db import models


class Student(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        HONOR_ROLL = "honor_roll", "Honor Roll"
        AT_RISK = "at_risk", "At Risk"
        WITHDRAWN = "withdrawn", "Withdrawn"
        GRADUATED = "graduated", "Graduated"

    class Gender(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student_code = models.CharField(max_length=16, unique=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="demo_student",
    )
    full_name = models.CharField(max_length=160)
    campus = models.ForeignKey(
        "demo.Campus",
        on_delete=models.PROTECT,
        related_name="students",
    )
    current_classroom = models.ForeignKey(
        "demo.Classroom",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )
    last_homeroom_teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="former_homeroom_students",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    is_waec_candidate = models.BooleanField(default=False)
    photo = models.ImageField(upload_to="students/photos/", blank=True)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=16, choices=Gender.choices)
    catchment_zone = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student_code"]

    def __str__(self):
        return f"{self.student_code} {self.full_name}"


class StudentWelfare(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.OneToOneField(
        Student,
        on_delete=models.CASCADE,
        related_name="welfare",
    )
    allergies = models.TextField(blank=True)
    medical_notes = models.TextField(blank=True)
    blood_group = models.CharField(max_length=8, blank=True)
    emergency_contact = models.CharField(max_length=20, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Welfare {self.student_id}"


class AuthorizedPickup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="pickups",
    )
    name = models.CharField(max_length=120)
    relationship = models.CharField(max_length=64)
    phone = models.CharField(max_length=20)
    id_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.student_id})"


class ConductNote(models.Model):
    class NoteType(models.TextChoices):
        MERIT = "merit", "Merit"
        INCIDENT = "incident", "Incident"
        HOMEROOM_NOTE = "homeroom_note", "Homeroom Note"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="conduct_notes",
    )
    note_type = models.CharField(max_length=16, choices=NoteType.choices)
    severity = models.CharField(max_length=8, choices=Severity.choices)
    description = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="conduct_notes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.note_type} {self.student_id}"


class StaffProfile(models.Model):
    class Role(models.TextChoices):
        PRINCIPAL = "principal", "Principal"
        REGISTRAR = "registrar", "Registrar"
        BURSAR = "bursar", "Bursar"
        TEACHER = "teacher", "Teacher"
        SUPPORT_STAFF = "support_staff", "Support Staff"

    class EmploymentStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="demo_staff",
    )
    staff_code = models.CharField(max_length=16, unique=True)
    full_name = models.CharField(max_length=160)
    role = models.CharField(max_length=32, choices=Role.choices)
    job_title = models.CharField(max_length=120, blank=True)
    division = models.ForeignKey(
        "demo.Division",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff",
    )
    campus = models.ForeignKey(
        "demo.Campus",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff",
    )
    qualifications = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField()
    employment_status = models.CharField(
        max_length=16,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["staff_code"]

    def __str__(self):
        return f"{self.staff_code} {self.full_name}"


class GuardianProfile(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="demo_guardian",
    )
    full_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=20)
    whatsapp_phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField()
    residential_zone = models.CharField(max_length=120, blank=True)
    occupation = models.CharField(max_length=120, blank=True)
    sms_consent = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "email"]

    def __str__(self):
        return self.full_name


class StudentGuardianLink(models.Model):
    class Relationship(models.TextChoices):
        MOTHER = "mother", "Mother"
        FATHER = "father", "Father"
        GUARDIAN = "guardian", "Guardian"
        UNCLE = "uncle", "Uncle"
        AUNT = "aunt", "Aunt"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="guardian_links",
    )
    guardian = models.ForeignKey(
        GuardianProfile,
        on_delete=models.CASCADE,
        related_name="child_links",
    )
    relationship = models.CharField(max_length=16, choices=Relationship.choices)
    relationship_note = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    is_financial_responsible = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "student__student_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "guardian"],
                name="one_link_per_student_guardian",
            ),
        ]

    def __str__(self):
        return f"{self.guardian_id} -> {self.student_id}"
