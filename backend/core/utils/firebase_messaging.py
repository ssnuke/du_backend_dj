"""
Firebase Admin SDK initialization for sending push notifications
"""
import firebase_admin
from firebase_admin import credentials, messaging
import os
import json
import logging

logger = logging.getLogger(__name__)

# Initialize Firebase Admin SDK
# You need to place your Firebase service account JSON file in the project directory
# Or set FIREBASE_SERVICE_ACCOUNT_PATH with the full path or JSON string

firebase_initialized = False

def initialize_firebase():
    global firebase_initialized
    
    if firebase_initialized:
        return True
    
    try:
        # Try to get service account from environment variable first
        service_account_path = os.getenv('FIREBASE_SERVICE_ACCOUNT_PATH')
        
        if not service_account_path:
            # Check if serviceAccountKey.json exists in the current directory
            if os.path.exists('serviceAccountKey.json'):
                service_account_path = 'serviceAccountKey.json'
            elif os.path.exists('django-app/backend/serviceAccountKey.json'):
                service_account_path = 'django-app/backend/serviceAccountKey.json'
            else:
                logger.warning('Firebase service account key not found. Push notifications will not work.')
                return False
        
        if not firebase_admin._apps:
            # Check if service_account_path is a JSON string or a file path
            if service_account_path.startswith('{'):
                # It's a JSON string - parse it directly
                logger.info('Loading Firebase credentials from JSON string')
                service_account_json = json.loads(service_account_path)
                cred = credentials.Certificate(service_account_json)
            else:
                # It's a file path - load from file
                logger.info(f'Loading Firebase credentials from file: {service_account_path}')
                cred = credentials.Certificate(service_account_path)
            
            firebase_admin.initialize_app(cred)
            logger.info('Firebase Admin SDK initialized successfully')

        firebase_initialized = True
        _widen_messaging_connection_pool()
        return True

    except Exception as e:
        logger.error(f'Error initializing Firebase Admin SDK: {str(e)}')
        return False


def _widen_messaging_connection_pool(pool_maxsize=50):
    """
    send_each_for_multicast issues one HTTP request per token rather than a
    true batch request (Google deprecated the old batch endpoint), so a room
    with more members than urllib3's default pool size (10) logs a stream of
    "Connection pool is full, discarding connection" warnings and pays
    reconnect overhead on every send. Not a functional bug — sends still
    succeed — just needless churn once a room/broadcast has more than ~10
    recipients. Reaches into the SDK's internal HTTP session, so this is
    guarded and non-fatal if a future firebase_admin version restructures it.
    """
    try:
        import requests
        from firebase_admin import messaging as _messaging

        app = firebase_admin.get_app()
        session = _messaging._get_messaging_service(app)._client.session
        adapter = requests.adapters.HTTPAdapter(pool_connections=pool_maxsize, pool_maxsize=pool_maxsize)
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        logger.info(f'Widened FCM HTTP connection pool to {pool_maxsize}')
    except Exception as e:
        logger.warning(f'Could not widen FCM connection pool (non-fatal, sends still work): {e}')

def send_notification(fcm_token, title, body, data=None):
    """
    Send a push notification to a specific device using FCM token
    
    Args:
        fcm_token: FCM device token
        title: Notification title
        body: Notification body
        data: Optional dictionary of additional data (all values must be strings)
    
    Returns:
        message_id if successful, None otherwise
    """
    try:
        if not firebase_initialized and not initialize_firebase():
            logger.warning('Firebase not initialized, cannot send notification')
            return None
        
        # Ensure all data values are strings (FCM requirement)
        clean_data = {}
        if data:
            for key, value in data.items():
                clean_data[key] = str(value) if value is not None else ''
        
        logger.info(f'Sending FCM notification to token {fcm_token[:20]}... - Title: {title}')
        
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=clean_data,
            token=fcm_token,
        )
        
        response = messaging.send(message)
        logger.info(f'Successfully sent FCM notification. Message ID: {response}')
        return response
        
    except Exception as e:
        logger.error(f'Error sending FCM notification to {fcm_token[:20]}...: {str(e)}')
        logger.exception('Full traceback:')
        return None

