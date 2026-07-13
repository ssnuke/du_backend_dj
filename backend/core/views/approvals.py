from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.db import transaction, IntegrityError
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from core.models import Ir, IrId, AccessLevel, IrPendingRequest
from core.utils.notifications import create_notifications
from core.serializers import IrPendingRequestSerializer

import logging


def _get_ir(ir_id):
    try:
        return Ir.objects.get(ir_id=ir_id)
    except Ir.DoesNotExist:
        return None


def _notify_admins(request_obj, title, message):
    admins = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN, status=True)
    create_notifications(
        recipients=admins,
        title=title,
        message=message,
        notification_type='IR_APPROVAL_REQUEST',
        related_object_id=str(request_obj.id),
        metadata={
            "request_id": request_obj.id,
            "action_type": request_obj.action_type,
            "ir_id": request_obj.target_ir_code,
            "ir_name": request_obj.target_ir_name,
            "referred_ir_id": request_obj.referred_ir_id,
            "requested_by_id": request_obj.requested_by.ir_id,
            "requested_by_name": request_obj.requested_by.ir_name,
        },
    )


# ---------------------------------------------------
# REQUEST: REGISTER NEW IR (creates a pending request, no Ir row yet)
# ---------------------------------------------------
class RequestRegisterIr(APIView):
    def post(self, request):
        data = request.data
        requester_ir_id = data.get("requester_ir_id")
        ir_id = data.get("ir_id")
        ir_name = data.get("ir_name")
        ir_email = data.get("ir_email")
        parent_ir_id = data.get("parent_ir_id") or None
        ir_access_level = data.get("ir_access_level", 6)
        ir_password = data.get("ir_password") or "secret"

        if not requester_ir_id:
            return Response({"detail": "requester_ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not ir_id or not ir_name or not ir_email:
            return Response({"detail": "ir_id, ir_name and ir_email are required"}, status=status.HTTP_400_BAD_REQUEST)

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)

        # Same gate as the (legacy) direct RegisterIR flow: LDC and above only
        if requester.ir_access_level > AccessLevel.LDC:
            return Response({"detail": "Not authorized. Only LDC and above can request IR registration"}, status=status.HTTP_403_FORBIDDEN)

        if Ir.objects.filter(ir_id=ir_id).exists():
            return Response({"detail": "IR ID already registered"}, status=status.HTTP_400_BAD_REQUEST)

        if IrPendingRequest.objects.filter(
            target_ir_code=ir_id, action_type=IrPendingRequest.ActionType.REGISTER, request_status=IrPendingRequest.Status.PENDING
        ).exists():
            return Response({"detail": "A registration request for this IR ID is already pending"}, status=status.HTTP_400_BAD_REQUEST)

        if parent_ir_id and not Ir.objects.filter(ir_id=parent_ir_id).exists():
            return Response({"detail": f"Referrer IR '{parent_ir_id}' not found"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # Whitelist the ID now (idempotent) so approval-time validation
                # matches the legacy RegisterIR flow's expectations.
                IrId.objects.get_or_create(ir_id=ir_id)

                pending = IrPendingRequest.objects.create(
                    action_type=IrPendingRequest.ActionType.REGISTER,
                    requested_by=requester,
                    target_ir_code=ir_id,
                    target_ir_name=ir_name,
                    referred_ir_id=parent_ir_id,
                    payload={
                        "ir_id": ir_id,
                        "ir_name": ir_name,
                        "ir_email": ir_email,
                        "ir_access_level": ir_access_level,
                        "parent_ir_id": parent_ir_id,
                        # Pre-hashed so no plaintext password is ever stored.
                        "ir_password_hash": make_password(ir_password),
                    },
                )
        except IntegrityError:
            logging.exception("RequestRegisterIr: IntegrityError creating pending request")
            return Response({"detail": "Database integrity error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        _notify_admins(
            pending,
            title="New IR registration awaiting approval",
            message=f"{requester.ir_name} ({requester.ir_id}) wants to register {ir_name} ({ir_id})"
                    + (f", referred by {parent_ir_id}" if parent_ir_id else ""),
        )

        return Response(IrPendingRequestSerializer(pending).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------
# REQUEST: DELETE IR (creates a pending request, no deletion yet)
# ---------------------------------------------------
class RequestDeleteIr(APIView):
    def post(self, request, ir_id):
        requester_ir_id = request.data.get("requester_ir_id")
        if not requester_ir_id:
            return Response({"detail": "requester_ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)

        target_ir = _get_ir(ir_id)
        if not target_ir:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        if requester.ir_access_level > AccessLevel.LDC:
            return Response({"detail": "Not authorized. Only LDC and above can request IR deletion"}, status=status.HTTP_403_FORBIDDEN)
        if requester.ir_id == target_ir.ir_id:
            return Response({"detail": "Cannot request deletion of your own account"}, status=status.HTTP_403_FORBIDDEN)
        if target_ir.ir_access_level <= requester.ir_access_level:
            return Response({"detail": "Cannot request deletion of an IR with the same or higher access level"}, status=status.HTTP_403_FORBIDDEN)
        if not requester.can_view_ir(target_ir):
            return Response({"detail": "Not authorized to request deletion of this IR"}, status=status.HTTP_403_FORBIDDEN)

        if IrPendingRequest.objects.filter(
            target_ir=target_ir, action_type=IrPendingRequest.ActionType.DELETE, request_status=IrPendingRequest.Status.PENDING
        ).exists():
            return Response({"detail": "A deletion request for this IR is already pending"}, status=status.HTTP_400_BAD_REQUEST)

        pending = IrPendingRequest.objects.create(
            action_type=IrPendingRequest.ActionType.DELETE,
            requested_by=requester,
            target_ir=target_ir,
            target_ir_code=target_ir.ir_id,
            target_ir_name=target_ir.ir_name,
        )

        _notify_admins(
            pending,
            title="IR deletion awaiting approval",
            message=f"{requester.ir_name} ({requester.ir_id}) wants to delete {target_ir.ir_name} ({target_ir.ir_id})",
        )

        return Response(IrPendingRequestSerializer(pending).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------
# LIST PENDING REQUESTS (ADMIN only)
# ---------------------------------------------------
class ListPendingIrRequests(APIView):
    def get(self, request):
        requester_ir_id = request.query_params.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)
        if requester.ir_access_level != AccessLevel.ADMIN:
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        requests_qs = IrPendingRequest.objects.filter(
            request_status=IrPendingRequest.Status.PENDING
        ).select_related('requested_by').order_by('-created_at')
        return Response(IrPendingRequestSerializer(requests_qs, many=True).data)


# ---------------------------------------------------
# APPROVE REQUEST (ADMIN only) — performs the real create/delete
# ---------------------------------------------------
class ApproveIrRequest(APIView):
    def post(self, request, request_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)
        if requester.ir_access_level != AccessLevel.ADMIN:
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        try:
            pending = IrPendingRequest.objects.select_related('requested_by', 'target_ir').get(id=request_id)
        except IrPendingRequest.DoesNotExist:
            return Response({"detail": "Request not found"}, status=status.HTTP_404_NOT_FOUND)

        if pending.request_status != IrPendingRequest.Status.PENDING:
            return Response({"detail": f"Request already {pending.request_status.lower()}"}, status=status.HTTP_400_BAD_REQUEST)

        if pending.action_type == IrPendingRequest.ActionType.REGISTER:
            payload = pending.payload or {}
            ir_id = payload.get("ir_id")

            if Ir.objects.filter(ir_id=ir_id).exists():
                return Response({"detail": "IR ID was registered elsewhere in the meantime"}, status=status.HTTP_400_BAD_REQUEST)
            if not IrId.objects.filter(ir_id=ir_id).exists():
                return Response({"detail": "IR ID not found in whitelist"}, status=status.HTTP_400_BAD_REQUEST)

            parent_ir_id = payload.get("parent_ir_id")
            parent_ir = Ir.objects.filter(ir_id=parent_ir_id).first() if parent_ir_id else None

            with transaction.atomic():
                ir = Ir(
                    ir_id=ir_id,
                    ir_name=payload.get("ir_name"),
                    ir_email=payload.get("ir_email"),
                    ir_access_level=payload.get("ir_access_level", 6),
                    parent_ir=parent_ir,
                )
                # Already hashed at request time — assign directly, don't re-hash.
                ir.ir_password = payload.get("ir_password_hash")
                ir.save()  # NEW_IR notification fires via the existing post_save signal

                pending.request_status = IrPendingRequest.Status.APPROVED
                pending.reviewed_by = requester
                pending.reviewed_at = timezone.now()
                pending.save(update_fields=["request_status", "reviewed_by", "reviewed_at"])

        else:  # DELETE
            target_ir = pending.target_ir
            if not target_ir:
                return Response({"detail": "Target IR no longer exists"}, status=status.HTTP_400_BAD_REQUEST)

            with transaction.atomic():
                pending.request_status = IrPendingRequest.Status.APPROVED
                pending.reviewed_by = requester
                pending.reviewed_at = timezone.now()
                # Save review fields before deleting — target_ir is SET_NULL,
                # not CASCADE, so this row survives the delete below either way.
                pending.save(update_fields=["request_status", "reviewed_by", "reviewed_at"])

                target_ir.delete()  # IR_DELETED notification fires via the existing post_delete signal

        return Response(IrPendingRequestSerializer(pending).data)


# ---------------------------------------------------
# REJECT REQUEST (ADMIN only)
# ---------------------------------------------------
class RejectIrRequest(APIView):
    def post(self, request, request_id):
        requester_ir_id = request.data.get("requester_ir_id")
        reason = (request.data.get("reason") or "").strip() or None

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)
        if requester.ir_access_level != AccessLevel.ADMIN:
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        try:
            pending = IrPendingRequest.objects.select_related('requested_by').get(id=request_id)
        except IrPendingRequest.DoesNotExist:
            return Response({"detail": "Request not found"}, status=status.HTTP_404_NOT_FOUND)

        if pending.request_status != IrPendingRequest.Status.PENDING:
            return Response({"detail": f"Request already {pending.request_status.lower()}"}, status=status.HTTP_400_BAD_REQUEST)

        pending.request_status = IrPendingRequest.Status.REJECTED
        pending.reviewed_by = requester
        pending.reviewed_at = timezone.now()
        pending.rejection_reason = reason
        pending.save(update_fields=["request_status", "reviewed_by", "reviewed_at", "rejection_reason"])

        action_label = "registration" if pending.action_type == IrPendingRequest.ActionType.REGISTER else "deletion"
        create_notifications(
            recipients=[pending.requested_by],
            title=f"IR {action_label} request rejected",
            message=f"Your request for {pending.target_ir_name} ({pending.target_ir_code}) was rejected"
                    + (f": {reason}" if reason else "."),
            notification_type='IR_REQUEST_REJECTED',
            related_object_id=str(pending.id),
            metadata={"request_id": pending.id, "action_type": pending.action_type},
        )

        return Response(IrPendingRequestSerializer(pending).data)
