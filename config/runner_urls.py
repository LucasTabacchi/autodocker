from django.urls import include, path

from core.views import HealthcheckView

urlpatterns = [
    path("health/", HealthcheckView.as_view(), name="health"),
    path("", include("core.runner_api.urls")),
]
