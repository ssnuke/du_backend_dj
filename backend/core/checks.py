"""
Django system checks — surfaced by `python manage.py check` (and
`check --deploy`), which most deploy pipelines run before/after a release.
Firebase push notifications were previously failing completely-silently in
any environment missing serviceAccountKey.json / FIREBASE_SERVICE_ACCOUNT_PATH:
initialize_firebase() just logged a warning and every subsequent send quietly
reported 0 successes forever, easy to miss among normal startup log noise.
"""
from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_firebase_configured(app_configs, **kwargs):
    from core.utils.firebase_messaging import firebase_initialized, initialize_firebase

    if not firebase_initialized:
        initialize_firebase()

    if firebase_initialized or settings.DEBUG:
        return []

    return [
        Warning(
            "Firebase Admin SDK is not configured — chat push notifications and "
            "all other FCM sends will silently no-op (0 tokens sent) in this "
            "environment.",
            hint=(
                "Set FIREBASE_SERVICE_ACCOUNT_PATH to the service account JSON "
                "(file path or raw JSON string) or place serviceAccountKey.json "
                "in the backend directory."
            ),
            id="core.W001",
        )
    ]
