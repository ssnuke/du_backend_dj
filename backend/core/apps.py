from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        import core.signals
        
        # Initialize Firebase Admin SDK on app startup
        try:
            from core.utils.firebase_messaging import initialize_firebase
            if initialize_firebase():
                logger.info('Firebase Admin SDK initialized successfully on app startup')
            else:
                logger.warning('Firebase Admin SDK initialization failed - push notifications may not work')
        except Exception as e:
            logger.error(f'Error initializing Firebase on app startup: {str(e)}')
