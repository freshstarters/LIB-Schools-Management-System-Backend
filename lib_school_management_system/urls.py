from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from core.views.health import health, ready

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/ready/", ready, name="ready"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/", include("core.urls")),
    path("api/demo/v1/", include("demo.urls")),
]
