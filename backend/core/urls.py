from django.urls import path
from core.views.get import (
    GetAllIR,
    GetSingleIR,
    GetAllRegisteredIR,
    GetAllTeams,
    GetTeamMembers,
    GetInfoDetails,
    GetPlanDetails,
    GetTargetsDashboard,
    GetTeamInfoTotal,
    GetLDCs,
    GetTeamsByLDC,
    GetTeamsByIR,
    GetTargets,
    GetUVCount,
    GetTeamUVTotal,
    # New hierarchy-based endpoints
    GetVisibleTeams,
    GetDownlineData,
    GetDirectDownlines,
    GetHierarchyTree,
    GetAvailableWeeks,
)
from core.views.post import (
    AddIrId,
    RegisterIR,
    BulkRegisterIRFromExcel,
    IRLogin,
    CreateTeam,
    AddIrToTeam,
    AddInfoDetail,
    AddPlanDetail,
    AddUV,
    SetTargets,
    ResetDatabase,
    PasswordReset,
    ChangeIRAccessLevel,
)

from core.views.health import health_check
from core.views.put import (
    UpdateIrDetails,
    UpdateInfoDetail,
    UpdatePlanDetail,
    SetTargetsPut,
    UpdateTeamName,
    TransferTeamOwnership,
    UpdateParentIR,
    UpdateWeeklyTargets,
    UpdateIrName,
    UpdateIrId,
    UpdateUVCount,
)
from core.views.delete import (
    ResetDatabase,
    DeleteTeam,
    RemoveIrFromTeam,
    DeleteInfoDetail,
    DeletePlanDetail,
    DeleteIr,
    DeleteUVDetail,
)
from core.views.notifications import (
    get_notifications,
    mark_notification_read,
    mark_all_read,
    get_unread_count,
    get_vapid_public_key,
    subscribe_push,
    unsubscribe_push,
)
from core.views.move_ir import MoveIrToTeam

# Pocket views
from core.views.pockets import (
    CreatePocket,
    GetPockets,
    GetPocketDetail,
    UpdatePocket,
    DeletePocket,
    AddMemberToPocket,
    RemoveMemberFromPocket,
    MoveMemberBetweenPockets,
    SplitTargetToPockets,
    GetPocketTargets,
)


