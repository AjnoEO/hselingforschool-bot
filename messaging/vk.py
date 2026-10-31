import json
import os
import secrets
import tempfile
from html.parser import HTMLParser

import requests
from vk_api import VkApi
from vk_api.keyboard import VkKeyboard
from vk_api.upload import VkUpload

from enums import Platform
from .base import Messenger
from .types import (
    Document, Keyboard, Media, MessageRef, RemoveKeyboard,
)

MAX_BUTTON_LABEL = 40
MAX_MESSAGE_LENGTH = 4096


def truncate_label(label: str) -> str:
    if len(label) <= MAX_BUTTON_LABEL:
        return label
    return label[:MAX_BUTTON_LABEL - 1] + "…"


class _TextExtractor(HTMLParser):
    """Telegram HTML -> обычный текст VK"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")
        elif tag == "a":
            self.links.append(dict(attrs).get("href", ""))
        elif tag in ("p", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(f" ({href})")
        elif tag in ("p", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_vk(text: str) -> str:
    parser = _TextExtractor()
    parser.feed(text)
    result = "".join(parser.parts)
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result.strip()


def vk_keyboard(keyboard: Keyboard | RemoveKeyboard | None) -> str | None:
    if keyboard is None:
        return None
    if isinstance(keyboard, RemoveKeyboard):
        # TODO: проверить, достаточно ли пустой клавиатуры для VK
        return VkKeyboard.get_empty_keyboard()

    result = VkKeyboard(inline=keyboard.inline)
    for index, row in enumerate(keyboard.rows):
        if index:
            result.add_line()
        for button in row:
            label = truncate_label(button.text)
            if keyboard.inline:
                # TODO: проверять длину payload (255 байт)
                result.add_callback_button(label, payload={"data": button.callback_data or button.text})
            else:
                result.add_button(label)
    return result.get_keyboard()


class VkMessenger(Messenger):
    platform = Platform.VK

    def __init__(self, vk: VkApi):
        self.vk = vk
        self.api = vk.get_api()
        self.upload = VkUpload(vk)
        self.__texts: dict[tuple[int, int], str] = {}

    def ref_for(self, message) -> MessageRef:
        return MessageRef(message["peer_id"], message["conversation_message_id"], Platform.VK)

    @staticmethod
    def __path(media: Media):
        if media.path:
            return media.path, None
        if media.data:
            file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(media.filename or "")[1])
            file.write(media.data)
            file.close()
            return file.name, file.name
        raise ValueError("У медиа нет ни пути, ни данных")

    def __send(self, peer_id: int, text: str, *, attachment: str | None = None,
               keyboard: Keyboard | RemoveKeyboard | None = None,
               reply_to: MessageRef | None = None) -> MessageRef:
        params = {
            "peer_id": peer_id,
            "message": html_to_vk(text),
            "random_id": secrets.randbelow(2 ** 31),
        }
        if attachment:
            params["attachment"] = attachment
        keyboard_json = vk_keyboard(keyboard)
        if keyboard_json is not None:
            params["keyboard"] = keyboard_json
        if reply_to is not None:
            # TODO: проверить, что VK ждёт именно conversation_message_id, а не глобальный id
            params["reply_to"] = reply_to.message_id
        # TODO: обрабатывать ошибки VK, лимиты длины (MAX_MESSAGE_LENGTH) и повторы
        message_id = self.api.messages.send(**params)
        self.__texts[(peer_id, message_id)] = text
        return MessageRef(peer_id, message_id, Platform.VK)

    def send_text(self, peer_id, text, *, keyboard=None, reply_to=None) -> MessageRef:
        return self.__send(peer_id, text, keyboard=keyboard, reply_to=reply_to)

    def send_photo(self, peer_id, media, *, caption="", keyboard=None,
                   show_caption_above_media=False) -> MessageRef:
        path, temporary = self.__path(media)
        try:
            uploaded = self.upload.photo_messages(path, peer_id=peer_id)[0]
        finally:
            if temporary:
                os.unlink(temporary)
        attachment = f"photo{uploaded['owner_id']}_{uploaded['id']}"
        return self.__send(peer_id, caption, attachment=attachment, keyboard=keyboard)

    def send_document(self, peer_id, media, *, caption="", keyboard=None) -> MessageRef:
        path, temporary = self.__path(media)
        try:
            uploaded = self.upload.document(path, title=media.filename, message_peer_id=peer_id)["doc"]
        finally:
            if temporary:
                os.unlink(temporary)
        attachment = f"doc{uploaded['owner_id']}_{uploaded['id']}"
        return self.__send(peer_id, caption, attachment=attachment, keyboard=keyboard)

    def edit_text(self, ref, text, *, keyboard=None) -> None:
        params = {
            "peer_id": ref.peer_id,
            "message_id": ref.message_id,
            "message": html_to_vk(text),
        }
        keyboard_json = vk_keyboard(keyboard)
        if keyboard_json is not None:
            params["keyboard"] = keyboard_json
        self.api.messages.edit(**params)
        self.__texts[(ref.peer_id, ref.message_id)] = text

    def edit_keyboard(self, ref, keyboard) -> None:
        # TODO: VK не умеет менять только клавиатуру; хранить текст надёжнее
        self.api.messages.edit(
            peer_id=ref.peer_id,
            message_id=ref.message_id,
            message=html_to_vk(self.__texts.get((ref.peer_id, ref.message_id), "")),
            keyboard=vk_keyboard(keyboard),
        )

    def delete(self, ref) -> None:
        self.api.messages.delete(
            peer_id=ref.peer_id, message_ids=ref.message_id, delete_for_all=1
        )

    def answer_callback(self, callback_id, text="", show_alert=False, *,
                        user_id=None, peer_id=None) -> None:
        if user_id is None or peer_id is None:
            # TODO: пробрасывать user_id/peer_id всегда
            raise ValueError("Для ответа на callback в VK нужны user_id и peer_id")
        self.api.messages.sendMessageEventAnswer(
            event_id=int(callback_id),
            user_id=user_id,
            peer_id=peer_id,
            event_data=json.dumps({"type": "show_snackbar", "text": text}, ensure_ascii=False),
        )

    def copy_message(self, peer_id, ref) -> MessageRef:
        # TODO: пересылка между мессенджерами
        if ref.platform != Platform.VK:
            raise NotImplementedError("Пересылка сообщений между мессенджерами пока не поддерживается")
        message_id = self.api.messages.send(
            peer_id=peer_id,
            random_id=secrets.randbelow(2 ** 31),
            forward_messages=ref.message_id,
        )
        return MessageRef(peer_id, message_id, Platform.VK)

    def download_document(self, document: Document) -> bytes:
        if document.file_id.startswith("http"):
            return requests.get(document.file_id, timeout=30).content
        # TODO: поддержать скачивание документов VK по owner_id/doc_id
        raise NotImplementedError("Не умею скачивать документ VK")
