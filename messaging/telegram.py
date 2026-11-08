import requests
from telebot import TeleBot
from telebot.types import (
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telebot.util import MAX_MESSAGE_LENGTH
from .base import Messenger
from .types import Document, Keyboard, Media, MessageRef, RemoveKeyboard
from enums import Platform
from utils import MultiError


class TelegramMessenger(Messenger):
    platform = Platform.TELEGRAM
    api_exceptions = ConnectionError

    def __init__(self, bot: TeleBot):
        self.bot = bot

    def ref_for(self, message: Message) -> MessageRef:
        return MessageRef(message.chat.id, message.message_id, self.platform)

    def __markup(self, keyboard: Keyboard | RemoveKeyboard | None):
        if keyboard is None:
            return None
        if isinstance(keyboard, RemoveKeyboard):
            return ReplyKeyboardRemove()
        if keyboard.inline:
            markup = InlineKeyboardMarkup()
            for row in keyboard.rows:
                markup.row(*[
                    InlineKeyboardButton(button.text, callback_data=button.callback_data)
                    for button in row
                ])
            return markup
        markup = ReplyKeyboardMarkup(resize_keyboard=keyboard.resize)
        for row in keyboard.rows:
            markup.row(*[KeyboardButton(button.text) for button in row])
        return markup

    @staticmethod
    def __input_file(media: Document | Media) -> InputFile | str:
        if isinstance(media, Document):
            return media.file_id
        if media.path is not None:
            return InputFile(media.path, media.filename)
        return InputFile(media.data, media.filename)

    @staticmethod
    def __split_msg(text: str) -> list[str]:
        paragraphs = text.split("\n\n")
        split_msg = [[]]
        last_total_len = 0
        for p in paragraphs:
            if last_total_len + 2 + len(p) <= MAX_MESSAGE_LENGTH:
                split_msg[-1].append("")
                split_msg[-1].append(p)
                last_total_len += 2 + len(p)
                continue
            if split_msg[-1]:
                split_msg.append([])
                last_total_len = 0
            if len(p) <= MAX_MESSAGE_LENGTH:
                split_msg[-1].append(p)
                last_total_len = len(p)
                continue
            for q in p.split("\n"):
                if last_total_len + 1 + len(q) > MAX_MESSAGE_LENGTH:
                    split_msg.append([])
                    last_total_len = 0
                if len(q) > MAX_MESSAGE_LENGTH:
                    raise ValueError(f"Слишком длинный абзац: {q[:-(MAX_MESSAGE_LENGTH+100)]}…")
                split_msg[-1].append(q)
                last_total_len += (1 if last_total_len else 0) + len(q)
            split_msg.append([])
        if not split_msg[-1]: split_msg.pop()
        split_msg = ["\n".join(paragraphs) for paragraphs in split_msg]
        return split_msg


    def send( # TODO: Дробить длинные сообщения
        self,
        peer_id: int,
        text: str = None,
        *,
        media: Document | Media | None = None,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        reply_to: MessageRef | None = None,
        show_caption_above_media: bool = False
    ) -> MessageRef:
        if text and len(text) > MAX_MESSAGE_LENGTH:
            text, *remainder = self.__split_msg(text)
        else:
            remainder = None
        is_img = (
            isinstance(media, Document) and media.mime_type == "photo"
            or isinstance(media, Media) and media.is_img
        )
        if (media is None or not is_img) and show_caption_above_media:
            ValueError("Нельзя устанавливать show_caption_above_media при отсутствии изображения")
        if is_img:
            message = self._retry_method(
                self.bot.send_photo,
                peer_id,
                photo=self.__input_file(media),
                caption=text,
                reply_markup=self.__markup(keyboard),
                show_caption_above_media=show_caption_above_media,
            )
        elif media:
            message = self._retry_method(
                self.bot.send_document,
                peer_id,
                document=self.__input_file(media),
                caption=text,
                reply_markup=self.__markup(keyboard),
            )
        else:
            message = self._retry_method(
                self.bot.send_message,
                peer_id,
                text,
                reply_markup=self.__markup(keyboard),
                reply_to_message_id=reply_to.message_id if reply_to else None,
            )
        if remainder:
            for follow_up_text in remainder:
                self.send(peer_id, follow_up_text)
        return self.ref_for(message)
    
    def send_many(
        self,
        peer_ids: list[int],
        text: str = None,
        *,
        media: Document | Media | None = None,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False
    ) -> None:
        error_list: list[Exception] = []
        ref_to_copy: MessageRef | None = None
        for peer_id in peer_ids:
            try:
                if ref_to_copy is not None:
                    ref_to_copy = self.copy_message(peer_id, ref_to_copy, keyboard=keyboard)
                else:
                    ref_to_copy = self.send(
                        peer_id, text, media=media, keyboard=keyboard, 
                        show_caption_above_media=show_caption_above_media
                    )
            except Exception as exc:
                error_list.append(exc)
        multierror = MultiError(error_list, "при рассылке сообщений")
        multierror.clean_raise()

    def edit_text(
        self,
        ref: MessageRef,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> None:
        self._retry_method(
            self.bot.edit_message_text,
            text,
            ref.peer_id,
            ref.message_id,
            reply_markup=self.__markup(keyboard),
        )

    def edit_keyboard(self, ref: MessageRef, keyboard: Keyboard | RemoveKeyboard | None) -> None:
        self._retry_method(
            self.bot.edit_message_reply_markup,
            ref.peer_id,
            ref.message_id,
            reply_markup=self.__markup(keyboard),
        )

    def delete(self, ref: MessageRef) -> None:
        self._retry_method(self.bot.delete_message, ref.peer_id, ref.message_id)

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False,
                        *, user_id: int | None = None, peer_id: int | None = None) -> None:
        self._retry_method(self.bot.answer_callback_query, callback_id, text=text, show_alert=show_alert)

    def copy_message(
        self,
        peer_id: int,
        ref: MessageRef,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> MessageRef:
        if self.platform != ref.platform and ref.text is None and ref.media is None:
            raise ValueError(
                f"Данные о содержании сообщения {ref} недоступны. "
                f"Пересылка из {ref.platform} в {self.platform} невозможна")
        if self.platform != ref.platform and isinstance(ref.media, Document):
            raise NotImplementedError(f"Пересылка документов из {ref.platform} в {self.platform} пока невозможна")
        elif self.platform != ref.platform:
            return self.send(peer_id, ref.text, media=ref.media, keyboard=keyboard)
        # copyMessage не переносит reply_markup исходного сообщения,
        # поэтому клавиатуру нужно передать явно.
        message = self._retry_method(
            self.bot.copy_message, peer_id, ref.peer_id, ref.message_id,
            reply_markup=self.__markup(keyboard),
        )
        return MessageRef(peer_id, message.message_id, self.platform)

    def copy_many(self, peer_ids: list[int], ref: MessageRef) -> None:
        if self.platform != ref.platform and ref.text is None and ref.media is None:
            raise ValueError(
                f"Данные о содержании сообщения {ref} недоступны. "
                f"Пересылка из {ref.platform} в {self.platform} невозможна")
        if self.platform != ref.platform and isinstance(ref.media, Document):
            raise NotImplementedError(f"Пересылка документов из {ref.platform} в {self.platform} пока невозможна")
        same_platform = self.platform == ref.platform
        error_list = []
        ref_to_copy: MessageRef | None = None
        for peer_id in peer_ids:
            try:
                if same_platform:
                    self._retry_method(self.bot.copy_message, peer_id, ref.peer_id, ref.message_id)
                elif ref_to_copy is not None:
                    self._retry_method(self.bot.copy_message, peer_id, ref_to_copy.peer_id, ref_to_copy.message_id)
                else:
                    ref_to_copy = self.send(peer_id, ref.text, media=ref.media)
            except Exception as exc:
                error_list.append(exc)
        multierror = MultiError(error_list, "при пересылке сообщения")
        multierror.clean_raise()

    def download_document(self, document: Document) -> bytes:
        file = self._retry_method(self.bot.get_file, document.file_id)
        url = f"https://api.telegram.org/file/bot{self.bot.token}/{file.file_path}"
        return requests.get(url).content
