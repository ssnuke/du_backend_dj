from django.urls import re_path

from core.chat.consumers import ChatConsumer, UserInboxConsumer

websocket_urlpatterns = [
    re_path(r"^ws/chat/(?P<room_id>\d+)/$", ChatConsumer.as_asgi()),
    re_path(r"^ws/inbox/$", UserInboxConsumer.as_asgi()),
]
