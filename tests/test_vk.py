import unittest

try:
    from messaging.vk import VkMessenger, html_to_vk, truncate_label, vk_keyboard
    from messaging.vk_transport import VkTransport
except ImportError:  # vk-api is an optional dependency for non-VK test runs
    VkMessenger = html_to_vk = truncate_label = vk_keyboard = VkTransport = None

from messaging import Button, Keyboard, Media


@unittest.skipIf(html_to_vk is None, "vk-api is not installed")
class VkFormattingTest(unittest.TestCase):
    def test_html_is_stripped_and_links_are_kept(self):
        result = html_to_vk('<strong>Привет</strong> <a href="https://vk.com/id1">vk@id1</a>')
        self.assertEqual(result, "Привет vk@id1 (https://vk.com/id1)")

    def test_html_br(self):
        self.assertEqual(html_to_vk("Первая<br>Вторая"), "Первая\nВторая")

    def test_label_is_truncated_to_40(self):
        label = truncate_label("x" * 50)
        self.assertEqual(len(label), 40)
        self.assertTrue(label.endswith("…"))

    def test_inline_keyboard_uses_callback_buttons(self):
        keyboard = Keyboard(inline=True)
        keyboard.row(Button("Сдать", callback_data="join_queue_1"))
        self.assertIn('"type":"callback"', vk_keyboard(keyboard))
        self.assertIn('"payload"', vk_keyboard(keyboard))

    def test_reply_keyboard_uses_text_buttons(self):
        keyboard = Keyboard()
        keyboard.row(Button("Сдать"))
        result = vk_keyboard(keyboard)
        self.assertIn('"type":"text"', result)
        self.assertIn('"inline":false', result)


@unittest.skipIf(VkMessenger is None, "vk-api is not installed")
class VkMessengerTest(unittest.TestCase):
    def test_photo_upload_failure_falls_back_to_text(self):
        from unittest.mock import MagicMock, patch

        messenger = object.__new__(VkMessenger)
        messenger.api = MagicMock()
        messenger.api.photos.getMessagesUploadServer.return_value = {"upload_url": "https://upload"}
        messenger.api.messages.send.return_value = 42
        messenger._VkMessenger__texts = {}

        response = MagicMock()
        response.status_code = 502
        response.json.side_effect = ValueError("empty body")

        with patch("messaging.vk.requests.post", return_value=response):
            result = messenger.send_photo(
                123, Media("predefined_files/buttons.png"), caption="Подпись"
            )

        self.assertEqual(result.message_id, 42)
        self.assertEqual(messenger.api.messages.send.call_args.kwargs["message"], "Подпись")

    def test_answer_callback_uses_event_ids(self):
        from unittest.mock import MagicMock

        messenger = object.__new__(VkMessenger)
        messenger.api = MagicMock()
        messenger.answer_callback("123", "Готово", user_id=10, peer_id=20)
        messenger.api.messages.sendMessageEventAnswer.assert_called_once()
        kwargs = messenger.api.messages.sendMessageEventAnswer.call_args.kwargs
        self.assertEqual(kwargs["event_id"], "123")
        self.assertEqual(kwargs["user_id"], 10)
        self.assertEqual(kwargs["peer_id"], 20)

    def test_empty_callback_text_uses_fallback_snackbar(self):
        from unittest.mock import MagicMock

        messenger = object.__new__(VkMessenger)
        messenger.api = MagicMock()
        messenger.answer_callback("123", "", user_id=10, peer_id=20)
        kwargs = messenger.api.messages.sendMessageEventAnswer.call_args.kwargs
        self.assertIn("Готово", kwargs["event_data"])


@unittest.skipIf(VkTransport is None, "vk-api is not installed")
class VkTransportTest(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.transport = object.__new__(VkTransport)
        self.transport._VkTransport__handles = {}
        self.transport._VkTransport__handle_for = lambda user_id: "ivan"
        self.SimpleNamespace = SimpleNamespace

    def test_message_event(self):
        message = {
            "peer_id": 10,
            "from_id": 20,
            "conversation_message_id": 30,
            "text": "/start",
            "attachments": [],
        }
        event = self.SimpleNamespace(object=self.SimpleNamespace(message=message))
        result = self.transport.to_message_event(event)
        self.assertEqual(result.platform.code, "vk")
        self.assertEqual(result.peer_id, 10)
        self.assertEqual(result.sender_id, 20)
        self.assertEqual(result.sender_handle, "ivan")
        self.assertEqual(result.message_id, 30)
        self.assertEqual(result.text, "/start")

    def test_document_event(self):
        message = {
            "peer_id": 10,
            "from_id": 20,
            "conversation_message_id": 30,
            "text": "",
            "attachments": [{"type": "doc", "doc": {"url": "https://example.com/a.pdf", "title": "a.pdf"}}],
        }
        event = self.SimpleNamespace(object=self.SimpleNamespace(message=message))
        result = self.transport.to_message_event(event)
        self.assertEqual(result.content_type, "document")
        self.assertEqual(result.document.file_id, "https://example.com/a.pdf")

    def test_callback_event(self):
        obj = {
            "peer_id": 10,
            "user_id": 20,
            "event_id": 30,
            "conversation_message_id": 40,
            "payload": '{"data": "join_queue_1"}',
        }
        result = self.transport.to_callback_event(self.SimpleNamespace(object=obj))
        self.assertEqual(result.callback_id, "30")
        self.assertEqual(result.sender_id, 20)
        self.assertEqual(result.data, "join_queue_1")
        self.assertEqual(result.ref.message_id, 40)


class PlatformIdentityTest(unittest.TestCase):
    def test_parse_identity_arg(self):
        import bot
        from enums import Platform

        self.assertEqual(bot.parse_identity_arg("vk@Иван"), (Platform.VK, "иван"))
        self.assertEqual(bot.parse_identity_arg("tg@Иван"), (Platform.TELEGRAM, "иван"))
        with self.assertRaises(bot.UserError):
            bot.parse_identity_arg("@Иван")

    def test_display_handle_links(self):
        from types import SimpleNamespace
        from enums import Platform
        from users import User

        telegram = SimpleNamespace(platform=Platform.TELEGRAM, handle="ajnoeo", external_id=10)
        telegram_link = User.display_handle(telegram)
        self.assertIn('href="https://t.me/ajnoeo"', telegram_link)
        self.assertIn("telegram@ajnoeo", telegram_link)

        vk = SimpleNamespace(platform=Platform.VK, handle="123", external_id=123)
        vk_link = User.display_handle(vk)
        self.assertIn('href="https://vk.ru/id123"', vk_link)
        self.assertIn("vk@123", vk_link)
