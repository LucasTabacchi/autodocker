from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from core.models import Workspace, WorkspaceInvitation, WorkspaceMembership


def ensure_personal_workspace(user):
    if not user or not user.is_authenticated:
        return None

    membership = (
        WorkspaceMembership.objects.select_related("workspace")
        .filter(user=user, role=WorkspaceMembership.Role.OWNER)
        .order_by("created_at")
        .first()
    )
    if membership:
        return membership.workspace

    workspace = Workspace.objects.create(
        owner=user,
        name=f"{user.get_username()} workspace",
        slug=_build_unique_slug(f"{user.get_username()}-workspace"),
        description="Workspace personal de AutoDocker.",
        visibility=Workspace.Visibility.PRIVATE,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role=WorkspaceMembership.Role.OWNER,
    )
    return workspace


def default_workspace_for_user(user):
    if not user or not user.is_authenticated:
        return None
    return (
        Workspace.objects.for_user(user)
        .order_by("created_at")
        .first()
        or ensure_personal_workspace(user)
    )


def invite_workspace_member(*, workspace: Workspace, identifier: str, role: str, invited_by):
    user_model = get_user_model()
    normalized = (identifier or "").strip()
    if not normalized:
        raise ValueError("Se requiere un username o email para invitar al workspace.")

    invited_user = None
    email = ""
    if "@" in normalized:
        email = normalized.lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise ValueError("El email ingresado no tiene un formato válido.") from exc
        invited_user = user_model.objects.filter(email__iexact=email).first()
        if invited_user and invited_user.email:
            email = invited_user.email
    else:
        invited_user = user_model.objects.filter(username=normalized).first()
        if not invited_user:
            raise ValueError("No existe un usuario con ese username.")
        email = invited_user.email or ""

    membership_filter = Q(workspace=workspace)
    if invited_user:
        membership_filter &= Q(user=invited_user)
    else:
        membership_filter &= Q(user__email__iexact=email)
    if WorkspaceMembership.objects.filter(membership_filter).exists():
        raise ValueError("Ese usuario ya forma parte del workspace.")

    pending_filter = Q(workspace=workspace, status=WorkspaceInvitation.Status.PENDING)
    if invited_user:
        pending_filter &= Q(invited_user=invited_user)
    else:
        pending_filter &= Q(email__iexact=email)
    invitation = WorkspaceInvitation.objects.filter(pending_filter).first()
    if invitation:
        invitation.role = role
        invitation.invited_by = invited_by
        invitation.email = email
        invitation.delivery_status = WorkspaceInvitation.DeliveryStatus.PENDING
        invitation.delivery_error = ""
        invitation.save(
            update_fields=[
                "role",
                "invited_by",
                "email",
                "delivery_status",
                "delivery_error",
                "updated_at",
            ]
        )
    else:
        invitation = WorkspaceInvitation.objects.create(
            workspace=workspace,
            invited_by=invited_by,
            invited_user=invited_user,
            email=email,
            role=role,
        )

    deliver_workspace_invitation(invitation)
    return invitation


def add_workspace_member(*, workspace: Workspace, username: str, role: str, invited_by):
    user_model = get_user_model()
    member = user_model.objects.get(username=username)
    membership, created = WorkspaceMembership.objects.update_or_create(
        workspace=workspace,
        user=member,
        defaults={"role": role},
    )
    if created and workspace.owner_id == member.id:
        membership.role = WorkspaceMembership.Role.OWNER
        membership.save(update_fields=["role", "updated_at"])
    return membership


def user_can_manage_workspace(user, workspace: Workspace) -> bool:
    if not user or not user.is_authenticated:
        return False
    return WorkspaceMembership.objects.filter(
        workspace=workspace,
        user=user,
        role__in=(WorkspaceMembership.Role.OWNER, WorkspaceMembership.Role.EDITOR),
    ).exists()


def incoming_workspace_invitations_for_user(user):
    return WorkspaceInvitation.objects.incoming_for_user(user)


def user_can_respond_to_invitation(user, invitation: WorkspaceInvitation) -> bool:
    if not user or not user.is_authenticated:
        return False
    if invitation.invited_user_id:
        return invitation.invited_user_id == user.id
    email = (getattr(user, "email", "") or "").strip().lower()
    return bool(email and invitation.email and invitation.email.lower() == email)


def accept_workspace_invitation(*, invitation: WorkspaceInvitation, user):
    if invitation.status != WorkspaceInvitation.Status.PENDING:
        raise ValueError("La invitación ya no está pendiente.")
    if not user_can_respond_to_invitation(user, invitation):
        raise ValueError("No tenés permisos para aceptar esta invitación.")

    membership, _created = WorkspaceMembership.objects.update_or_create(
        workspace=invitation.workspace,
        user=user,
        defaults={
            "role": (
                WorkspaceMembership.Role.OWNER
                if invitation.workspace.owner_id == user.id
                else invitation.role
            )
        },
    )
    invitation.invited_user = user
    if user.email:
        invitation.email = user.email
    invitation.status = WorkspaceInvitation.Status.ACCEPTED
    invitation.accepted_at = timezone.now()
    invitation.responded_at = invitation.accepted_at
    invitation.delivery_error = ""
    invitation.save(
        update_fields=[
            "invited_user",
            "email",
            "status",
            "accepted_at",
            "responded_at",
            "delivery_error",
            "updated_at",
        ]
    )
    return membership


def decline_workspace_invitation(*, invitation: WorkspaceInvitation, user):
    if invitation.status != WorkspaceInvitation.Status.PENDING:
        raise ValueError("La invitación ya no está pendiente.")
    if not user_can_respond_to_invitation(user, invitation):
        raise ValueError("No tenés permisos para rechazar esta invitación.")

    invitation.invited_user = invitation.invited_user or user
    if user.email:
        invitation.email = user.email
    invitation.status = WorkspaceInvitation.Status.DECLINED
    invitation.responded_at = timezone.now()
    invitation.save(
        update_fields=["invited_user", "email", "status", "responded_at", "updated_at"]
    )
    return invitation


def deliver_workspace_invitation(invitation: WorkspaceInvitation):
    if not invitation.email:
        invitation.delivery_status = WorkspaceInvitation.DeliveryStatus.IN_APP
        invitation.delivery_error = ""
        invitation.save(update_fields=["delivery_status", "delivery_error", "updated_at"])
        return invitation

    dashboard_url = f"{settings.AUTODOCKER_APP_BASE_URL.rstrip('/')}{reverse('core:dashboard')}"
    subject = f"Invitación a {invitation.workspace.name} en AutoDocker"
    invited_by = invitation.invited_by.get_username()
    message = (
        f"{invited_by} te invitó al workspace \"{invitation.workspace.name}\" en AutoDocker.\n\n"
        f"Rol propuesto: {invitation.get_role_display()}.\n"
        f"Ingresá a {dashboard_url} con tu cuenta para aceptar o rechazar la invitación."
    )
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [invitation.email],
            fail_silently=False,
        )
    except Exception as exc:
        invitation.delivery_status = WorkspaceInvitation.DeliveryStatus.FAILED
        invitation.delivery_error = str(exc)[:255]
    else:
        invitation.delivery_status = WorkspaceInvitation.DeliveryStatus.SENT
        invitation.delivery_error = ""
    invitation.save(update_fields=["delivery_status", "delivery_error", "updated_at"])
    return invitation


def _build_unique_slug(base: str) -> str:
    root = slugify(base) or "workspace"
    slug = root
    index = 2
    while Workspace.objects.filter(slug=slug).exists():
        slug = f"{root}-{index}"
        index += 1
    return slug
