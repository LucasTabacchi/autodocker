from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import (
    HealthcheckView,
    PasswordResetCompleteCustomView,
    PasswordResetConfirmCustomView,
    PasswordResetDoneCustomView,
    PasswordResetRequestView,
    SignInView,
    SignUpView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", SignInView.as_view(), name="login"),
    path("accounts/signup/", SignUpView.as_view(), name="signup"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password-reset/", PasswordResetRequestView.as_view(), name="password_reset"),
    path("accounts/password-reset/sent/", PasswordResetDoneCustomView.as_view(), name="password_reset_done"),
    path(
        "accounts/password-reset/confirm/<uidb64>/<token>/",
        PasswordResetConfirmCustomView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "accounts/password-reset/complete/",
        PasswordResetCompleteCustomView.as_view(),
        name="password_reset_complete",
    ),
    path("health/", HealthcheckView.as_view(), name="health"),
    path("", include("core.urls")),
    path("api/", include("core.api.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
