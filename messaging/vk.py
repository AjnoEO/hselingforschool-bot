import json
import os
import secrets
import tempfile
from html.parser import HTMLParser
from utils import MultiError

import requests
from vk_api import VkApi
from vk_api.keyboard import VkKeyboard
from vk_api.upload import VkUpload
from vk_api.exceptions import VkApiError

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

    def __send(self, peer_id: int | list[int], text: str | None = None, *,
               media: Document | Media | None = None,
               keyboard: Keyboard | RemoveKeyboard | None = None,
               reply_to: MessageRef | None = None,
               forward_message: MessageRef | None = None) -> MessageRef:
        params = {
            "peer_ids" if isinstance(peer_id, list) else "peer_id": peer_id,
            "random_id": secrets.randbelow(2 ** 31),
        }
        if text:
            params["message"] = html_to_vk(text)
        if media:
            path, temporary = self.__path(media)
            try:
                if media.is_img:
                    attachment_type = "photo"
                    uploaded = self.upload.photo_messages(path, peer_id)[0]
                else:
                    attachment_type = "doc"
                    uploaded = self.upload.document_message(path, title=media.filename, peer_id=peer_id)["doc"]
            finally:
                if temporary:
                    os.unlink(temporary)
            attachment = f"{attachment_type}{uploaded['owner_id']}_{uploaded['id']}"
            params["attachment"] = attachment
        keyboard_json = vk_keyboard(keyboard)
        if keyboard_json is not None:
            params["keyboard"] = keyboard_json
        if reply_to is not None:
            # TODO: проверить, что VK ждёт именно conversation_message_id, а не глобальный id
            params["reply_to"] = reply_to.message_id
        if forward_message is not None:
            params["forward_messages"]=forward_message.message_id
        # TODO: обрабатывать ошибки VK, лимиты длины (MAX_MESSAGE_LENGTH) и повторы
        api_response = self.api.messages.send(**params)
        if isinstance(peer_id, list):
            error_list: list[VkApiError] = []
            for message_obj in api_response:
                if "error" in message_obj:
                    error_list.append(VkApiError(message_obj["error"]))
                    continue
                self.__texts[(message_obj["peer_id"], message_obj["message_id"])] = text
            multierror = MultiError(error_list, "при рассылке сообщений")
            multierror.clean_raise()
            return
        self.__texts[(peer_id, api_response)] = text
        return MessageRef(peer_id, api_response, Platform.VK)

    def send(
        self,
        peer_id: int,
        text: str = None,
        *,
        media: Document | Media | None = None,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        reply_to: MessageRef | None = None,
        show_caption_above_media: bool = False
    ) -> MessageRef:
        return self.__send(peer_id, text, media=media, keyboard=keyboard, reply_to=reply_to)
    
    def send_many(
        self,
        peer_ids: list[int],
        text: str = None,
        *,
        media: Document | Media | None = None,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False
    ) -> None:
        self.__send(peer_ids, text, media=media, keyboard=keyboard)

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
        try:
            # VK requires cmids together with peer_id for conversation messages.
            self.api.messages.delete(
                peer_id=ref.peer_id, cmids=[ref.message_id], delete_for_all=1
            )
        except Exception as error:
            # TODO: разобраться с удалением сообщений в VK
            if "can not be found" not in str(error):
                print(f"! Не удалось удалить сообщение VK: {error}")

    def answer_callback(self, callback_id, text="", show_alert=False, *,
                        user_id=None, peer_id=None) -> None:
        if user_id is None or peer_id is None:
            # TODO: пробрасывать user_id/peer_id всегда
            raise ValueError("Для ответа на callback в VK нужны user_id и peer_id")
        self.api.messages.sendMessageEventAnswer(
            event_id=callback_id,
            user_id=user_id,
            peer_id=peer_id,
            event_data=json.dumps(
                {"type": "show_snackbar", "text": text or "Готово"},
                ensure_ascii=False,
            ),
        )

    def copy_message(self, peer_id: int, ref: MessageRef) -> MessageRef:
        if self.platform != ref.platform and ref.text is None and ref.media is None:
            raise ValueError(
                f"Данные о содержании сообщения {ref} недоступны. "
                f"Пересылка из {ref.platform} в {self.platform} невозможна")
        if self.platform != ref.platform and isinstance(ref.media, Document):
            raise NotImplementedError(f"Пересылка документов из {ref.platform} в {self.platform} пока невозможна")
        if self.platform != ref.platform:
            return self.send(peer_id, ref.text, media=ref.media)
        return self.__send(peer_id=peer_id, forward_message=ref)
    
    def copy_many(self, peer_ids: list[int], ref: MessageRef) -> None:
        if self.platform != ref.platform and ref.text is None and ref.media is None:
            raise ValueError(
                f"Данные о содержании сообщения {ref} недоступны. "
                f"Пересылка из {ref.platform} в {self.platform} невозможна")
        if self.platform != ref.platform and isinstance(ref.media, Document):
            raise NotImplementedError(f"Пересылка документов из {ref.platform} в {self.platform} пока невозможна")
        if self.platform != ref.platform:
            return self.send_many(peer_ids, ref.text, media=ref.media)
        return self.__send(peer_id=peer_ids, forward_message=ref)

    def download_document(self, document: Document) -> bytes:
        if document.file_id.startswith("http"):
            return requests.get(document.file_id, timeout=30).content
        # TODO: поддержать скачивание документов VK по owner_id/doc_id
        raise NotImplementedError("Не умею скачивать документ VK")
