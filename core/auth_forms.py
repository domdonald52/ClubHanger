from django import forms
from django.contrib.auth.forms import AuthenticationForm


class EmailAuthenticationForm(AuthenticationForm):
    """Login form that presents the username field as 'Email' (members log in
    with their email address, which is stored as the username) and uses
    member-friendly error wording."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": (
            "The email or password you entered is incorrect. "
            "Please try again, or reset your password below."
        ),
    }

    username = forms.CharField(
        label="Email",
        widget=forms.EmailInput(attrs={
            "autofocus": True,
            "autocomplete": "username",
            "placeholder": "you@example.com",
        }),
    )
