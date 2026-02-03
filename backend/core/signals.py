from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import UVDetail, Ir, Notification, AccessLevel, TeamRole, TeamMember

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
    
    recipients = set()

    # 1. Admin (Level 1)
    admins = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN)
    for admin in admins:
        recipients.add(admin)

    # 2. Ancestor CTCs (Level 2) in the hierarchy
    # We can use the hierarchy_path to find all ancestors
    ancestors = added_by_ir.get_all_uplines()
    ctc_ancestors = ancestors.filter(ir_access_level=AccessLevel.CTC)
    for ctc in ctc_ancestors:
        recipients.add(ctc)

    # 3. Team LDCs (Level 3)
    # Find all teams this IR is part of
    team_memberships = TeamMember.objects.filter(ir=added_by_ir)
    for membership in team_memberships:
        team = membership.team
        
        # Find LDC of this team
        # Logic: Find members of this team who have role=LDC
        ldc_members = TeamMember.objects.filter(team=team, role=TeamRole.LDC)
        for ldc_mem in ldc_members:
             # LDC should not notify themselves if they added their own UV (though rarely happens)
            if ldc_mem.ir.ir_id != added_by_ir.ir_id:
                recipients.add(ldc_mem.ir)

        # Also notify the Creator of the team if they are LDC (sometimes creator isn't a member explicitly)
        if team.created_by and team.created_by.ir_access_level == AccessLevel.LDC:
             notify_creator = True
             # If creator is the one who added, don't notify
             if team.created_by.ir_id == added_by_ir.ir_id:
                 notify_creator = False
             
             if notify_creator:
                recipients.add(team.created_by)

    # Bulk create notifications
    notifications_to_create = []
    for recipient in recipients:
        notifications_to_create.append(
            Notification(
                recipient=recipient,
                title=title,
                message=message,
                notification_type=Notification.Type.UV_ADDED,
                related_object_id=str(uv_record.id)
            )
        )
    
    Notification.objects.bulk_create(notifications_to_create)


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
    
    recipients = set()

    # 1. Admin (Level 1)
    admins = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN)
    for admin in admins:
        recipients.add(admin)

    # 2. Ancestor CTCs (Level 2)
    # We can use the hierarchy_path to find all ancestors
    # Note: When IR is just created, if parent is set, hierarchy might be set.
    # The 'save' method of Ir sets the path. post_save comes after that.
    ancestors = new_ir.get_all_uplines()
    ctc_ancestors = ancestors.filter(ir_access_level=AccessLevel.CTC)
    for ctc in ctc_ancestors:
        recipients.add(ctc)

    # Note: Newly registered IRs might not belong to a team immediately, 
    # so we might not be able to notify LDCs yet unless we look at the parent's team.
    # For now, we stick to Admin + Upline CTCs as per requirements.

    # Bulk create notifications
    notifications_to_create = []
    for recipient in recipients:
        notifications_to_create.append(
            Notification(
                recipient=recipient,
                title=title,
                message=message,
                notification_type=Notification.Type.NEW_IR,
                related_object_id=str(new_ir.ir_id)
            )
        )
    
    Notification.objects.bulk_create(notifications_to_create)
