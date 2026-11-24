"""Platform endpoints. Reserved hosts and header mode both land here."""

from django.http import JsonResponse


def platform_ping(request):
    return JsonResponse({"status": "ok"})
