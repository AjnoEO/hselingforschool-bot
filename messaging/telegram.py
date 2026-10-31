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
from .base import Messenger
from .types import Document, Keyboard, Media, MessageRef, RemoveKeyboard


class TelegramMessenger(Messenger):
    def __init__(self, bot: TeleBot):
        self.bot = bot

    def ref_for(self, message: Message) -> MessageRef:
        return MessageRef(message.chat.id, message.message_id)

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
    def __input_file(media: Media) -> InputFile:
        if media.path is not None:
            return InputFile(media.path, media.filename)
        return InputFile(media.data, media.filename)

    def send_text(
        self,
        peer_id: int,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        reply_to: MessageRef | None = None,
    ) -> MessageRef:
        message = self.bot.send_message(
            peer_id,
            text,
            reply_markup=self.__markup(keyboard),
            reply_to_message_id=reply_to.message_id if reply_to else None,
        )
        return self.ref_for(message)

    def send_photo(
        self,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False,
    ) -> MessageRef:
        message = self.bot.send_photo(
            peer_id,
            photo=self.__input_file(media),
            caption=caption,
            reply_markup=self.__markup(keyboard),
            show_caption_above_media=show_caption_above_media,
        )
        return self.ref_for(message)

    def send_document(
        self,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> MessageRef:
        message = self.bot.send_document(
            peer_id,
            document=self.__input_file(media),
            caption=caption,
            reply_markup=self.__markup(keyboard),
        )
        return self.ref_for(message)

    def edit_text(
        self,
        ref: MessageRef,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> None:
        self.bot.edit_message_text(
            text,
            ref.peer_id,
            ref.message_id,
            reply_markup=self.__markup(keyboard),
        )

    def edit_keyboard(self, ref: MessageRef, keyboard: Keyboard | RemoveKeyboard | None) -> None:
        self.bot.edit_message_reply_markup(
            ref.peer_id,
            ref.message_id,
            reply_markup=self.__markup(keyboard),
        )

    def delete(self, ref: MessageRef) -> None:
        self.bot.delete_message(ref.peer_id, ref.message_id)

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False) -> None:
        self.bot.answer_callback_query(callback_id, text=text, show_alert=show_alert)

    def copy_message(self, peer_id: int, ref: MessageRef) -> MessageRef:
        message = self.bot.copy_message(peer_id, ref.peer_id, ref.message_id)
        return MessageRef(peer_id, message.message_id)

    def download_document(self, document: Document) -> bytes:
        file = self.bot.get_file(document.file_id)
        url = f"https://api.telegram.org/file/bot{self.bot.token}/{file.file_path}"
        return requests.get(url).content
