import json
import time
import traceback

from vk_api import VkApi
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll, VkBotEvent

from enums import Platform
from .events import IncomingCallback, IncomingMessage
from .types import Document, MessageRef
from .vk import VkMessenger


# Пауза перед перезапуском опроса после ошибки (как в telebot.infinity_polling)
POLLING_RETRY_DELAY = 5


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
    def __attachment_from(message, types: list[str] = ["doc", "photo"]):
        for attachment in message.get("attachments", []):
            if attachment.get("type") in types:
                file = attachment[attachment["type"]]
                name = file.get("title") or ""
                ext = file.get("ext") or attachment["type"]
                if ext and name and not name.lower().endswith("." + ext.lower()):
                    name = f"{name}.{ext}"
                return Document(file.get("url", ""), name, ext)
        return None

    def to_message_event(self, event: VkBotEvent) -> IncomingMessage:
        message = event.object.message if getattr(event.object, "message", None) is not None else event.object
        peer_id = int(message["peer_id"])
        sender_id = int(message["from_id"])
        message_id = int(message.get("conversation_message_id") or message["id"])

        reply = message.get("reply_message")
        document = self.__attachment_from(message, ["doc"])
        if document is None and reply:
            document = self.__attachment_from(reply, ["doc"])

        reply_to = None
        if reply:
            reply_to = MessageRef(
                peer_id,
                int(reply.get("conversation_message_id") or reply["id"]),
                Platform.VK,
                reply.get("text") or None,
                self.__attachment_from(reply),
            )

        return IncomingMessage(
            Platform.VK,
            peer_id,
            sender_id,
            self.__handle_for(sender_id),
            message_id,
            text=message.get("text") or None,
            attachment=self.__attachment_from(message),
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

    def __dispatch(self, event: VkBotEvent):
        try:
            if event.type == VkBotEventType.MESSAGE_NEW:
                self.on_event(self.to_message_event(event))
            elif event.type == VkBotEventType.MESSAGE_EVENT:
                callback = self.to_callback_event(event)
                print(f"VK: message_event {callback.callback_id} data={callback.data!r}")
                self.on_event(callback)
        except Exception:
            # TODO: логировать
            traceback.print_exc()

    def start(self):
        # Аналог telebot.infinity_polling: любая ошибка long poll (сеть, VK API,
        # разбор ответа) не должна останавливать чтение обновлений навсегда.
        while True:
            try:
                if self.longpoll is None:
                    self.longpoll = VkBotLongPoll(self.vk, self.group_id)
                for event in self.longpoll.listen():
                    self.__dispatch(event)
            except Exception:
                traceback.print_exc()
                print(f"VK: polling упал, перезапускаю через {POLLING_RETRY_DELAY} с")
                time.sleep(POLLING_RETRY_DELAY)
