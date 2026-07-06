from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        import core.signals
        import core.checks  # registers check_firebase_configured with `manage.py check`

        # Initialize Firebase Admin SDK on app startup
        try:
            from django.conf import settings
            from core.utils.firebase_messaging import initialize_firebase
            if initialize_firebase():
                logger.info('Firebase Admin SDK initialized successfully on app startup')
            elif settings.DEBUG:
                logger.warning('Firebase Admin SDK initialization failed - push notifications may not work')
            else:
                # In production this means every chat/notification push will
                # silently no-op — log loudly enough to not get lost among
                # normal startup noise (see also core.checks.check_firebase_configured).
                logger.error(
                    'Firebase Admin SDK failed to initialize in production — '
                    'ALL push notifications will silently fail until this is fixed'
                )
        except Exception as e:
            logger.error(f'Error initializing Firebase on app startup: {str(e)}')
