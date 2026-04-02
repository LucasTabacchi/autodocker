from __future__ import annotations

from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, TemplateView

from core.api.serializers import (
    DashboardConnectionSerializer,
    DashboardHistoryAnalysisSerializer,
    DashboardWorkspaceInvitationSerializer,
    DashboardWorkspaceSerializer,
)
from core.forms import AnalysisSubmissionForm, SignUpForm
from core.models import ExternalRepoConnection, ProjectAnalysis, Workspace
from core.services.runtime import preview_runtime_capability
from core.services.workspaces import (
    default_workspace_for_user,
    ensure_personal_workspace,
    incoming_workspace_invitations_for_user,
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_workspace = default_workspace_for_user(self.request.user)
        workspaces = Workspace.objects.for_user(self.request.user).prefetch_related(
            "memberships__user",
            "invitations__invited_user",
            "invitations__invited_by",
        )
        context["submission_form"] = AnalysisSubmissionForm()
        context["workspaces"] = workspaces
        context["active_workspace"] = active_workspace
        context["preview_runtime_capability"] = preview_runtime_capability()
        analyses = (
            ProjectAnalysis.objects.for_user(self.request.user)
            .select_related("workspace")
            .only(
                "id",
                "workspace_id",
                "workspace__name",
                "workspace__slug",
                "project_name",
                "status",
                "detected_framework",
                "created_at",
            )
        )
        if active_workspace:
            analyses = analyses.filter(workspace=active_workspace)
        recent_analyses = analyses[:8]
        context["recent_analyses"] = recent_analyses
        context["dashboard_bootstrap"] = {
            "workspaces": DashboardWorkspaceSerializer(
                workspaces,
                many=True,
            ).data,
            "incoming_invitations": DashboardWorkspaceInvitationSerializer(
                incoming_workspace_invitations_for_user(self.request.user),
                many=True,
            ).data,
            "recent_analyses": DashboardHistoryAnalysisSerializer(
                recent_analyses,
                many=True,
            ).data,
            "connections": DashboardConnectionSerializer(
                ExternalRepoConnection.objects.for_user(self.request.user),
                many=True,
            ).data,
        }
        return context


class SignInView(View):
    template_name = "registration/login.html"
    form_class = AuthenticationForm
    success_url = reverse_lazy("core:dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(
            request,
            self.template_name,
            {
                "form": self.form_class(request=request),
                "next_url": request.GET.get("next", ""),
            },
        )

    def post(self, request):
        form = self.form_class(request=request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            redirect_url = self._redirect_url(request)
            if self._wants_json(request):
                return JsonResponse({"ok": True, "redirect_url": redirect_url})
            return HttpResponseRedirect(redirect_url)

        if self._wants_json(request):
            return JsonResponse(
                {"ok": False, "detail": self._form_error_message(form)},
                status=400,
            )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "next_url": request.POST.get("next", ""),
            },
            status=400,
        )

    def _wants_json(self, request: HttpRequest) -> bool:
        accept = request.headers.get("Accept", "")
        return "application/json" in accept or request.headers.get("X-Requested-With") == "fetch"

    def _redirect_url(self, request: HttpRequest) -> str:
        return request.POST.get("next") or str(self.success_url)

    def _form_error_message(self, form: AuthenticationForm) -> str:
        non_field = form.non_field_errors()
        if non_field:
            return non_field[0]
        for field in form.errors.values():
            if field:
                return field[0]
        return "Usuario o contraseña inválidos."


class SignUpView(FormView):
    template_name = "registration/signup.html"
    form_class = SignUpForm
    success_url = reverse_lazy("core:dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        ensure_personal_workspace(user)
        login(self.request, user)
        return HttpResponseRedirect(self.get_success_url())


class HealthcheckView(View):
    def get(self, request):
        return JsonResponse({"ok": True, "service": "autodocker"})
