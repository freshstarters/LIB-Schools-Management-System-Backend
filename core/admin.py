from django.contrib import admin

from core.models import TenantSchool, User


@admin.register(TenantSchool)
class TenantSchoolAdmin(admin.ModelAdmin):
    exclude = ("db_password_encrypted",)
    list_display = ("slug", "name", "status", "db_name")
    search_fields = ("slug", "name")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    exclude = ("password", "username")
    list_display = ("email", "phone", "app_scope", "school", "is_active")
    search_fields = ("email", "phone")
    list_filter = ("app_scope", "is_active")
