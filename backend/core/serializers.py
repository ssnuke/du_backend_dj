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
    class Meta:
        model = PlanDetail
        fields = "__all__"


class UVDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = UVDetail
        fields = "__all__"
