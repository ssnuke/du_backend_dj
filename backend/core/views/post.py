from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from core.utils.dates import get_current_week_start, get_saturday_friday_week_info
from django.db.models import F

from core.models import (
    IrId,
    Ir,
    Team,
    TeamMember,
    InfoDetail,
    PlanDetail,
    TeamWeek,
    TeamRole,
    WeeklyTarget,
)
from core.serializers import (
    IrIdSerializer,
    IrRegisterSerializer,
    TeamSerializer,
    InfoDetailSerializer,
    PlanDetailSerializer,
)

import logging
from django.db import IntegrityError

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------
# ADD IR ID
# ---------------------------------------------------
class AddIrId(APIView):
    def post(self, request):
        payload = request.data

        if payload is None:
            return Response({"detail": "Empty payload"}, status=status.HTTP_400_BAD_REQUEST)

        items = payload if isinstance(payload, list) else [payload]

        if not items:
            return Response({"detail": "Empty list provided"}, status=status.HTTP_400_BAD_REQUEST)

        errors = []
        seen = set()
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append({"index": idx, "error": "Invalid item format, expected object"})
                continue

            ir_id = item.get("ir_id")
            if not ir_id:
                errors.append({"index": idx, "error": "ir_id missing"})
                continue

            if ir_id in seen:
                errors.append({"index": idx, "ir_id": ir_id, "error": "Duplicate in payload"})
                continue
            seen.add(ir_id)

            if IrId.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "IR ID already exists"})

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        serializer = IrIdSerializer(data=items, many=True)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                created = serializer.save()
        except IntegrityError:
            logging.exception("IntegrityError while bulk adding IrId entries")
            return Response({"detail": "Database integrity error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception:
            logging.exception("Unexpected error while bulk adding IrId entries")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        created_ids = [obj.ir_id for obj in created]
        return Response({"message": "IrId(s) added", "ir_ids": created_ids}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------
# REGISTER NEW IR
# ---------------------------------------------------
class RegisterIR(APIView):
    def post(self, request):
        payload = request.data

        # Basic payload validation
        if payload is None:
            return Response({"detail": "Empty payload"}, status=status.HTTP_400_BAD_REQUEST)

        items = payload if isinstance(payload, list) else [payload]

        if not items:
            return Response({"detail": "Empty list provided"}, status=status.HTTP_400_BAD_REQUEST)

        for idx, itm in enumerate(items):
            if not isinstance(itm, dict):
                return Response({"detail": "Invalid payload format, expected object or list of objects", "index": idx}, status=status.HTTP_400_BAD_REQUEST)

        # Pre-check whitelist and existing registrations
        errors = []
        for idx, item in enumerate(items):
            ir_id = item.get("ir_id")
            if not ir_id:
                errors.append({"index": idx, "error": "ir_id missing"})
                continue
            if not IrId.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "IR ID Not Found"})
            if Ir.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "Already registered"})

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        serializer = IrRegisterSerializer(data=items, many=True)
        # Collect serializer errors without raising to format per-item errors
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                created = serializer.save()
        except IntegrityError as e:
            logging.exception("IntegrityError while bulk registering IRs")
            return Response({"detail": "Database integrity error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            logging.exception("Unexpected error while bulk registering IRs")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        created_ids = [obj.ir_id for obj in created]

        return Response(
            {"message": "IR(s) registered successfully", "ir_ids": created_ids},
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------
# BULK REGISTER IR FROM EXCEL
# ---------------------------------------------------
class BulkRegisterIRFromExcel(APIView):
    def post(self, request):
        if 'file' not in request.FILES:
            return Response(
                {"detail": "No file uploaded. Please provide an Excel file."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        excel_file = request.FILES['file']
        
        # Validate file extension
        if not excel_file.name.endswith(('.xlsx', '.xls')):
            return Response(
                {"detail": "Invalid file format. Please upload an Excel file (.xlsx or .xls)"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            import pandas as pd
            
            # Read Excel file
            df = pd.read_excel(excel_file)
            
            # Validate required columns
            required_columns = ['ir_name', 'ir_id', 'ir_email', 'ir_access_level']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                return Response(
                    {"detail": f"Missing required columns: {', '.join(missing_columns)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Remove rows with any missing values
            df = df.dropna(subset=required_columns)
            
            if df.empty:
                return Response(
                    {"detail": "No valid data found in the Excel file"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Prepare data for registration
            errors = []
            ir_ids_to_add = []
            irs_to_register = []
            
            for idx, row in df.iterrows():
                ir_id = str(row['ir_id']).strip()
                ir_name = str(row['ir_name']).strip()
                ir_email = str(row['ir_email']).strip()
                
                try:
                    ir_access_level = int(row['ir_access_level'])
                except (ValueError, TypeError):
                    errors.append({
                        "row": idx + 2,  # Excel row (accounting for header)
                        "ir_id": ir_id,
                        "error": "Invalid access level (must be a number)"
                    })
                    continue
                
                # Validate email format (basic check)
                if '@' not in ir_email:
                    errors.append({
                        "row": idx + 2,
                        "ir_id": ir_id,
                        "error": "Invalid email format"
                    })
                    continue
                
                # Check if IR already exists
                if Ir.objects.filter(ir_id=ir_id).exists():
                    errors.append({
                        "row": idx + 2,
                        "ir_id": ir_id,
                        "error": "Already registered"
                    })
                    continue
                
                # Prepare data for batch creation
                ir_ids_to_add.append(ir_id)
                irs_to_register.append({
                    'ir_id': ir_id,
                    'ir_name': ir_name,
                    'ir_email': ir_email,
                    'ir_access_level': ir_access_level,
                    'ir_password': 'secret'  # Default password
                })
            
            if not irs_to_register:
                return Response({
                    "detail": "No valid IRs to register",
                    "errors": errors
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Register IRs in database
            try:
                with transaction.atomic():
                    # First, add IR IDs to whitelist if they don't exist
                    for ir_id in ir_ids_to_add:
                        IrId.objects.get_or_create(ir_id=ir_id)
                    
                    # Create IR records with default password
                    created_irs = []
                    for ir_data in irs_to_register:
                        ir = Ir(
                            ir_id=ir_data['ir_id'],
                            ir_name=ir_data['ir_name'],
                            ir_email=ir_data['ir_email'],
                            ir_access_level=ir_data['ir_access_level'],
                            started_date=timezone.now()    
                        )
                        ir.set_password('secret')  # Set default password
                        ir.save()
                        created_irs.append(ir.ir_id)
                    
                    response_data = {
                        "message": f"Successfully registered {len(created_irs)} IR(s) from Excel",
                        "registered_count": len(created_irs),
                        "ir_ids": created_irs,
                        "default_password": "secret"
                    }
                    
                    if errors:
                        response_data["skipped_count"] = len(errors)
                        response_data["errors"] = errors
                    
                    return Response(response_data, status=status.HTTP_201_CREATED)
                    
            except IntegrityError:
                logging.exception("IntegrityError while bulk registering IRs from Excel")
                return Response(
                    {"detail": "Database integrity error"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            except Exception as e:
                logging.exception("Error bulk registering IRs from Excel")
                return Response(
                    {"detail": f"Internal server error: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        except ImportError:
            return Response(
                {"detail": "pandas library not installed. Please install it: pip install pandas openpyxl"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            logging.exception("Error reading Excel file")
            return Response(
                {"detail": f"Error reading Excel file: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )


# ---------------------------------------------------
# IR LOGIN
# ---------------------------------------------------
class IRLogin(APIView):
    def post(self, request):
        ir_id = request.data.get("ir_id")
        password = request.data.get("ir_password")

        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR ID Not Found"}, status=404)

        if not ir.check_password(password):
            return Response({"detail": "Invalid credentials"}, status=401)

        return Response({
            "message": "Login Successful",
            "ir": {
                "ir_id": ir.ir_id,
                "ir_name": ir.ir_name,
                "ir_email": ir.ir_email,
                "ir_access_level": ir.ir_access_level,
            }
        })


# ---------------------------------------------------
# CREATE TEAM
# ---------------------------------------------------
class CreateTeam(APIView):
    def post(self, request):
        serializer = TeamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = serializer.save()

        return Response(
            {"message": "Team created", "team_id": team.id, "team_name": team.name},
            status=201,
        )


# ---------------------------------------------------
# ADD IR TO TEAM
# ---------------------------------------------------
class AddIrToTeam(APIView):
    def post(self, request):
        ir_id = request.data.get("ir_id")
        team_id = request.data.get("team_id")
        role = request.data.get("role")

        ir = get_object_or_404(Ir, ir_id=ir_id)
        team = get_object_or_404(Team, id=team_id)

        if TeamMember.objects.filter(ir=ir, team=team).exists():
            return Response(
                {"detail": "IR already assigned to team"},
                status=status.HTTP_409_CONFLICT,
            )

        TeamMember.objects.create(
            ir=ir,
            team=team,
            role=role,
        )

        return Response(
            {"message": f"{role} assigned to team {team.id}"},
            status=201,
        )


# ---------------------------------------------------
# ADD INFO DETAIL (WITH WEEKLY ROLLOVER)
# ---------------------------------------------------
class AddInfoDetail(APIView):
    def post(self, request, ir_id):
        with transaction.atomic():
            ir = Ir.objects.select_for_update().get(ir_id=ir_id)
            payload = request.data
            created_ids = []

            for item in payload:
                info = InfoDetail.objects.create(
                    ir=ir,
                    info_date=item.get("info_date", timezone.now()),
                    response=item["response"],
                    comments=item.get("comments"),
                    info_name=item["info_name"],
                )
                created_ids.append(info.id)

                # ✅ Atomic IR counter update
                Ir.objects.filter(ir_id=ir_id).update(
                    info_count=F("info_count") + 1
                )

                # Update teams
                links = (
                    TeamMember.objects
                    .select_related("team")
                    .select_for_update()
                    .filter(ir=ir)
                )

                week_start = get_current_week_start()

                for link in links:
                    team = link.team

                    # Archive week if needed
                    if not TeamWeek.objects.filter(
                        team=team,
                        week_start=week_start
                    ).exists():
                        TeamWeek.objects.create(
                            team=team,
                            week_start=week_start,
                            weekly_info_done=team.weekly_info_done,
                            weekly_plan_done=team.weekly_plan_done,
                        )
                        Team.objects.filter(id=team.id).update(
                            weekly_info_done=0,
                            weekly_plan_done=0,
                        )

                    # ✅ Atomic team counter update
                    Team.objects.filter(id=team.id).update(
                        weekly_info_done=F("weekly_info_done") + 1
                    )

        return Response(
            {"message": "Info details added", "info_ids": created_ids},
            status=201,
        )

# ---------------------------------------------------
# ADD PLAN DETAIL
# ---------------------------------------------------
class AddPlanDetail(APIView):
    def post(self, request, ir_id):
        with transaction.atomic():
            ir = Ir.objects.select_for_update().get(ir_id=ir_id)
            payload = request.data
            created_ids = []

            for item in payload:
                plan = PlanDetail.objects.create(
                    ir=ir,
                    plan_date=item.get("plan_date", timezone.now()),
                    plan_name=item.get("plan_name"),
                    comments=item.get("comments"),
                )
                created_ids.append(plan.id)

                # ✅ Atomic IR counter update
                Ir.objects.filter(ir_id=ir_id).update(
                    plan_count=F("plan_count") + 1
                )

                links = (
                    TeamMember.objects
                    .select_related("team")
                    .select_for_update()
                    .filter(ir=ir)
                )

                week_start = get_current_week_start()

                for link in links:
                    team = link.team

                    if not TeamWeek.objects.filter(
                        team=team,
                        week_start=week_start
                    ).exists():
                        TeamWeek.objects.create(
                            team=team,
                            week_start=week_start,
                            weekly_info_done=team.weekly_info_done,
                            weekly_plan_done=team.weekly_plan_done,
                        )
                        Team.objects.filter(id=team.id).update(
                            weekly_info_done=0,
                            weekly_plan_done=0,
                        )

                    # ✅ Atomic team counter update
                    Team.objects.filter(id=team.id).update(
                        weekly_plan_done=F("weekly_plan_done") + 1
                    )

        return Response(
            {"message": "Plan details added", "plan_ids": created_ids},
            status=201,
        )


# ---------------------------------------------------
# ADD UV (UV Counter Update)
# ---------------------------------------------------
class AddUV(APIView):
    def post(self, request, ir_id):
        try:
            with transaction.atomic():
                ir = Ir.objects.select_for_update().get(ir_id=ir_id)
                
                # Validate IR access level for UV operations
                # if ir.ir_access_level not in [2, 3]:
                #     return Response(
                #         {"detail": "IR access level must be 2 or 3 for UV operations"},
                #         status=status.HTTP_403_FORBIDDEN
                #     )
                
                payload = request.data
                if not isinstance(payload, list):
                    payload = [payload]
                
                total_uvs_added = 0
                
                for item in payload:
                    uv_count = item.get("uv_count", 1)  # Default to 1 UV if not specified
                    try:
                        uv_count = int(uv_count)
                        if uv_count <= 0:
                            return Response(
                                {"detail": "UV count must be a positive integer"},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                    except (ValueError, TypeError):
                        return Response(
                            {"detail": "Invalid UV count format"},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    
                    total_uvs_added += uv_count
                
                # ✅ Atomic IR UV counter update
                Ir.objects.filter(ir_id=ir_id).update(
                    uv_count=F("uv_count") + total_uvs_added
                )
                
                return Response(
                    {
                        "message": "UV count updated successfully",
                        "ir_id": ir_id,
                        "uvs_added": total_uvs_added,
                        "new_uv_count": ir.uv_count + total_uvs_added
                    },
                    status=status.HTTP_201_CREATED,
                )
                
        except Ir.DoesNotExist:
            return Response(
                {"detail": "IR not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception:
            logging.exception("Error adding UV count for ir_id=%s", ir_id)
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ---------------------------------------------------
# SET TARGETS (IR + TEAM)
# ---------------------------------------------------
class SetTargets(APIView):
    def _process(self, request):
        raw = request.data or {}

        # Support nested payloads: either {"payload": {...}, "acting_ir_id": "..."}
        # or flat payload with keys at top-level.
        payload = raw.get("payload") if isinstance(raw, dict) and isinstance(raw.get("payload"), dict) else raw

        acting_ir_id = raw.get("acting_ir_id") or (payload.get("acting_ir_id") if isinstance(payload, dict) else None)

        if not acting_ir_id:
            return Response({"detail": "acting_ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)

        if acting_ir.ir_access_level not in [1, 2, 3]:
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        # Get current week info (Saturday-Friday cycle)
        week_number, year, week_start, week_end = get_saturday_friday_week_info()
        
        updated = {
            "week_number": week_number,
            "year": year,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat()
        }

        # Use atomic transaction for consistency
        try:
            with transaction.atomic():
                # Update IR targets for current week
                if payload.get("ir_id"):
                    ir = get_object_or_404(Ir, ir_id=payload["ir_id"])
                    
                    # Get or create weekly target for this IR
                    weekly_target, created = WeeklyTarget.objects.get_or_create(
                        ir=ir,
                        week_number=week_number,
                        year=year,
                        defaults={
                            'week_start': week_start,
                            'week_end': week_end
                        }
                    )
                    
                    # Update weekly targets
                    if payload.get("weekly_info_target") is not None:
                        try:
                            weekly_target.ir_weekly_info_target = int(payload["weekly_info_target"])
                        except Exception:
                            weekly_target.ir_weekly_info_target = payload["weekly_info_target"]
                    
                    if payload.get("weekly_plan_target") is not None:
                        try:
                            weekly_target.ir_weekly_plan_target = int(payload["weekly_plan_target"])
                        except Exception:
                            weekly_target.ir_weekly_plan_target = payload["weekly_plan_target"]
                    
                    if payload.get("weekly_uv_target") is not None and ir.ir_access_level in [2, 3]:
                        try:
                            weekly_target.ir_weekly_uv_target = int(payload["weekly_uv_target"])
                        except Exception:
                            weekly_target.ir_weekly_uv_target = payload["weekly_uv_target"]
                    
                    weekly_target.save()
                    updated["ir_id"] = ir.ir_id

                # Update Team targets for current week
                if payload.get("team_id"):
                    # accept numeric or string team_id
                    team_id_raw = payload.get("team_id")
                    try:
                        team_id_val = int(team_id_raw)
                    except Exception:
                        team_id_val = team_id_raw

                    team = get_object_or_404(Team, id=team_id_val)
                    
                    # Get or create weekly target for this team
                    weekly_target, created = WeeklyTarget.objects.get_or_create(
                        team=team,
                        week_number=week_number,
                        year=year,
                        defaults={
                            'week_start': week_start,
                            'week_end': week_end
                        }
                    )
                    
                    # Update team weekly targets
                    if payload.get("team_weekly_info_target") is not None:
                        try:
                            weekly_target.team_weekly_info_target = int(payload["team_weekly_info_target"])
                        except Exception:
                            weekly_target.team_weekly_info_target = payload["team_weekly_info_target"]
                    
                    if payload.get("team_weekly_plan_target") is not None:
                        try:
                            weekly_target.team_weekly_plan_target = int(payload["team_weekly_plan_target"])
                        except Exception:
                            weekly_target.team_weekly_plan_target = payload["team_weekly_plan_target"]
                    
                    if payload.get("team_weekly_uv_target") is not None:
                        try:
                            weekly_target.team_weekly_uv_target = int(payload["team_weekly_uv_target"])
                        except Exception:
                            weekly_target.team_weekly_uv_target = payload["team_weekly_uv_target"]
                    
                    weekly_target.save()
                    updated["team_id"] = team.id

        except Exception as e:
            logging.exception("Error updating weekly targets")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"message": "Weekly targets updated", "updated": updated}, status=status.HTTP_200_OK)

    def post(self, request):
        return self._process(request)

    def put(self, request):
        return self._process(request)



# ---------------------------------------------------
# CHANGE IR ACCESS LEVEL (PROMOTE/DEMOTE)
# ---------------------------------------------------
class ChangeIRAccessLevel(APIView):
    def post(self, request):
        acting_ir_id = request.data.get("acting_ir_id")
        target_ir_id = request.data.get("target_ir_id")
        new_access_level = request.data.get("new_access_level")
        
        # Validate required fields
        if not acting_ir_id:
            return Response(
                {"detail": "acting_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not target_ir_id:
            return Response(
                {"detail": "target_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if new_access_level is None:
            return Response(
                {"detail": "new_access_level is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate new_access_level is an integer
        try:
            new_access_level = int(new_access_level)
        except (ValueError, TypeError):
            return Response(
                {"detail": "new_access_level must be a valid integer"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate access level range (typically 1-5)
        if new_access_level not in [1, 2, 3, 4, 5]:
            return Response(
                {"detail": "new_access_level must be between 1 and 5"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Get acting IR
            acting_ir = Ir.objects.get(ir_id=acting_ir_id)
        except Ir.DoesNotExist:
            return Response(
                {"detail": "Acting IR not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if acting IR has permission (only access level 1 or 2)
        if acting_ir.ir_access_level not in [1, 2]:
            return Response(
                {"detail": "Unauthorized. Only IRs with access level 1 or 2 can change access levels"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            # Get target IR
            target_ir = Ir.objects.get(ir_id=target_ir_id)
        except Ir.DoesNotExist:
            return Response(
                {"detail": "Target IR not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Prevent self-modification
        if acting_ir_id == target_ir_id:
            return Response(
                {"detail": "Cannot modify your own access level"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Store old access level for response
        old_access_level = target_ir.ir_access_level
        
        # Prevent access level 2 from promoting to access level 1
        if acting_ir.ir_access_level == 2 and new_access_level == 1:
            return Response(
                {"detail": "Access level 2 users cannot promote to access level 1"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Prevent access level 2 from modifying access level 1 users
        if acting_ir.ir_access_level == 2 and target_ir.ir_access_level == 1:
            return Response(
                {"detail": "Access level 2 users cannot modify access level 1 users"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            # Update access level
            target_ir.ir_access_level = new_access_level
            target_ir.save()
            
            action = "promoted" if new_access_level < old_access_level else "demoted" if new_access_level > old_access_level else "updated"
            
            return Response({
                "message": f"IR access level {action} successfully",
                "target_ir_id": target_ir.ir_id,
                "target_ir_name": target_ir.ir_name,
                "old_access_level": old_access_level,
                "new_access_level": new_access_level,
                "changed_by": acting_ir.ir_id
            }, status=status.HTTP_200_OK)
            
        except Exception:
            logging.exception("Error changing access level for ir_id=%s", target_ir_id)
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# ---------------------------------------------------
# PASSWORD RESET
# ---------------------------------------------------
class PasswordReset(APIView):
    def post(self, request):
        ir_id = request.data.get("ir_id")
        new_password = request.data.get("new_password")
        
        if not ir_id:
            return Response(
                {"detail": "ir_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not new_password:
            return Response(
                {"detail": "new_password is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if len(new_password) < 6:
            return Response(
                {"detail": "Password must be at least 6 characters long"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response(
                {"detail": "IR ID not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            # Update password
            ir.set_password(new_password)
            ir.save()
            
            return Response({
                "message": "Password reset successfully",
                "ir_id": ir.ir_id
            }, status=status.HTTP_200_OK)
            
        except Exception:
            logging.exception("Error resetting password for ir_id=%s", ir_id)
            return Response(
                {"detail": "Internal server error"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ---------------------------------------------------
# RESET DATABASE
# ---------------------------------------------------
class ResetDatabase(APIView):
    def post(self, request):
        TeamMember.objects.all().delete()
        InfoDetail.objects.all().delete()
        PlanDetail.objects.all().delete()
        TeamWeek.objects.all().delete()
        Team.objects.all().delete()
        Ir.objects.all().delete()
        IrId.objects.all().delete()

        return Response({"status": "success", "message": "Database reset"})
