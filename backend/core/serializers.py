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
    class Meta:
        model = Ir
        fields = "__all__"

    def create(self, validated_data):
        password = validated_data.pop("ir_password")
        ir = Ir(**validated_data)
        ir.set_password(password)
        ir.save()
        return ir


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = "__all__"


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
