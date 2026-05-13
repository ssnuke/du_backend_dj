from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from core.models import Ir, InfoDetail, PlanDetail, PipelineStats


PIPELINE_FIELDS = [
    'total_name_list',
    'numbers_have',
    'numbers_dont_have',
    'infos_done',
    'infos_not_done',
    'invites_gone',
    'invites_not_gone',
    'invite_yes',
    'invite_no',
    'showed_up',
    'didnt_show_up',
    'signed_up_dr',
    'kiv',
]


def _serialize(stats, target_ir):
    return {
        'ir_id': target_ir.ir_id,
        'ir_name': target_ir.ir_name,
        **{f: getattr(stats, f) for f in PIPELINE_FIELDS},
        'updated_at': stats.updated_at.isoformat() if stats.updated_at else None,
        'last_sync': stats.last_sync.isoformat() if stats.last_sync else None,
    }


def _get_irs(ir_id, requester_ir_id):
    try:
        target_ir = Ir.objects.get(ir_id=ir_id)
    except Ir.DoesNotExist:
        return None, None, Response({'message': 'IR not found'}, status=404)

    effective_requester_id = requester_ir_id or ir_id
    try:
        requester = Ir.objects.get(ir_id=effective_requester_id)
    except Ir.DoesNotExist:
        return None, None, Response({'message': 'Requester IR not found'}, status=404)

    return target_ir, requester, None


class GetPipelineStats(APIView):
    def get(self, request, ir_id):
        requester_ir_id = request.query_params.get('requester_ir_id')
        target_ir, requester, err = _get_irs(ir_id, requester_ir_id)
        if err:
            return err

        if not requester.can_view_name_list(target_ir):
            return Response({'message': 'You do not have permission to view this data'}, status=403)

        stats, _ = PipelineStats.objects.get_or_create(ir=target_ir)
        return Response(_serialize(stats, target_ir))


class UpdatePipelineStats(APIView):
    def put(self, request, ir_id):
        requester_ir_id = request.data.get('requester_ir_id')
        target_ir, requester, err = _get_irs(ir_id, requester_ir_id)
        if err:
            return err

        if not requester.can_add_data_for_ir(target_ir):
            return Response({'message': 'You do not have permission to update this data'}, status=403)

        stats, _ = PipelineStats.objects.get_or_create(ir=target_ir)

        for field in PIPELINE_FIELDS:
            if field in request.data:
                val = request.data[field]
                try:
                    setattr(stats, field, max(0, int(val)))
                except (TypeError, ValueError):
                    return Response({'message': f'Invalid value for {field}'}, status=400)

        stats.save()
        return Response({
            'message': 'Pipeline stats updated successfully',
            **_serialize(stats, target_ir),
        })


class AutoFetchPipelineStats(APIView):
    def post(self, request, ir_id):
        requester_ir_id = request.data.get('requester_ir_id')
        target_ir, requester, err = _get_irs(ir_id, requester_ir_id)
        if err:
            return err

        if not requester.can_add_data_for_ir(target_ir):
            return Response({'message': 'You do not have permission to sync this data'}, status=403)

        stats, _ = PipelineStats.objects.get_or_create(ir=target_ir)

        stats.total_name_list = target_ir.name_list
        stats.infos_done = InfoDetail.objects.filter(ir=target_ir).count()
        stats.showed_up = PlanDetail.objects.filter(ir=target_ir).count()
        stats.signed_up_dr = target_ir.dr_count
        stats.last_sync = timezone.now()
        stats.save()

        return Response({
            'message': 'Auto-fetch complete. Synced: Total Name List, Infos Done, Showed Up, Signed Up (DR).',
            'synced_fields': ['total_name_list', 'infos_done', 'showed_up', 'signed_up_dr'],
            **_serialize(stats, target_ir),
        })


class GetViewableIRsForPipeline(APIView):
    """Returns all IR IDs + names the requester can view pipeline stats for."""
    def get(self, request, ir_id):
        try:
            requester = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({'message': 'IR not found'}, status=404)

        viewable = requester.get_viewable_irs_for_name_list().values('ir_id', 'ir_name').order_by('ir_name')
        return Response(list(viewable))
