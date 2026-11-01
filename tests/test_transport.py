import unittest
from types import SimpleNamespace

from enums import Platform
from messaging.telegram_transport import TelegramTransport


def make_message(**overrides):
    values = {
        "chat": SimpleNamespace(id=10),
        "message_id": 20,
        "from_user": SimpleNamespace(id=30, username="user"),
        "text": "/start",
        "caption": None,
        "document": None,
        "reply_to_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelegramTransportTest(unittest.TestCase):
    def test_message_event(self):
        event = TelegramTransport.to_message_event(make_message())
        self.assertEqual(event.platform, Platform.TELEGRAM)
        self.assertEqual(event.peer_id, 10)
        self.assertEqual(event.sender_id, 30)
        self.assertEqual(event.sender_handle, "user")
        self.assertEqual(event.text, "/start")
        self.assertEqual(event.content_type, "text")
        self.assertIsNone(event.document)

    def test_document_event(self):
        document = SimpleNamespace(file_id="file-1", file_name="table.xlsx", mime_type="application/vnd.ms-excel")
        event = TelegramTransport.to_message_event(make_message(
            text=None,
            caption="/upload_participants",
            document=document,
        ))
        self.assertEqual(event.content_type, "document")
        self.assertEqual(event.caption, "/upload_participants")
        self.assertEqual(event.document.file_id, "file-1")
        self.assertEqual(event.document.file_name, "table.xlsx")

    def test_reply_to(self):
        reply = SimpleNamespace(chat=SimpleNamespace(id=10), message_id=5, document=None)
        event = TelegramTransport.to_message_event(make_message(reply_to_message=reply))
        self.assertEqual(event.reply_to.peer_id, 10)
        self.assertEqual(event.reply_to.message_id, 5)

    def test_document_in_reply(self):
        reply_document = SimpleNamespace(file_id="file-2", file_name="block.pdf", mime_type="application/pdf")
        reply = SimpleNamespace(
            chat=SimpleNamespace(id=10),
            message_id=5,
            document=reply_document,
        )
        event = TelegramTransport.to_message_event(make_message(
            text="/problem_block_update_file",
            reply_to_message=reply,
        ))
        self.assertEqual(event.content_type, "text")
        self.assertEqual(event.document.file_id, "file-2")
        self.assertEqual(event.document.file_name, "block.pdf")

    def test_callback_event(self):
        callback_query = SimpleNamespace(
            id="callback-1",
            from_user=SimpleNamespace(id=30, username="user"),
            data="join_queue_1",
            message=SimpleNamespace(chat=SimpleNamespace(id=10), message_id=20),
        )
        event = TelegramTransport.to_callback_event(callback_query)
        self.assertEqual(event.platform, Platform.TELEGRAM)
        self.assertEqual(event.callback_id, "callback-1")
        self.assertEqual(event.sender_id, 30)
        self.assertEqual(event.data, "join_queue_1")
        self.assertEqual(event.ref.peer_id, 10)
        self.assertEqual(event.ref.message_id, 20)
