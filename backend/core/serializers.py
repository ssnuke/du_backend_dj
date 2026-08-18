from rest_framework import serializers
from .models import *

class IrIdSerializer(serializers.ModelSerializer):
    class Meta:
        model = IrId
        fields = "__all__"


class IrSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ir
        exclude = ["ir_password"]


class IrRegisterSerializer(serializers.ModelSerializer):
    parent_ir_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, write_only=True)
    
    class Meta:
        model = Ir
        fields = "__all__"
        extra_kwargs = {
            'parent_ir': {'required': False},
            'hierarchy_path': {'required': False},
            'hierarchy_level': {'required': False},
        }

    def create(self, validated_data):
        # Remove parent_ir_id from validated_data (handled separately)
        validated_data.pop('parent_ir_id', None)
        password = validated_data.pop("ir_password")
        ir = Ir(**validated_data)
        ir.set_password(password)
        ir.save()
        return ir


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = "__all__"
        extra_kwargs = {
            'created_by': {'required': False},
            'created_at': {'read_only': True},
        }


class TeamMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamMember
        fields = "__all__"


class InfoDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = InfoDetail
        fields = "__all__"


class PlanDetailSerializer(serializers.ModelSerializer):
    # presented_by (UL2) is only an ir_id on the model — the frontend needs
    # the display name too, e.g. to prefill the "Plan Taker / UL2" picker
    # when editing an existing plan instead of showing the raw id.
    presented_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PlanDetail
        fields = "__all__"

    def get_presented_by_name(self, obj):
        return obj.presented_by.ir_name if obj.presented_by else None


class UVDetailSerializer(serializers.ModelSerializer):
    ir_id = serializers.CharField(source='ir.ir_id', read_only=True)
    
    class Meta:
        model = UVDetail
        fields = ['id', 'ir', 'ir_id', 'ir_name', 'prospect_name', 'uv_date', 'uv_count', 'comments']


class PocketMemberSerializer(serializers.ModelSerializer):
    ir_id = serializers.CharField(source='ir.ir_id', read_only=True)
    ir_name = serializers.CharField(source='ir.ir_name', read_only=True)
    ir_email = serializers.CharField(source='ir.ir_email', read_only=True)
    added_by_name = serializers.CharField(source='added_by.ir_name', read_only=True, allow_null=True)
    
    class Meta:
        model = PocketMember
        fields = ['id', 'pocket', 'ir', 'ir_id', 'ir_name', 'ir_email', 'role', 'is_head', 'added_by', 'added_by_name', 'team', 'joined_at', 'updated_at']
        extra_kwargs = {
            'team': {'read_only': True},
            'joined_at': {'read_only': True},
            'updated_at': {'read_only': True},
        }


class PocketSerializer(serializers.ModelSerializer):
    members = PocketMemberSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source='created_by.ir_name', read_only=True, allow_null=True)
    member_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Pocket
        fields = ['id', 'team', 'name', 'created_by', 'created_by_name', 'is_active', 'members', 'member_count', 'created_at', 'updated_at']
        extra_kwargs = {
            'created_by': {'required': False},
            'created_at': {'read_only': True},
            'updated_at': {'read_only': True},
        }
    
    def get_member_count(self, obj):
        # len(obj.members.all()) reuses a prefetch_related cache when the view
        # supplies one (see GetPockets), instead of .count() always issuing a
        # fresh query per pocket; costs the same single query when not prefetched.
        return len(obj.members.all())


class PocketDetailedSerializer(serializers.ModelSerializer):
    """Includes nested team info and all member details"""
    team_name = serializers.CharField(source='team.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.ir_name', read_only=True, allow_null=True)
    members = PocketMemberSerializer(many=True, read_only=True)
    member_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Pocket
        fields = ['id', 'team', 'team_name', 'name', 'created_by', 'created_by_name', 'is_active', 
                  'members', 'member_count', 'created_at', 'updated_at']
        extra_kwargs = {
            'created_at': {'read_only': True},
            'updated_at': {'read_only': True},
        }
    
    def get_member_count(self, obj):
        # len(obj.members.all()) reuses a prefetch_related cache when the view
        # supplies one (see GetPockets), instead of .count() always issuing a
        # fresh query per pocket; costs the same single query when not prefetched.
        return len(obj.members.all())


class WeeklyTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklyTarget
        fields = "__all__"
        extra_kwargs = {
            'created_at': {'read_only': True},
            'updated_at': {'read_only': True},
        }


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'recipient', 'title', 'message', 'notification_type', 'is_read', 'related_object_id', 'metadata', 'created_at']
        read_only_fields = ['created_at']


class IrPendingRequestSerializer(serializers.ModelSerializer):
    requested_by_id = serializers.CharField(source='requested_by.ir_id', read_only=True)
    requested_by_name = serializers.CharField(source='requested_by.ir_name', read_only=True)
    reviewed_by_id = serializers.CharField(source='reviewed_by.ir_id', read_only=True, allow_null=True)
    reviewed_by_name = serializers.CharField(source='reviewed_by.ir_name', read_only=True, allow_null=True)
    ir_name = serializers.SerializerMethodField()
    ir_email = serializers.SerializerMethodField()
    ir_access_level = serializers.SerializerMethodField()

    class Meta:
        model = IrPendingRequest
        fields = [
            'id', 'action_type', 'request_status',
            'requested_by_id', 'requested_by_name',
            'target_ir_code', 'target_ir_name', 'referred_ir_id',
            'ir_name', 'ir_email', 'ir_access_level',
            'reviewed_by_id', 'reviewed_by_name', 'reviewed_at', 'rejection_reason',
            'created_at',
        ]

    def get_ir_name(self, obj):
        # For REGISTER requests, the requested name lives in payload (never
        # yet an Ir row); target_ir_name already covers DELETE requests.
        return (obj.payload or {}).get('ir_name') if obj.action_type == IrPendingRequest.ActionType.REGISTER else obj.target_ir_name

    def get_ir_email(self, obj):
        return (obj.payload or {}).get('ir_email') if obj.action_type == IrPendingRequest.ActionType.REGISTER else None

    def get_ir_access_level(self, obj):
        return (obj.payload or {}).get('ir_access_level') if obj.action_type == IrPendingRequest.ActionType.REGISTER else None
