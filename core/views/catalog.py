"""Tenant-scoped division and subject reads and updates."""

from rest_framework.generics import ListAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.serializers import ModelSerializer

from core.helpers.idempotency import enforce_unmatched_since, run_idempotent
from core.helpers.permissions import CATALOG_READ, CATALOG_WRITE, HasSchoolRole
from core.models import Division, Subject
from core.service.auth import user_from_bearer


class DivisionSerializer(ModelSerializer):
    class Meta:
        model = Division
        fields = ["id", "code", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class SubjectSerializer(ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "code", "name", "division", "created_at", "updated_at"]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class _CatalogView:
    authentication_classes = []
    permission_classes = [HasSchoolRole]
    read_roles = CATALOG_READ
    write_roles = CATALOG_WRITE
    app_scope = "core"

    def idempotency_user(self, request):
        return user_from_bearer(request, "core")

    def update(self, request, *args, **kwargs):
        def produce():
            instance = self.get_object()
            enforce_unmatched_since(
                request,
                instance,
                self.get_serializer(instance).data,
            )
            return RetrieveUpdateDestroyAPIView.update(self, request, *args, **kwargs)

        return run_idempotent(self, request, produce)

    def destroy(self, request, *args, **kwargs):
        return run_idempotent(
            self,
            request,
            lambda: RetrieveUpdateDestroyAPIView.destroy(self, request, *args, **kwargs),
        )


class DivisionListView(_CatalogView, ListAPIView):
    serializer_class = DivisionSerializer

    def get_queryset(self):
        return Division.objects.all()


class DivisionDetailView(_CatalogView, RetrieveUpdateDestroyAPIView):
    serializer_class = DivisionSerializer

    def get_queryset(self):
        return Division.objects.all()


class SubjectListView(_CatalogView, ListAPIView):
    serializer_class = SubjectSerializer

    def get_queryset(self):
        return Subject.objects.all()


class SubjectDetailView(_CatalogView, RetrieveUpdateDestroyAPIView):
    serializer_class = SubjectSerializer

    def get_queryset(self):
        return Subject.objects.all()
