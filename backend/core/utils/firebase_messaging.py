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
        data: Optional dictionary of additional data
    
    Returns:
        message_id if successful, None otherwise
    """
    try:
        if not firebase_initialized and not initialize_firebase():
            logger.warning('Firebase not initialized, cannot send notification')
            return None
        
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            token=fcm_token,
        )
        
        response = messaging.send(message)
        logger.info(f'Successfully sent FCM notification: {response}')
        return response
        
    except Exception as e:
        logger.error(f'Error sending FCM notification: {str(e)}')
        return None

def send_multicast(fcm_tokens, title, body, data=None):
    """
    Send a push notification to multiple devices
    
    Args:
        fcm_tokens: List of FCM device tokens
        title: Notification title
        body: Notification body
        data: Optional dictionary of additional data
    
    Returns:
        dict with success and failure counts
    """
    try:
        if not firebase_initialized and not initialize_firebase():
            logger.warning('Firebase not initialized, cannot send notifications')
            return {'success': 0, 'failure': len(fcm_tokens)}
        
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            tokens=fcm_tokens,
        )
        
        response = messaging.send_multicast(message)
        logger.info(f'Multicast notification sent. Success: {response.success_count}, Failure: {response.failure_count}')
        
        return {
            'success': response.success_count,
            'failure': response.failure_count,
            'resp': response
        }
        
    except Exception as e:
        logger.error(f'Error sending multicast FCM notification: {str(e)}')
        return {'success': 0, 'failure': len(fcm_tokens)}