urlpatterns = [
    #GET endpoints
    path("get_all_ir/", GetAllIR.as_view()),
    path("ir/<str:fetch_ir_id>/", GetSingleIR.as_view()),
    path("irs/", GetAllRegisteredIR.as_view()),
    path("teams/", GetAllTeams.as_view()),
    path("ldcs/", GetLDCs.as_view()),
    path("teams_by_ldc/<str:ldc_id>/", GetTeamsByLDC.as_view()),
    path("team_members/<int:team_id>/", GetTeamMembers.as_view()),
    path("info_details/<str:ir_id>/", GetInfoDetails.as_view()),
    path("plan_details/<str:ir_id>/", GetPlanDetails.as_view()),
    path("uv_count/<str:ir_id>/", GetUVCount.as_view()),
    path("targets_dashboard/<str:ir_id>/", GetTargetsDashboard.as_view()),
    path("get_targets/", GetTargets.as_view()),
    path("teams_by_ir/<str:ir_id>/", GetTeamsByIR.as_view()),
    path("team_info_total/<int:team_id>/", GetTeamInfoTotal.as_view()),
    path("team_uv_total/<int:team_id>/", GetTeamUVTotal.as_view()),
    # New hierarchy-based endpoints
    path("visible_teams/<str:ir_id>/", GetVisibleTeams.as_view()),
    path("downline_data/<str:ir_id>/", GetDownlineData.as_view()),
    path("direct_downlines/<str:ir_id>/", GetDirectDownlines.as_view()),
    path("hierarchy_tree/<str:ir_id>/", GetHierarchyTree.as_view()),
    path("available_weeks/", GetAvailableWeeks.as_view()),
    #POST endpoints
    path("add_ir_id/", AddIrId.as_view()),
    path("register_new_ir/", RegisterIR.as_view()),
    path("bulk_register_from_excel/", BulkRegisterIRFromExcel.as_view()),
    path("login/", IRLogin.as_view()),
    path("create_team/", CreateTeam.as_view()),
    path("add_ir_to_team/", AddIrToTeam.as_view()),
    path("add_info_detail/<str:ir_id>/", AddInfoDetail.as_view()),
    path("add_plan_detail/<str:ir_id>/", AddPlanDetail.as_view()),
    path("add_uv/<str:ir_id>/", AddUV.as_view()),
    path("set_targets/", SetTargets.as_view()),
    path("password_reset/", PasswordReset.as_view()),
    path("change_access_level/", ChangeIRAccessLevel.as_view()),
    path("reset_database/", ResetDatabase.as_view()),
    #PUT endpoints
    path("update_ir/<str:update_ir>/", UpdateIrDetails.as_view()),
    path("update_info_detail/<int:info_id>/", UpdateInfoDetail.as_view()),
    path("update_plan_detail/<int:plan_id>/", UpdatePlanDetail.as_view()),
    path("update_team_name/<int:team_id>/", UpdateTeamName.as_view()),
    path("transfer_team_ownership/<int:team_id>/", TransferTeamOwnership.as_view()),
    path("update_ir_name/<str:ir_id>/", UpdateIrName.as_view()),
    path("update_ir_id/", UpdateIrId.as_view()),
    path("update_parent_ir/<str:ir_id>/", UpdateParentIR.as_view()),
    path("update_weekly_targets/", UpdateWeeklyTargets.as_view()),
    path("update_uv_count/<int:uv_id>/", UpdateUVCount.as_view()),
    path("move_ir_to_team/", MoveIrToTeam.as_view()),
    #DELETE endpoints
    path("delete_team/<int:team_id>/", DeleteTeam.as_view()),
    path("remove_ir_from_team/<int:team_id>/<str:ir_id>/", RemoveIrFromTeam.as_view()),
    path("delete_info_detail/<int:info_id>/", DeleteInfoDetail.as_view()),
    path("delete_plan_detail/<int:plan_id>/", DeletePlanDetail.as_view()),
    path("delete_uv_detail/<int:uv_id>/", DeleteUVDetail.as_view()),
    path("delete_ir/<str:ir_id>/", DeleteIr.as_view()),
    # Health Check endpoint
    path("health/", health_check),
    
    # ============ POCKET ENDPOINTS ============
    # Pocket CRUD
    path("pockets/create/", CreatePocket.as_view()),
    path("pockets/<int:team_id>/", GetPockets.as_view()),
    path("pocket/<int:pocket_id>/", GetPocketDetail.as_view()),
    path("pocket/<int:pocket_id>/update/", UpdatePocket.as_view()),
    path("pocket/<int:pocket_id>/delete/", DeletePocket.as_view()),
    
    # Pocket member management
    path("pocket/members/add/", AddMemberToPocket.as_view()),
    path("pocket/members/remove/", RemoveMemberFromPocket.as_view()),
    path("pocket/members/move/", MoveMemberBetweenPockets.as_view()),
    
    # Target allocation
    path("pockets/split_targets/", SplitTargetToPockets.as_view()),
    path("pockets/targets/", GetPocketTargets.as_view()),
    
    # ============ NOTIFICATION ENDPOINTS ============
    path("notifications/", get_notifications),
    path("notifications/unread_count/", get_unread_count),
    path("notifications/vapid_public_key/", get_vapid_public_key),
    path("notifications/subscribe/", subscribe_push),
    path("notifications/unsubscribe/", unsubscribe_push),
    path("notifications/<int:notification_id>/read/", mark_notification_read),
    path("notifications/mark_all_read/", mark_all_read),
]
