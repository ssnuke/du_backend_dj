from typing import Iterable
import json

from django.conf import settings
from pywebpush import WebPushException, webpush

from core.models import Notification, Ir, AccessLevel, TeamMember, TeamRole, PushSubscription


def get_notification_recipients(target_ir: Ir) -> set:
    """
    Build recipient list based on visibility rules.
    - Always include Admins.
    - Include upline CTCs (who manage branches).
    - Include LDCs only if they specifically manage teams the IR belongs to.
    - Filter non-admins to only those who can view the target IR.
    """
    recipients = set()

    # 1. Admins get everything
    admins = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN, status=True)
    for admin in admins:
        recipients.add(admin)

    # 2. CTCs get subtree notifications
    ctc_uplines = target_ir.get_all_uplines().filter(ir_access_level=AccessLevel.CTC, status=True)
    for ctc in ctc_uplines:
        recipients.add(ctc)

    # 3. LDCs only for teams they specifically manage
    team_memberships = TeamMember.objects.filter(ir=target_ir).select_related("team")
    for membership in team_memberships:
        team = membership.team
        
        # Members with LDC role in the team
        ldc_members = TeamMember.objects.filter(team=team, role=TeamRole.LDC).select_related("ir")
        for ldc_mem in ldc_members:
            if ldc_mem.ir.status and ldc_mem.ir.ir_id != target_ir.ir_id:
                recipients.add(ldc_mem.ir)

        # Team creator if they are an LDC
        if team.created_by and team.created_by.status and team.created_by.ir_access_level == AccessLevel.LDC:
            if team.created_by.ir_id != target_ir.ir_id:
                recipients.add(team.created_by)

    # Filter by visibility for non-admin recipients
    filtered = set()
    for recipient in recipients:
        if recipient.ir_access_level == AccessLevel.ADMIN:
            filtered.add(recipient)
            continue
        if recipient.get_viewable_irs().filter(ir_id=target_ir.ir_id).exists():
            filtered.add(recipient)

    return filtered


def create_notifications(
    recipients: Iterable[Ir],
    title: str,
    message: str,
    notification_type: str,
    related_object_id: str | None = None,
) -> None:
    notifications_to_create = []
    for recipient in recipients:
        notifications_to_create.append(
            Notification(
                recipient=recipient,
                title=title,
                message=message,
                notification_type=notification_type,
                related_object_id=related_object_id,
            )
        )

    if notifications_to_create:
        Notification.objects.bulk_create(notifications_to_create)
        send_push_notifications(
            notifications=notifications_to_create,
            title=title,
            message=message,
        )
        send_fcm_notifications(
            notifications=notifications_to_create,
            title=title,
            message=message,
        )


def send_push_notifications(notifications: Iterable[Notification], title: str, message: str) -> None:
    vapid_public_key = getattr(settings, "VAPID_PUBLIC_KEY", None)
    vapid_private_key = getattr(settings, "VAPID_PRIVATE_KEY", None)
    vapid_subject = getattr(settings, "VAPID_SUBJECT", "mailto:admin@example.com")

    if not vapid_public_key or not vapid_private_key:
        return

    for notification in notifications:
        recipient = notification.recipient
        subscriptions = PushSubscription.objects.filter(ir=recipient)
        for sub in subscriptions:
            payload = {
                "title": title,
                "message": message,
                "notification_id": notification.id,
                "notification_type": notification.notification_type,
            }

            subscription_info = {
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh,
                    "auth": sub.auth,
                }
            }

            try:
                webpush(
                    subscription_info=subscription_info,
                    data=json.dumps(payload),
                    vapid_private_key=vapid_private_key,
                    vapid_claims={"sub": vapid_subject},
                )
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in [404, 410]:
                    PushSubscription.objects.filter(id=sub.id).delete()


def send_fcm_notifications(notifications: Iterable[Notification], title: str, message: str) -> None:
    try:
        from core.utils.firebase_messaging import send_multicast, send_notification
        import logging
        logger = logging.getLogger(__name__)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Failed to import firebase messaging: {str(e)}')
        return

    notifications_list = list(notifications)
    if not notifications_list:
        logger.warning('send_fcm_notifications: No notifications to send')
        return

    sample = notifications_list[0]
    # Ensure all data values are strings (FCM requirement)
    data = {}
    if sample.notification_type:
        data["notification_type"] = str(sample.notification_type)
    if sample.related_object_id:
        data["related_object_id"] = str(sample.related_object_id)

    # Collect all FCM tokens from recipients
    tokens = []
    for notification in notifications_list:
        recipient = notification.recipient
        if recipient.fcm_tokens and isinstance(recipient.fcm_tokens, list):
            # Filter out empty or invalid tokens
            valid_tokens = [t for t in recipient.fcm_tokens if t and isinstance(t, str) and len(t) > 0]
            tokens.extend(valid_tokens)

    if not tokens:
        logger.warning(f'send_fcm_notifications: No FCM tokens found for {len(notifications_list)} notifications')
        return

    # Remove duplicates while preserving order
    tokens = list(dict.fromkeys(tokens))
    
    logger.info(f'send_fcm_notifications: Sending to {len(tokens)} unique FCM tokens for {len(notifications_list)} notifications')
    
    # Send notifications
    logger.info(f">>> [AUTO NOTIFICATION] ABOUT TO SEND PUSH TO DEVICES. Tokens: {tokens}, Title: {title}")
    
    result = send_multicast(tokens, title, message, data)
    logger.info(f'send_fcm_notifications: Multicast result - Success: {result.get("success", 0)}, Failure: {result.get("failure", 0)}')
    
    # Process failures and clean up invalid tokens
    if result.get("failure", 0) > 0:
        bad_tokens = set()
        for detail in result.get("failure_details", []):
            msg = str(detail.get("message", ""))
            code = str(detail.get("code", ""))
            if "NotRegistered" in msg or "invalid" in msg.lower() or code in ["registration-token-not-registered", "invalid-registration-token"]:
                bad_tokens.add(detail.get("token"))
                
        if bad_tokens:
            logger.info(f"Removing {len(bad_tokens)} invalid FCM tokens from database")
            for notification in notifications_list:
                recipient = notification.recipient
                if recipient.fcm_tokens and isinstance(recipient.fcm_tokens, list):
                    original_len = len(recipient.fcm_tokens)
                    new_tokens = [t for t in recipient.fcm_tokens if t not in bad_tokens]
                    if len(new_tokens) < original_len:
                        recipient.fcm_tokens = new_tokens
                        recipient.save(update_fields=['fcm_tokens'])
