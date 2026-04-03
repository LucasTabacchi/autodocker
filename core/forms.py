from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm, UserCreationForm


class AnalysisSubmissionForm(forms.Form):
    project_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "payments-api, monorepo-platform, fastapi-service",
            }
        ),
    )
    archive = forms.FileField(required=False)
    repository_url = forms.URLField(
        required=False,
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://github.com/acme/platform",
            }
        ),
    )
    generation_profile = forms.ChoiceField(
        choices=(
            ("production", "Production"),
            ("development", "Development"),
            ("ci", "CI"),
        ),
        initial="production",
        required=False,
        widget=forms.Select(),
    )

    def clean(self):
        cleaned_data = super().clean()
        archive = cleaned_data.get("archive")
        repository_url = cleaned_data.get("repository_url")

        if not archive and not repository_url:
            raise forms.ValidationError("Upload a .zip archive or provide a Git repository URL.")
        if archive and repository_url:
            raise forms.ValidationError("Choose a single source for each analysis.")
        if archive and not archive.name.lower().endswith(".zip"):
            raise forms.ValidationError("Only .zip archives are supported for this flow.")
        return cleaned_data


class PasswordResetRequestForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].label = "Email"
        self.fields["email"].widget.attrs.update(
            {
                "placeholder": "lucas@company.com",
                "autocomplete": "email",
                "class": "signup-input",
            }
        )


class PasswordResetConfirmCustomForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "New password"
        self.fields["new_password1"].widget.attrs.update(
            {
                "placeholder": "Minimum 8 characters",
                "autocomplete": "new-password",
                "class": "signup-input signup-input--password",
            }
        )
        self.fields["new_password2"].label = "Confirm new password"
        self.fields["new_password2"].widget.attrs.update(
            {
                "placeholder": "Repeat your new password",
                "autocomplete": "new-password",
                "class": "signup-input signup-input--password",
            }
        )


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(required=False, max_length=150)
    last_name = forms.CharField(required=False, max_length=150)
    email = forms.EmailField(required=True)
    accept_terms = forms.BooleanField(required=True)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("first_name", "last_name", "username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].label = "First name"
        self.fields["first_name"].widget.attrs.update(
            {
                "placeholder": "Lucas",
                "autocomplete": "given-name",
                "class": "signup-input",
            }
        )
        self.fields["last_name"].label = "Last name"
        self.fields["last_name"].widget.attrs.update(
            {
                "placeholder": "Garcia",
                "autocomplete": "family-name",
                "class": "signup-input",
            }
        )
        self.fields["username"].label = "Username"
        self.fields["username"].widget.attrs.update(
            {
                "placeholder": "lucas-garcia",
                "autocomplete": "username",
                "class": "signup-input",
            }
        )
        self.fields["email"].label = "Email"
        self.fields["email"].widget.attrs.update(
            {
                "placeholder": "lucas@company.com",
                "autocomplete": "email",
                "class": "signup-input",
            }
        )
        self.fields["password1"].label = "Password"
        self.fields["password1"].widget.attrs.update(
            {
                "placeholder": "Minimum 8 characters",
                "autocomplete": "new-password",
                "class": "signup-input signup-input--password",
            }
        )
        self.fields["password2"].label = "Confirm password"
        self.fields["password2"].widget.attrs.update(
            {
                "placeholder": "Repeat your password",
                "autocomplete": "new-password",
                "class": "signup-input signup-input--password",
            }
        )
        self.fields["accept_terms"].label = "I accept the Terms of Use and Privacy Policy of AutoDocker."
        self.fields["accept_terms"].widget.attrs.update(
            {
                "class": "signup-checkbox",
            }
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user
