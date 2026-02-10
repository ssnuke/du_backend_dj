from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import UVDetail, Ir, Notification
from core.utils.notifications import get_notification_recipients, create_notifications

@receiver(post_save, sender=UVDetail)
def notify_uv_added(sender, instance, created, **kwargs):
    """
    Trigger notification when a new UV is added.
    Recipients: Admin, Ancestor CTCs, Team LDCs.
    """
    if not created:
        return

    uv_record = instance
    added_by_ir = uv_record.ir
    
    title = "New UV Record Added"
    message = f"{added_by_ir.ir_name} ({added_by_ir.ir_id}) added a new UV for prospect '{uv_record.prospect_name}'."
    
    recipients = get_notification_recipients(added_by_ir)

    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=Notification.Type.UV_ADDED,
        related_object_id=str(uv_record.id),
    )


@receiver(post_save, sender=Ir)
def notify_new_ir(sender, instance, created, **kwargs):
    """
    Trigger notification when a new IR is registered.
    Recipients: Admin, Ancestor CTCs.
    """
    if not created:
        return

    new_ir = instance
    
    title = "New IR Registered"
    message = f"New IR registered: {new_ir.ir_name} ({new_ir.ir_id})."
    
    recipients = get_notification_recipients(new_ir)

    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=Notification.Type.NEW_IR,
        related_object_id=str(new_ir.ir_id),
    )
