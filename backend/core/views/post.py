from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth.hashers import make_password
from core.utils.dates import get_current_week_start, get_saturday_friday_week_info, get_week_info_friday_to_friday, get_week_info_monday_to_sunday
from django.db.models import F

from core.models import (
    IrId,
    Ir,
    Team,
    TeamMember,
    InfoDetail,
    InfoType,
    PlanDetail,
    UVDetail,
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

        # Pre-check whitelist, existing registrations, and parent validation
        errors = []
        for idx, item in enumerate(items):
            ir_id = item.get("ir_id")
            parent_ir_id = item.get("parent_ir_id")
            
            if not ir_id:
                errors.append({"index": idx, "error": "ir_id missing"})
                continue
            if not IrId.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "IR ID Not Found in whitelist"})
            if Ir.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "Already registered"})
            # Validate parent exists if provided
            if parent_ir_id and not Ir.objects.filter(ir_id=parent_ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": f"Parent IR '{parent_ir_id}' not found"})

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                created_irs = []
                for item in items:
                    parent_ir_id = item.get("parent_ir_id")
                    parent_ir = Ir.objects.get(ir_id=parent_ir_id) if parent_ir_id else None
                    
                    ir = Ir(
                        ir_id=item["ir_id"],
                        ir_name=item["ir_name"],
                        ir_email=item["ir_email"],
                        ir_access_level=item.get("ir_access_level", 5),
                        parent_ir=parent_ir,
                    )
                    ir.set_password(item.get("ir_password", "secret"))
                    ir.save()  # save() auto-calculates hierarchy_path and level
                    created_irs.append({
                        "ir_id": ir.ir_id,
                        "hierarchy_level": ir.hierarchy_level,
                        "parent_ir_id": parent_ir_id
                    })
                    
        except IntegrityError:
            logging.exception("IntegrityError while bulk registering IRs")
            return Response({"detail": "Database integrity error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception:
            logging.exception("Unexpected error while bulk registering IRs")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {"message": "IR(s) registered successfully", "ir_ids": created_irs},
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
            
            # Check if parent_ir_id column exists (optional)
            has_parent_column = 'parent_ir_id' in df.columns
            
            # Remove rows with any missing values in required columns
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
            pending_irs = {}  # Store IRs that will be registered in this batch (for parent lookup)
            
            # First pass: Parse all rows and collect valid entries
            for idx, row in df.iterrows():
                ir_id = str(row['ir_id']).strip()
                ir_name = str(row['ir_name']).strip()
                ir_email = str(row['ir_email']).strip()
                parent_ir_id = None
                
                # Get parent_ir_id if column exists and value is not NaN
                if has_parent_column and pd.notna(row.get('parent_ir_id')):
                    parent_ir_id = str(row['parent_ir_id']).strip()
                    if parent_ir_id == '':
                        parent_ir_id = None
                
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
                
                # Store in pending_irs for this batch
                pending_irs[ir_id] = {
                    'ir_id': ir_id,
                    'ir_name': ir_name,
                    'ir_email': ir_email,
                    'ir_access_level': ir_access_level,
                    'parent_ir_id': parent_ir_id,
                    'ir_password': 'secret',  # Default password
                    'row': idx + 2
                }
            
            # Second pass: Validate parent references (check DB and pending batch)
            for ir_id, ir_data in list(pending_irs.items()):
                parent_ir_id = ir_data['parent_ir_id']
                if parent_ir_id:
                    # Check if parent exists in DB OR will be registered in this batch
                    parent_in_db = Ir.objects.filter(ir_id=parent_ir_id).exists()
                    parent_in_batch = parent_ir_id in pending_irs
                    
                    if not parent_in_db and not parent_in_batch:
                        errors.append({
                            "row": ir_data['row'],
                            "ir_id": ir_id,
                            "error": f"Parent IR '{parent_ir_id}' not found"
                        })
                        del pending_irs[ir_id]
            
            # Third pass: Sort IRs by hierarchy (parents before children)
            # Build dependency graph and sort topologically
            def get_hierarchy_order(pending_irs):
                """Sort IRs so parents are registered before children"""
                sorted_irs = []
                remaining = dict(pending_irs)
                registered_in_batch = set()
                existing_irs = set(Ir.objects.values_list('ir_id', flat=True))
                
                max_iterations = len(remaining) + 1
                iteration = 0
                
                while remaining and iteration < max_iterations:
                    iteration += 1
                    progress_made = False
                    
                    for ir_id, ir_data in list(remaining.items()):
                        parent_ir_id = ir_data['parent_ir_id']
                        
                        # Can register if: no parent, parent exists in DB, or parent already in sorted list
                        can_register = (
                            parent_ir_id is None or 
                            parent_ir_id in existing_irs or 
                            parent_ir_id in registered_in_batch
                        )
                        
                        if can_register:
                            sorted_irs.append(ir_data)
                            registered_in_batch.add(ir_id)
                            del remaining[ir_id]
                            progress_made = True
                    
                    if not progress_made and remaining:
                        # Circular dependency or missing parent - add remaining with errors
                        for ir_id, ir_data in remaining.items():
                            errors.append({
                                "row": ir_data['row'],
                                "ir_id": ir_id,
                                "error": f"Cannot resolve parent hierarchy for '{ir_data['parent_ir_id']}'"
                            })
                        break
                
                return sorted_irs
            
            sorted_irs = get_hierarchy_order(pending_irs)
            
            # Prepare final lists
            for ir_data in sorted_irs:
                ir_ids_to_add.append(ir_data['ir_id'])
                irs_to_register.append({
                    'ir_id': ir_data['ir_id'],
                    'ir_name': ir_data['ir_name'],
                    'ir_email': ir_data['ir_email'],
                    'ir_access_level': ir_data['ir_access_level'],
                    'parent_ir_id': ir_data['parent_ir_id'],
                    'ir_password': ir_data['ir_password']
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
                    
                    # Pre-hash the default password once for efficiency
                    hashed_password = make_password('secret')
                    
                    # Create IRs one by one (needed for hierarchy calculation in save())
                    created_irs = []
                    for ir_data in irs_to_register:
                        parent_ir = None
                        if ir_data['parent_ir_id']:
                            parent_ir = Ir.objects.get(ir_id=ir_data['parent_ir_id'])
                        
                        ir = Ir(
                            ir_id=ir_data['ir_id'],
                            ir_name=ir_data['ir_name'],
                            ir_email=ir_data['ir_email'],
                            ir_access_level=ir_data['ir_access_level'],
                            ir_password=hashed_password,
                            parent_ir=parent_ir,
                        )
                        ir.save()  # save() auto-calculates hierarchy_path and level
                        created_irs.append({
                            "ir_id": ir.ir_id,
                            "hierarchy_level": ir.hierarchy_level,
                            "parent_ir_id": ir_data['parent_ir_id']
                        })
                    
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

        print("password:", password)

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
# CREATE TEAM (with role-based check)
# ---------------------------------------------------
class CreateTeam(APIView):
    def post(self, request):
        ir_id = request.data.get("ir_id")
        
        if not ir_id:
            return Response(
                {"detail": "ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate that the IR exists
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response(
                {"detail": "IR not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if IR can create teams (ADMIN, CTC, LDC only)
        if not ir.can_create_team():
            return Response(
                {"detail": "Not authorized to create teams. Only ADMIN, CTC, and LDC can create teams."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get team name from request
        team_name = request.data.get("name")
        if not team_name:
            return Response(
                {"detail": "Team name is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            with transaction.atomic():
                # Create the team with created_by set
                team = Team.objects.create(
                    name=team_name,
                    created_by=ir
                )
                
                # Automatically add the creating IR as LDC
                TeamMember.objects.create(
                    ir=ir,
                    team=team,
                    role=TeamRole.LDC
                )
                
                return Response({
                    "message": "Team created successfully",
                    "team_id": team.id,
                    "team_name": team.name,
                    "created_by": ir.ir_id,
                    "ldc_id": ir.ir_id,
                    "ldc_name": ir.ir_name
                }, status=status.HTTP_201_CREATED)
                
        except IntegrityError:
            logging.exception("IntegrityError while creating team")
            return Response(
                {"detail": "Database integrity error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception:
            logging.exception("Error creating team")
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ---------------------------------------------------
# ADD IR TO TEAM (with role-based check)
# ---------------------------------------------------
class AddIrToTeam(APIView):
    def post(self, request):
        requester_ir_id = request.data.get("requester_ir_id")
        ir_id = request.data.get("ir_id")
        ir_ids = request.data.get("ir_ids")  # Support bulk addition
        team_id = request.data.get("team_id")
        role = request.data.get("role")

        # Determine if bulk or single addition
        if ir_ids and isinstance(ir_ids, list):
            ir_id_list = ir_ids
        elif ir_id:
            ir_id_list = [ir_id]
        else:
            return Response(
                {"detail": "Either 'ir_id' or 'ir_ids' must be provided"},
                status=status.HTTP_400_BAD_REQUEST
            )

        team = get_object_or_404(Team, id=team_id)
        
        # Role-based permission check if requester provided
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                
                # Requester must be able to edit the team
                if not requester.can_edit_team(team):
                    return Response(
                        {"detail": "Not authorized to add members to this team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
                
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        added_count = 0
        skipped_count = 0
        errors = []

        for ir_id_item in ir_id_list:
            try:
                ir = Ir.objects.get(ir_id=ir_id_item)
                
                if TeamMember.objects.filter(ir=ir, team=team).exists():
                    skipped_count += 1
                    continue

                TeamMember.objects.create(
                    ir=ir,
                    team=team,
                    role=role,
                )
                added_count += 1
                
            except Ir.DoesNotExist:
                errors.append(f"IR {ir_id_item} not found")

        if len(ir_id_list) == 1:
            # Single addition response
            if added_count == 1:
                return Response(
                    {"message": f"{role} assigned to team {team.id}"},
                    status=201,
                )
            elif skipped_count == 1:
                return Response(
                    {"detail": "IR already assigned to team"},
                    status=status.HTTP_409_CONFLICT,
                )
            else:
                return Response(
                    {"detail": errors[0] if errors else "Failed to add member"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # Bulk addition response
            return Response(
                {
                    "message": f"Added {added_count} member(s) to team {team.id}",
                    "added_count": added_count,
                    "skipped_count": skipped_count,
                    "errors": errors,
                },
                status=201 if added_count > 0 else status.HTTP_400_BAD_REQUEST,
            )


# ---------------------------------------------------
# ADD INFO DETAIL (WITH WEEKLY ROLLOVER + role-based check)
# ---------------------------------------------------
class AddInfoDetail(APIView):
    def post(self, request, ir_id):
        requester_ir_id = request.data.get("requester_ir_id") if isinstance(request.data, dict) else None
        
        # Role-based permission check if requester provided
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                target_ir = Ir.objects.get(ir_id=ir_id)
                
                # Requester must be able to add data for this IR
                if not requester.can_add_data_for_ir(target_ir):
                    return Response(
                        {"detail": "Not authorized to add info details for this IR"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        with transaction.atomic():
            ir = Ir.objects.select_for_update().get(ir_id=ir_id)
            payload = request.data
            
            # Handle both list and dict payloads
            if isinstance(payload, dict):
                items = payload.get("items", [payload])
            else:
                items = payload
            
            created_ids = []

            for item in items:
                if not isinstance(item, dict):
                    continue
                    
                info = InfoDetail.objects.create(
                    ir=ir,
                    info_date=item.get("info_date", timezone.now()),
                    response=item["response"],
                    info_type=item.get("info_type", InfoType.FRESH),
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
# ADD PLAN DETAIL (with role-based check)
# ---------------------------------------------------
class AddPlanDetail(APIView):
    def post(self, request, ir_id):
        requester_ir_id = request.data.get("requester_ir_id") if isinstance(request.data, dict) else None
        
        # Role-based permission check if requester provided
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                target_ir = Ir.objects.get(ir_id=ir_id)
                
                # Requester must be able to add data for this IR
                if not requester.can_add_data_for_ir(target_ir):
                    return Response(
                        {"detail": "Not authorized to add plan details for this IR"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        with transaction.atomic():
            ir = Ir.objects.select_for_update().get(ir_id=ir_id)
            payload = request.data
            
            # Handle both list and dict payloads
            if isinstance(payload, dict):
                items = payload.get("items", [payload])
            else:
                items = payload
            
            created_ids = []

            for item in items:
                if not isinstance(item, dict):
                    continue
                    
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
# ADD UV (UV Counter Update + role-based check)
# ---------------------------------------------------
class AddUV(APIView):
    def post(self, request, ir_id):
        requester_ir_id = request.data.get("requester_ir_id") if isinstance(request.data, dict) else None
        
        # Role-based permission check if requester provided
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                target_ir = Ir.objects.get(ir_id=ir_id)
                
                # Requester must be able to add data for this IR
                if not requester.can_add_data_for_ir(target_ir):
                    return Response(
                        {"detail": "Not authorized to add UV for this IR"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
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
                uv_record_ids = []
                
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
                    
                    # Create UVDetail record for week-specific tracking with IR name and prospect name
                    uv_detail = UVDetail.objects.create(
                        ir=ir,
                        ir_name=ir.ir_name,  # Store IR name for display
                        prospect_name=item.get("prospect_name", ""),  # Store prospect name
                        uv_count=uv_count,
                        uv_date=item.get("uv_date", timezone.now()),
                        comments=item.get("comments")
                    )
                    uv_record_ids.append(uv_detail.id)
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
                        "uv_record_ids": uv_record_ids,
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

        # Only ADMIN, CTC, LDC can set targets
        if acting_ir.ir_access_level not in [1, 2, 3]:
            return Response({"detail": "Not authorized. Only ADMIN, CTC, and LDC can set targets"}, status=status.HTTP_403_FORBIDDEN)

        # Get week info - use specified week or current week
        week_param = payload.get("week") if isinstance(payload, dict) else None
        year_param = payload.get("year") if isinstance(payload, dict) else None
        
        if week_param is not None and year_param is not None:
            try:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday(
                    week_number=int(week_param),
                    year=int(year_param)
                )
            except Exception:
                return Response(
                    {"detail": "Invalid week or year parameter"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # Default to current week
            week_number, year, week_start, week_end = get_week_info_friday_to_friday()
        
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
                    
                    # Role-based check: acting IR must be able to add data for target IR
                    if not acting_ir.can_add_data_for_ir(ir):
                        return Response(
                            {"detail": "Not authorized to set targets for this IR"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                    
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

                # Update Team targets for current week using JSON structure
                if payload.get("team_id"):
                    # accept numeric or string team_id
                    team_id_raw = payload.get("team_id")
                    try:
                        team_id_val = int(team_id_raw)
                    except Exception:
                        team_id_val = team_id_raw

                    team = get_object_or_404(Team, id=team_id_val)
                    
                    # Role-based check: acting IR must be able to edit team
                    if not acting_ir.can_edit_team(team):
                        return Response(
                            {"detail": "Not authorized to set targets for this team"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                    
                    # Get or create TeamWeeklyTargets record for this team
                    from core.models import TeamWeeklyTargets
                    team_targets, created = TeamWeeklyTargets.objects.get_or_create(team=team)
                    
                    # Extract target values
                    info_target = payload.get("team_weekly_info_target", 0)
                    plan_target = payload.get("team_weekly_plan_target", 0)
                    uv_target = payload.get("team_weekly_uv_target", 0)
                    
                    # Try to set the week targets (will fail if week already exists)
                    success, message = team_targets.set_week_targets(
                        year=year,
                        week_number=week_number,
                        week_start=week_start,
                        week_end=week_end,
                        info_target=info_target,
                        plan_target=plan_target,
                        uv_target=uv_target,
                        allow_overwrite=False  # Prevent overwriting existing weeks
                    )
                    
                    if not success:
                        return Response(
                            {
                                "detail": f"Targets for week {week_number}, {year} already exist. Cannot overwrite existing week data.",
                                "week_number": week_number,
                                "year": year,
                                "message": message
                            },
                            status=status.HTTP_409_CONFLICT
                        )
                    
                    # Save the updated JSON data
                    team_targets.save()
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
# CHANGE IR ACCESS LEVEL (PROMOTE/DEMOTE) - role-based
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
        
        # Validate access level range (1-6)
        if new_access_level not in [1, 2, 3, 4, 5, 6]:
            return Response(
                {"detail": "new_access_level must be between 1 and 6"},
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
        
        # Only ADMIN (1) and CTC (2) can promote/demote
        if not acting_ir.can_promote_demote():
            return Response(
                {"detail": "Unauthorized. Only ADMIN and CTC can change access levels"},
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
        
        # CTC (2) cannot promote to ADMIN (1)
        if acting_ir.ir_access_level == 2 and new_access_level == 1:
            return Response(
                {"detail": "CTC cannot promote to ADMIN level"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # CTC (2) cannot modify ADMIN (1) users
        if acting_ir.ir_access_level == 2 and target_ir.ir_access_level == 1:
            return Response(
                {"detail": "CTC cannot modify ADMIN users"},
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
