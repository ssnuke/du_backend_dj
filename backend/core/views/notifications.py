from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from core.models import Notification, Ir
from core.serializers import NotificationSerializer
from django.shortcuts import get_object_or_404

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_notifications(request):
    """
    Get notifications for the logged-in user.
    Query Params:
    - unread_only (bool): If true, returns only unread notifications.
    - limit (int): Limit number of results (default 20).
    """
    ir_id = request.user.username  # Assuming username is the IR ID
    
    try:
        ir = Ir.objects.get(ir_id=ir_id)
    except Ir.DoesNotExist:
        return Response({"error": "IR profile not found"}, status=status.HTTP_404_NOT_FOUND)

    notifications = Notification.objects.filter(recipient=ir)

    unread_only = request.GET.get('unread_only', 'false').lower() == 'true'
    if unread_only:
        notifications = notifications.filter(is_read=False)

    # Limit results
    limit = int(request.GET.get('limit', 20))
    notifications = notifications[:limit]

    serializer = NotificationSerializer(notifications, many=True)
    
    # Also get unread count
    unread_count = Notification.objects.filter(recipient=ir, is_read=False).count()
    
    return Response({
        "success": True,
        "notifications": serializer.data,
        "unread_count": unread_count
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, notification_id):
    """
    Mark a specific notification as read.
    """
    ir_id = request.user.username
    
    try:
        notification = Notification.objects.get(id=notification_id, recipient__ir_id=ir_id)
    except Notification.DoesNotExist:
        return Response({"error": "Notification not found or access denied"}, status=status.HTTP_404_NOT_FOUND)

    notification.is_read = True
    notification.save()
    
    return Response({"success": True, "message": "Notification marked as read"})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_all_read(request):
    """
    Mark all notifications for the user as read.
    """
    ir_id = request.user.username
    
    updated_count = Notification.objects.filter(recipient__ir_id=ir_id, is_read=False).update(is_read=True)
    
    return Response({"success": True, "message": f"{updated_count} notifications marked as read"})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_unread_count(request):
    """
    Get only the count of unread notifications (for polling).
    """
    ir_id = request.user.username
    unread_count = Notification.objects.filter(recipient__ir_id=ir_id, is_read=False).count()
    
    return Response({"success": True, "unread_count": unread_count})
