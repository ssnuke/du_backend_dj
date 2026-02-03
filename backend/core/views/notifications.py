import logging
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from core.models import Notification, Ir
from core.serializers import NotificationSerializer

logger = logging.getLogger(__name__)

@api_view(['GET'])
def get_notifications(request):
    """
    Get notifications for the logged-in user.
    Query Params:
    - ir_id (str): The requester's IR ID.
    - unread_only (bool): If true, returns only unread notifications.
    - limit (int): Limit number of results (default 20).
    """
    try:
        ir_id = request.GET.get('ir_id') or request.query_params.get('ir_id')
        
        if not ir_id:
            return Response({"error": "ir_id parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"error": "IR profile not found"}, status=status.HTTP_404_NOT_FOUND)

        notifications = Notification.objects.filter(recipient=ir)

        unread_only = request.GET.get('unread_only', 'false').lower() == 'true'
        if unread_only:
            notifications = notifications.filter(is_read=False)

        # Limit results
        try:
            limit = int(request.GET.get('limit', 20))
        except ValueError:
            limit = 20
            
        notifications = notifications[:limit]

        serializer = NotificationSerializer(notifications, many=True)
        
        # Also get unread count
        unread_count = Notification.objects.filter(recipient=ir, is_read=False).count()
        
        return Response({
            "success": True,
            "notifications": serializer.data,
            "unread_count": unread_count
        })
    except Exception as e:
        logger.exception("Error in get_notifications")
        return Response({"error": f"Internal Server Error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def mark_notification_read(request, notification_id):
    """
    Mark a specific notification as read.
    """
    try:
        # Accept ir_id from body or query param
        ir_id = request.data.get('ir_id') or request.query_params.get('ir_id')
        
        if not ir_id:
            return Response({"error": "ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            notification = Notification.objects.get(id=notification_id, recipient__ir_id=ir_id)
        except Notification.DoesNotExist:
            return Response({"error": "Notification not found or access denied"}, status=status.HTTP_404_NOT_FOUND)

        notification.is_read = True
        notification.save()
        
        return Response({"success": True, "message": "Notification marked as read"})
    except Exception as e:
        logger.exception(f"Error in mark_notification_read for id {notification_id}")
        return Response({"error": f"Internal Server Error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def mark_all_read(request):
    """
    Mark all notifications for the user as read.
    """
    try:
        ir_id = request.data.get('ir_id') or request.query_params.get('ir_id')
        
        if not ir_id:
            return Response({"error": "ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        updated_count = Notification.objects.filter(recipient__ir_id=ir_id, is_read=False).update(is_read=True)
        
        return Response({"success": True, "message": f"{updated_count} notifications marked as read"})
    except Exception as e:
        logger.exception("Error in mark_all_read")
        return Response({"error": f"Internal Server Error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_unread_count(request):
    """
    Get only the count of unread notifications (for polling).
    """
    try:
        ir_id = request.GET.get('ir_id') or request.query_params.get('ir_id')
        
        if not ir_id:
            return Response({"success": False, "error": "ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        unread_count = Notification.objects.filter(recipient__ir_id=ir_id, is_read=False).count()
        
        return Response({"success": True, "unread_count": unread_count})
    except Exception as e:
        logger.exception("Error in get_unread_count")
        return Response({"error": f"Internal Server Error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
