from typing import Iterable
import json

from django.conf import settings
from pywebpush import WebPushException, webpush

from core.models import Notification, Ir, AccessLevel, TeamMember, TeamRole, PushSubscription


def get_notification_recipients(target_ir: Ir) -> set:
    """
    Build recipient list based on visibility rules.
    - Always include Admins.
    - Include uplines (CTC/LDC).
    - Include team LDCs for teams the IR belongs to.
    - Filter non-admins to only those who can view the target IR.
    """
    recipients = set()

    admins = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN)
    for admin in admins:
        recipients.add(admin)

    uplines = target_ir.get_all_uplines().filter(ir_access_level__in=[AccessLevel.CTC, AccessLevel.LDC])
    for upline in uplines:
        recipients.add(upline)

    team_memberships = TeamMember.objects.filter(ir=target_ir).select_related("team")
    for membership in team_memberships:
        team = membership.team
        ldc_members = TeamMember.objects.filter(team=team, role=TeamRole.LDC).select_related("ir")
        for ldc_mem in ldc_members:
            if ldc_mem.ir.ir_id != target_ir.ir_id:
                recipients.add(ldc_mem.ir)

        if team.created_by and team.created_by.ir_access_level == AccessLevel.LDC:
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
    except Exception:
        return

    notifications_list = list(notifications)
    if not notifications_list:
        return

    sample = notifications_list[0]
    data = {}
    if sample.notification_type:
        data["notification_type"] = sample.notification_type
    if sample.related_object_id:
        data["related_object_id"] = str(sample.related_object_id)

    tokens = []
    for notification in notifications_list:
        recipient = notification.recipient
        if recipient.fcm_tokens and isinstance(recipient.fcm_tokens, list):
            tokens.extend(recipient.fcm_tokens)

    if not tokens:
        return

    tokens = list(dict.fromkeys(tokens))
    if len(tokens) == 1:
        send_notification(tokens[0], title, message, data)
    else:
        send_multicast(tokens, title, message, data)
