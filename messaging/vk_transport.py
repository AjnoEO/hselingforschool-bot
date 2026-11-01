import json

from vk_api import VkApi
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll, VkBotEvent

from enums import Platform
from .events import IncomingCallback, IncomingMessage
from .types import Document, MessageRef
from .vk import VkMessenger


class VkTransport:
    """Приём обновлений VK Bots Long Poll и преобразование их в нейтральные события"""

    platform = Platform.VK

    def __init__(self, token: str, group_id: int, on_event):
        self.on_event = on_event
        self.vk = VkApi(token=token)
        self.api = self.vk.get_api()
        self.group_id = group_id
        self.longpoll = None
        self.messenger = VkMessenger(self.vk)
        self.__handles: dict[int, str] = {}

    def __handle_for(self, user_id: int) -> str:
        if user_id not in self.__handles:
            # TODO: обрабатывать недоступность users.get и кэшировать надёжнее
            try:
                user = self.api.users.get(user_ids=user_id, fields="screen_name")[0]
                self.__handles[user_id] = user.get("screen_name") or user.get("domain") or str(user_id)
            except Exception:
                self.__handles[user_id] = str(user_id)
        return self.__handles[user_id]

    @staticmethod
    def __document_from(message):
        for attachment in message.get("attachments", []):
            if attachment.get("type") == "doc":
                doc = attachment["doc"]
                name = doc.get("title") or ""
                ext = doc.get("ext")
                if ext and not name.lower().endswith("." + ext.lower()):
                    name = f"{name}.{ext}"
                return Document(doc.get("url", ""), name, ext)
        return None

    def to_message_event(self, event: VkBotEvent) -> IncomingMessage:
        message = event.object.message if getattr(event.object, "message", None) is not None else event.object
        peer_id = int(message["peer_id"])
        sender_id = int(message["from_id"])
        message_id = int(message.get("conversation_message_id") or message["id"])

        reply = message.get("reply_message")
        document = self.__document_from(message)
        if document is None and reply:
            document = self.__document_from(reply)

        reply_to = None
        if reply:
            reply_to = MessageRef(
                peer_id,
                int(reply.get("conversation_message_id") or reply["id"]),
                Platform.VK,
            )

        return IncomingMessage(
            Platform.VK,
            peer_id,
            sender_id,
            self.__handle_for(sender_id),
            message_id,
            text=message.get("text") or None,
            content_type="document" if document is not None else "text",
            document=document,
            reply_to=reply_to,
        )

    def to_callback_event(self, event: VkBotEvent) -> IncomingCallback:
        obj = event.object
        payload = obj.get("payload") or ""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"data": payload}
        data = payload.get("data", "") if isinstance(payload, dict) else str(payload)
        peer_id = int(obj["peer_id"])
        message_id = int(obj.get("conversation_message_id") or 0)
        return IncomingCallback(
            Platform.VK,
            str(obj["event_id"]),
            int(obj["user_id"]),
            self.__handle_for(int(obj["user_id"])),
            data,
            MessageRef(peer_id, message_id, Platform.VK),
        )

    def start(self):
        self.longpoll = VkBotLongPoll(self.vk, self.group_id)
        for event in self.longpoll.listen():
            try:
                if event.type == VkBotEventType.MESSAGE_NEW:
                    self.on_event(self.to_message_event(event))
                elif event.type == VkBotEventType.MESSAGE_EVENT:
                    callback = self.to_callback_event(event)
                    print(f"VK: message_event {callback.callback_id} data={callback.data!r}")
                    self.on_event(callback)
            except Exception:
                # TODO: логировать и не терять polling при ошибке обработчика
                import traceback
                traceback.print_exc()
