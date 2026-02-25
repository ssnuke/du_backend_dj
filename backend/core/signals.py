from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import UVDetail, Ir, Notification, PlanDetail, InfoDetail, TeamMember
from core.utils.notifications import get_notification_recipients, create_notifications


@receiver(post_save, sender=UVDetail)
def notify_uv_saved(sender, instance, created, **kwargs):
    uv_record = instance
    added_by_ir = uv_record.ir
    recipients = get_notification_recipients(added_by_ir)

    if created:
        title = "New UV Record Added"
        message = f"{added_by_ir.ir_name} ({added_by_ir.ir_id}) added a new UV for prospect '{uv_record.prospect_name}'."
        notification_type = Notification.Type.UV_ADDED
    else:
        title = "UV Record Updated"
        message = f"{added_by_ir.ir_name} ({added_by_ir.ir_id}) updated the UV record for prospect '{uv_record.prospect_name}'."
        notification_type = Notification.Type.UV_UPDATED

    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=notification_type,
        related_object_id=str(uv_record.id),
    )


@receiver(post_delete, sender=UVDetail)
def notify_uv_deleted(sender, instance, **kwargs):
    uv_record = instance
    added_by_ir = uv_record.ir
    recipients = get_notification_recipients(added_by_ir)
    title = "UV Record Deleted"
    message = f"A UV record for prospect '{uv_record.prospect_name}' was deleted for {added_by_ir.ir_name} ({added_by_ir.ir_id})."
    
    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=Notification.Type.UV_DELETED,
        related_object_id=str(uv_record.id),
    )


@receiver(post_save, sender=PlanDetail)
def notify_plan_saved(sender, instance, created, **kwargs):
    plan = instance
    ir = plan.ir
    recipients = get_notification_recipients(ir)

    if created:
        title = "New Plan Added"
        message = f"{ir.ir_name} ({ir.ir_id}) added a new plan: {plan.plan_name or 'Plan'}."
        notification_type = Notification.Type.PLAN_ADDED
    else:
        title = "Plan Updated"
        message = f"{ir.ir_name} ({ir.ir_id}) updated plan: {plan.plan_name or 'Plan'}."
        notification_type = Notification.Type.PLAN_UPDATED

    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=notification_type,
        related_object_id=str(plan.id),
    )


@receiver(post_delete, sender=PlanDetail)
def notify_plan_deleted(sender, instance, **kwargs):
    plan = instance
    ir = plan.ir
    recipients = get_notification_recipients(ir)
    title = "Plan Deleted"
    message = f"Plan '{plan.plan_name or 'Plan'}' was deleted for {ir.ir_name} ({ir.ir_id})."
    
    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=Notification.Type.PLAN_DELETED,
        related_object_id=str(plan.id),
    )


# INFO notifications disabled per user request
# @receiver(post_save, sender=InfoDetail)
# def notify_info_saved(sender, instance, created, **kwargs):
#     info = instance
#     ir = info.ir
#     recipients = get_notification_recipients(ir)

#     if created:
#         title = "New Info Added"
#         message = f"{ir.ir_name} ({ir.ir_id}) added new info: {info.info_name or 'Info'}."
#         notification_type = Notification.Type.INFO_ADDED
#     else:
#         title = "Info Updated"
#         message = f"{ir.ir_name} ({ir.ir_id}) updated info: {info.info_name or 'Info'}."
#         notification_type = Notification.Type.INFO_UPDATED

#     create_notifications(
#         recipients=recipients,
#         title=title,
#         message=message,
#         notification_type=notification_type,
#         related_object_id=str(info.id),
#     )


# INFO notifications disabled per user request
# @receiver(post_delete, sender=InfoDetail)
# def notify_info_deleted(sender, instance, **kwargs):
#     info = instance
#     ir = info.ir
#     recipients = get_notification_recipients(ir)
#     title = "Info Deleted"
#     message = f"Info '{info.info_name or 'Info'}' was deleted for {ir.ir_name} ({ir.ir_id})."
    
#     create_notifications(
#         recipients=recipients,
#         title=title,
#         message=message,
#         notification_type=Notification.Type.INFO_DELETED,
#         related_object_id=str(info.id),
#     )


@receiver(post_save, sender=Ir)
def notify_ir_saved(sender, instance, created, **kwargs):
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


@receiver(post_delete, sender=Ir)
def notify_ir_deleted(sender, instance, **kwargs):
    ir = instance
    title = "IR Deleted"
    message = f"IR deleted: {ir.ir_name} ({ir.ir_id})."
    
    # Try to notify parent since IR is being deleted
    if ir.parent_ir:
        recipients = get_notification_recipients(ir.parent_ir)
    else:
        recipients = Ir.objects.filter(ir_access_level=1)

    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=Notification.Type.IR_DELETED,
        related_object_id=str(ir.ir_id),
    )


@receiver(post_save, sender=TeamMember)
def notify_member_saved(sender, instance, created, **kwargs):
    team_member = instance
    ir = team_member.ir
    team = team_member.team
    recipients = get_notification_recipients(ir)

    if created:
        title = "Team Member Added"
        message = f"{ir.ir_name} ({ir.ir_id}) was added to team '{team.name}' as {team_member.role}."
        notification_type = Notification.Type.MEMBER_ADDED
    else:
        title = "Team Member Updated"
        message = f"{ir.ir_name} ({ir.ir_id})'s role in team '{team.name}' was updated to {team_member.role}."
        notification_type = Notification.Type.MEMBER_UPDATED

    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=notification_type,
        related_object_id=str(team_member.id),
    )


@receiver(post_delete, sender=TeamMember)
def notify_member_deleted(sender, instance, **kwargs):
    team_member = instance
    ir = team_member.ir
    team = team_member.team
    recipients = get_notification_recipients(ir)
    title = "Team Member Removed"
    message = f"{ir.ir_name} ({ir.ir_id}) was removed from team '{team.name}'."
    
    create_notifications(
        recipients=recipients,
        title=title,
        message=message,
        notification_type=Notification.Type.MEMBER_DELETED,
        related_object_id=str(team_member.id),
    )
