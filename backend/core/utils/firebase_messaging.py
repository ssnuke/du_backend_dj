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
        return True
        
    except Exception as e:
        logger.error(f'Error initializing Firebase Admin SDK: {str(e)}')
        return False

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

def send_multicast(fcm_tokens, title, body, data=None):
    """
    Send a push notification to multiple devices
    
    Args:
        fcm_tokens: List of FCM device tokens
        title: Notification title
        body: Notification body
        data: Optional dictionary of additional data (all values must be strings)
    
    Returns:
        dict with success and failure counts
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
        
        logger.info(f'Sending multicast FCM notification to {len(valid_tokens)} tokens - Title: {title}')
        
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=clean_data,
            tokens=valid_tokens,
        )
        
        response = messaging.send_multicast(message)
        logger.info(f'Multicast notification sent. Success: {response.success_count}, Failure: {response.failure_count}')
        
        failure_details = []
        # Log detailed error information for failed tokens
        if response.failure_count > 0:
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    err = resp.exception
                    err_code = getattr(err, 'code', None)
                    err_msg = str(err)
                    failure_details.append({
                        'index': idx,
                        'token': valid_tokens[idx][:20] + '...',
                        'code': err_code,
                        'message': err_msg,
                    })
                    logger.error(f'Failed to send to token {idx} ({valid_tokens[idx][:20]}...): {err_msg}')
        
        return {
            'success': response.success_count,
            'failure': response.failure_count,
            'failure_details': failure_details,
            'resp': response
        }
        
    except Exception as e:
        logger.error(f'Error sending multicast FCM notification: {str(e)}')
        logger.exception('Full traceback:')
        return {'success': 0, 'failure': len(fcm_tokens)}