_DEAD_TOKEN_EXCEPTION_TYPES = {
    # firebase_admin.messaging.UnregisteredError — app uninstalled or the
    # push subscription otherwise invalidated on the device.
    'UnregisteredError',
    # Malformed/invalid token string.
    'InvalidArgumentError',
}

# firebase_admin.exceptions.FirebaseError.code values (NOT the legacy FCM HTTP
# API's code strings like 'registration-token-not-registered' — those never
# appear from this SDK and were silently never matching here).
_DEAD_TOKEN_CODES = {
    'NOT_FOUND',
    'INVALID_ARGUMENT',
}


def is_dead_token_error(code, message, exception_type=None):
    """
    True if an FCM send failure means the token itself is permanently invalid
    (unregistered/uninstalled app, malformed token, etc.) and should be pruned
    from storage rather than retried. Shared by the chat push path
    (core/chat/consumers.py) and the generic notification path
    (core/utils/notifications.py) so both prune on the same signal.

    Prefers exception_type (the firebase_admin exception class name, e.g.
    'UnregisteredError') since it's the most stable signal across SDK
    versions; code/message are checked as a fallback for callers that don't
    have it.
    """
    if exception_type in _DEAD_TOKEN_EXCEPTION_TYPES:
        return True

    msg = str(message or '').lower()
    code = str(code or '')
    return (
        'unregistered' in msg
        or 'not registered' in msg
        or code in _DEAD_TOKEN_CODES
    )


def send_multicast(fcm_tokens, title, body, data=None):
    """
    Send a push notification to multiple devices in a single batched FCM call.

    Args:
        fcm_tokens: List of FCM device tokens
        title: Notification title
        body: Notification body
        data: Optional dictionary of additional data (all values must be strings)

    Returns:
        dict with success/failure counts and per-token failure_details
    """
    try:
        if not firebase_initialized and not initialize_firebase():
            logger.warning('Firebase not initialized, cannot send notifications')
            return {'success': 0, 'failure': len(fcm_tokens)}

        # Filter out empty or invalid tokens
        valid_tokens = [token for token in fcm_tokens if token and isinstance(token, str) and len(token) > 0]

        if not valid_tokens:
            logger.warning('No valid FCM tokens to send to')
            return {'success': 0, 'failure': 0}

        # Ensure all data values are strings (FCM requirement)
        clean_data = {}
        if data:
            for key, value in data.items():
                clean_data[key] = str(value) if value is not None else ''

        logger.info(f'Sending multicast FCM notification to {len(valid_tokens)} tokens (batched) - Title: {title}')

        multicast_message = messaging.MulticastMessage(
            tokens=valid_tokens,
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            webpush=messaging.WebpushConfig(
                headers={
                    "Urgency": "high"
                },
                notification=messaging.WebpushNotification(
                    title=title,
                    body=body,
                    icon='/icons/Icon-192.png',
                    require_interaction=False
                )
            ),
            data=clean_data,
        )

        batch_response = messaging.send_each_for_multicast(multicast_message)

        failure_details = []
        for idx, (token, resp) in enumerate(zip(valid_tokens, batch_response.responses)):
            if not resp.success:
                err = resp.exception
                failure_details.append({
                    'index': idx,
                    'token': token,
                    'code': getattr(err, 'code', None),
                    'message': str(err),
                    # Most reliable signal for is_dead_token_error() — the
                    # firebase_admin SDK's own exception class (e.g.
                    # 'UnregisteredError'), rather than .code/message strings
                    # which vary across SDK versions and don't always match
                    # what we check for (see is_dead_token_error docstring).
                    'exception_type': type(err).__name__,
                })
                logger.error(f'Failed to send to token {idx} ({token[:20]}...): {err}')

        logger.info(
            f'Multicast notification sent (batched). '
            f'Success: {batch_response.success_count}, Failure: {batch_response.failure_count}'
        )

        return {
            'success': batch_response.success_count,
            'failure': batch_response.failure_count,
            'failure_details': failure_details,
            'resp': None
        }

    except Exception as e:
        logger.error(f'Error sending multicast FCM notification: {str(e)}')
        logger.exception('Full traceback:')
        return {'success': 0, 'failure': len(fcm_tokens)}
