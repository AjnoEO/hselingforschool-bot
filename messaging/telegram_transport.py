import telebot
from telebot.types import CallbackQuery, Message

from enums import Platform
from .events import IncomingCallback, IncomingMessage
from .telegram import TelegramMessenger
from .types import Document, MessageRef


class TelegramTransport:
    """Приём обновлений Telegram и преобразование их в нейтральные события"""

    platform = Platform.TELEGRAM

    def __init__(self, token: str, on_event, *, parse_mode: str = "HTML"):
        self.on_event = on_event
        self.bot = telebot.TeleBot(
            token,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        self.messenger = TelegramMessenger(self.bot)
        self.bot.message_handler(func=lambda message: True, content_types=["text", "document"])(self.__on_message)
        self.bot.callback_query_handler(func=lambda callback_query: True)(self.__on_callback)

    def __on_message(self, message: Message):
        self.on_event(self.to_message_event(message))

    def __on_callback(self, callback_query: CallbackQuery):
        self.on_event(self.to_callback_event(callback_query))

    @staticmethod
    def to_message_event(message: Message) -> IncomingMessage:
        source_document = message.document
        if source_document is None and message.reply_to_message is not None:
            source_document = message.reply_to_message.document
        document = None
        if source_document is not None:
            document = Document(
                source_document.file_id,
                source_document.file_name,
                source_document.mime_type,
            )
        reply_to = None
        if message.reply_to_message is not None:
            reply_to = MessageRef(
                message.reply_to_message.chat.id,
                message.reply_to_message.message_id,
                Platform.TELEGRAM,
            )
        return IncomingMessage(
            Platform.TELEGRAM,
            message.chat.id,
            message.from_user.id,
            message.from_user.username or str(message.from_user.id),
            message.message_id,
            text=message.text,
            caption=message.caption,
            content_type="document" if message.document is not None else "text",
            document=document,
            reply_to=reply_to,
        )

    @staticmethod
    def to_callback_event(callback_query: CallbackQuery) -> IncomingCallback:
        message = callback_query.message
        return IncomingCallback(
            Platform.TELEGRAM,
            callback_query.id,
            callback_query.from_user.id,
            callback_query.from_user.username or str(callback_query.from_user.id),
            callback_query.data,
            MessageRef(message.chat.id, message.message_id, Platform.TELEGRAM),
        )

    def start(self):
        self.bot.infinity_polling()
