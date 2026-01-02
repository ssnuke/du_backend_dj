from django.urls import path
from core.views.get import (
    GetAllIR,
    GetSingleIR,
    GetAllRegisteredIR,
    GetAllTeams,
    GetTeamMembers,
    GetInfoDetails,
    GetTargetsDashboard,
    GetTeamInfoTotal,
    GetLDCs,
    GetTeamsByLDC,
    GetTeamsByIR,
        GetTargets,
)
from core.views.post import (
    AddIrId,
    RegisterIR,
    IRLogin,
    CreateTeam,
    AddIrToTeam,
    AddInfoDetail,
    AddPlanDetail,
    SetTargets,
    ResetDatabase,
)

from core.views.health import health_check
from core.views.put import (
    UpdateIrDetails,
    UpdateInfoDetail,
    SetTargetsPut,
    UpdateTeamName,
)
from core.views.delete import (
    ResetDatabase,
    DeleteTeam,
    RemoveIrFromTeam,
    DeleteInfoDetail,
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
    path("targets_dashboard/<str:ir_id>/", GetTargetsDashboard.as_view()),
    path("get_targets/", GetTargets.as_view()),
    path("teams_by_ir/<str:ir_id>/", GetTeamsByIR.as_view()),
    path("team_info_total/<int:team_id>/", GetTeamInfoTotal.as_view()),
    #POST endpoints
    path("add_ir_id/", AddIrId.as_view()),
    path("register_new_ir/", RegisterIR.as_view()),
    path("login/", IRLogin.as_view()),
    path("create_team/", CreateTeam.as_view()),
    path("add_ir_to_team/", AddIrToTeam.as_view()),
    path("add_info_detail/<str:ir_id>/", AddInfoDetail.as_view()),
    path("add_plan_detail/<str:ir_id>/", AddPlanDetail.as_view()),
    path("set_targets/", SetTargets.as_view()),
    path("reset_database/", ResetDatabase.as_view()),
    #PUT endpoints
    path("update_ir/<str:update_ir>/", UpdateIrDetails.as_view()),
    path("update_info_detail/<int:info_id>/", UpdateInfoDetail.as_view()),
    path("set_targets/", SetTargetsPut.as_view()),
    path("update_team_name/<int:team_id>/", UpdateTeamName.as_view()),
    #DELETE endpoints
    path("delete_team/<int:team_id>/", DeleteTeam.as_view()),
    path("remove_ir_from_team/<int:team_id>/<str:ir_id>/", RemoveIrFromTeam.as_view()),
    path("delete_info_detail/<int:info_id>/", DeleteInfoDetail.as_view()),
    # Health Check endpoint
    path("health/", health_check)
]
