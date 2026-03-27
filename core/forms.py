from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


class AnalysisSubmissionForm(forms.Form):
    project_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Mi API, monorepo-platform, fastapi-service",
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
            ("production", "Producción"),
            ("development", "Desarrollo"),
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
            raise forms.ValidationError("Subí un archivo .zip o indicá una URL Git.")
        if archive and repository_url:
            raise forms.ValidationError("Elegí una sola fuente por análisis.")
        if archive and not archive.name.lower().endswith(".zip"):
            raise forms.ValidationError("Solo se admiten archivos .zip en este MVP.")
        return cleaned_data


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
        self.fields["first_name"].label = "Nombre"
        self.fields["first_name"].widget.attrs.update(
            {
                "placeholder": "Lucas",
                "autocomplete": "given-name",
                "class": "signup-input",
            }
        )
        self.fields["last_name"].label = "Apellido"
        self.fields["last_name"].widget.attrs.update(
            {
                "placeholder": "García",
                "autocomplete": "family-name",
                "class": "signup-input",
            }
        )
        self.fields["username"].label = "Usuario"
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
                "placeholder": "lucas@empresa.com",
                "autocomplete": "email",
                "class": "signup-input",
            }
        )
        self.fields["password1"].label = "Contraseña"
        self.fields["password1"].widget.attrs.update(
            {
                "placeholder": "Mínimo 8 caracteres",
                "autocomplete": "new-password",
                "class": "signup-input signup-input--password",
            }
        )
        self.fields["password2"].label = "Repetir contraseña"
        self.fields["password2"].widget.attrs.update(
            {
                "placeholder": "Repetí la contraseña",
                "autocomplete": "new-password",
                "class": "signup-input signup-input--password",
            }
        )
        self.fields["accept_terms"].label = "Acepto los Términos de uso y la Política de privacidad de AutoDocker."
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
