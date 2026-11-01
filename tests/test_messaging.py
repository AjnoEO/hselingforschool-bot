import os
import unittest
from types import SimpleNamespace

from telebot.types import (
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from enums import Platform
from messaging import (
    Button,
    Context,
    IncomingCallback,
    IncomingMessage,
    Keyboard,
    Media,
    MessageRef,
    MessengerRegistry,
    REMOVE_KEYBOARD,
    inline_keyboard,
)
from messaging.telegram import TelegramMessenger
from tests import REPO_DIR


class FakeBot:
    def __init__(self):
        self.calls = []
        self.__last_message_id = 100

    def __message(self, chat_id):
        self.__last_message_id += 1
        return SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=self.__last_message_id,
        )

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("send_message", chat_id, text, kwargs))
        return self.__message(chat_id)

    def send_photo(self, chat_id, **kwargs):
        self.calls.append(("send_photo", chat_id, kwargs))
        return self.__message(chat_id)

    def send_document(self, chat_id, **kwargs):
        self.calls.append(("send_document", chat_id, kwargs))
        return self.__message(chat_id)

    def edit_message_text(self, text, chat_id, message_id, **kwargs):
        self.calls.append(("edit_message_text", chat_id, message_id, text, kwargs))

    def edit_message_reply_markup(self, chat_id, message_id, **kwargs):
        self.calls.append(("edit_message_reply_markup", chat_id, message_id, kwargs))

    def delete_message(self, chat_id, message_id):
        self.calls.append(("delete_message", chat_id, message_id))

    def answer_callback_query(self, callback_id, **kwargs):
        self.calls.append(("answer_callback_query", callback_id, kwargs))

    def copy_message(self, chat_id, from_chat_id, message_id):
        self.calls.append(("copy_message", chat_id, from_chat_id, message_id))
        return SimpleNamespace(message_id=555)


class TelegramMessengerTest(unittest.TestCase):
    def setUp(self):
        self.bot = FakeBot()
        self.messenger = TelegramMessenger(self.bot)

    def tearDown(self):
        for call in self.bot.calls:
            if len(call) < 3 or not isinstance(call[2], dict):
                continue
            for key in ("photo", "document"):
                input_file = call[2].get(key)
                if isinstance(input_file, InputFile):
                    input_file._file.close()

    def test_send_text_inline_keyboard(self):
        keyboard = inline_keyboard({'Да': 'yes', 'Нет': 'no'}, row_width=2)
        ref = self.messenger.send_text(10, "Вопрос", keyboard=keyboard)
        self.assertEqual(ref.peer_id, 10)
        call = self.bot.calls[-1]
        self.assertEqual(call[0], "send_message")
        self.assertEqual(call[1], 10)
        self.assertEqual(call[2], "Вопрос")
        markup = call[3]["reply_markup"]
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        buttons = markup.to_dict()["inline_keyboard"][0]
        self.assertEqual(buttons[0]["text"], "Да")
        self.assertEqual(buttons[0]["callback_data"], "yes")

    def test_send_text_reply_keyboard_and_remove(self):
        keyboard = Keyboard()
        keyboard.row(Button("Сдать"), Button("Мои задачи"))
        self.messenger.send_text(10, "Кнопки", keyboard=keyboard)
        markup = self.bot.calls[-1][3]["reply_markup"]
        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        buttons = markup.keyboard[0]
        self.assertEqual(buttons[0]["text"], "Сдать")

        self.messenger.send_text(10, "Без кнопок", keyboard=REMOVE_KEYBOARD)
        self.assertIsInstance(self.bot.calls[-1][3]["reply_markup"], ReplyKeyboardRemove)

    def test_send_photo_and_document(self):
        photo_path = os.path.join(REPO_DIR, "predefined_files", "buttons.png")
        document_path = os.path.join(REPO_DIR, "predefined_files", "default_problem_block.pdf")
        photo_ref = self.messenger.send_photo(
            10,
            Media(photo_path, filename="Где_кнопки.png"),
            caption="Кнопки",
            show_caption_above_media=True,
        )
        self.assertEqual(photo_ref.peer_id, 10)
        self.assertIsInstance(self.bot.calls[-1][2]["photo"], InputFile)
        self.assertTrue(self.bot.calls[-1][2]["show_caption_above_media"])

        self.messenger.send_document(
            10,
            Media(document_path, filename="Блок_1.pdf"),
            caption="Блок",
        )
        self.assertIsInstance(self.bot.calls[-1][2]["document"], InputFile)

    def test_edit_delete_answer_and_copy(self):
        ref = MessageRef(10, 20)
        self.messenger.edit_text(ref, "Новый текст", keyboard=None)
        self.assertEqual(self.bot.calls[-1][0], "edit_message_text")
        self.assertEqual(self.bot.calls[-1][1:4], (10, 20, "Новый текст"))

        self.messenger.edit_keyboard(ref, None)
        self.assertEqual(self.bot.calls[-1][0], "edit_message_reply_markup")

        self.messenger.delete(ref)
        self.assertEqual(self.bot.calls[-1], ("delete_message", 10, 20))

        self.messenger.answer_callback("callback-1", text="Ок")
        self.assertEqual(self.bot.calls[-1][0], "answer_callback_query")
        self.assertEqual(self.bot.calls[-1][1], "callback-1")
        self.assertEqual(self.bot.calls[-1][2]["text"], "Ок")

        copied = self.messenger.copy_message(30, ref)
        self.assertEqual(self.bot.calls[-1], ("copy_message", 30, 10, 20))
        self.assertEqual(copied.message_id, 555)


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.bot = FakeBot()
        self.messenger = TelegramMessenger(self.bot)
        self.registry = MessengerRegistry()
        self.registry.add(self.messenger)
        self.message = IncomingMessage(
            Platform.TELEGRAM, 10, 10, "sender", 20, text="x"
        )

    def test_reply_and_notify(self):
        ctx = Context(self.registry, event=self.message)
        self.assertEqual(ctx.peer_id, 10)
        ctx.reply("Ответ")
        self.assertEqual(self.bot.calls[-1][1], 10)

        user = SimpleNamespace(platform=Platform.TELEGRAM, external_id=30)
        ctx.notify(user, "Личное")
        self.assertEqual(self.bot.calls[-1][1], 30)

    def test_callback_context(self):
        callback = IncomingCallback(
            Platform.TELEGRAM, "callback-1", 10, "sender", "x",
            MessageRef(10, 20, Platform.TELEGRAM),
        )
        ctx = Context(self.registry, event=callback)
        ctx.answer("Ок")
        self.assertEqual(self.bot.calls[-1][1], "callback-1")
        ctx.edit_keyboard(None)
        self.assertEqual(self.bot.calls[-1][0], "edit_message_reply_markup")
        ctx.delete()
        self.assertEqual(self.bot.calls[-1][0], "delete_message")
