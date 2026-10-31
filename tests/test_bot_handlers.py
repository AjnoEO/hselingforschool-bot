import unittest
from unittest.mock import patch

import bot
from enums import BlockType, OlympStatus, Platform
from messaging import IncomingCallback, IncomingMessage, MessageRef, MessengerRegistry
from olymp import Olymp
from problem import Problem, ProblemBlock
from users import Examiner, Participant
from tests import DatabaseTestCase


class FakeMessenger:
    def __init__(self, platform=Platform.TELEGRAM):
        self.platform = platform
        self.calls = []

    def ref_for(self, message):
        return MessageRef(message.chat.id, message.message_id)

    def send_text(self, peer_id, text, *, keyboard=None, reply_to=None):
        self.calls.append(("text", peer_id, text, keyboard))
        return MessageRef(peer_id, 1)

    def send_photo(self, *args, **kwargs):
        self.calls.append(("photo", args, kwargs))
        return MessageRef(0, 1)

    def send_document(self, *args, **kwargs):
        self.calls.append(("document", args, kwargs))
        return MessageRef(0, 1)

    def edit_text(self, ref, text, *, keyboard=None):
        self.calls.append(("edit_text", ref, text, keyboard))

    def edit_keyboard(self, ref, keyboard):
        self.calls.append(("edit_keyboard", ref, keyboard))

    def delete(self, ref):
        self.calls.append(("delete", ref))

    def answer_callback(self, callback_id, text="", show_alert=False, *, user_id=None, peer_id=None):
        self.calls.append(("answer", callback_id, text, show_alert))

    def copy_message(self, peer_id, ref):
        self.calls.append(("copy", peer_id, ref))
        return MessageRef(peer_id, 1)

    def download_document(self, document):
        self.calls.append(("download", document))
        return b"data"


def make_event(text: str, sender_id: int = 10) -> IncomingMessage:
    return IncomingMessage(
        Platform.TELEGRAM, sender_id, sender_id, "sender", 20, text=text
    )


class BotHandlerSmokeTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.messenger = FakeMessenger()
        self.notifier = MessengerRegistry()
        self.notifier.add(self.messenger)
        self.notifier_patch = patch.object(bot, "notifier", self.notifier)
        self.notifier_patch.start()
        self.addCleanup(self.notifier_patch.stop)

    def test_lost_replies_through_context(self):
        bot.handle_event(make_event("/start"))
        self.assertEqual(self.messenger.calls[0][0], "text")
        self.assertEqual(self.messenger.calls[0][1], 10)

    def test_callback_is_dispatched(self):
        callback = IncomingCallback(
            Platform.TELEGRAM, "callback-1", 10, "sender",
            "page_list_participant_invalid", MessageRef(10, 20, Platform.TELEGRAM),
        )
        bot.handle_event(callback)
        self.assertEqual(self.messenger.calls[0][0], "answer")
        self.assertEqual(self.messenger.calls[0][2], "Страницы нет")


class CrossPlatformAnnounceTest(DatabaseTestCase):
    def test_queue_entry_notifies_each_member_on_their_platform(self):
        olymp = Olymp.create("Олимпиада")
        olymp.status = OlympStatus.CONTEST
        participant = Participant.create_as_new_user(
            "participant", "Имя", "Фамилия", 8, olymp.id,
            platform=Platform.TELEGRAM, external_id=100,
        )
        problems = [
            Problem.create(olymp.id, f"Задача {number}")
            for number in range(1, 4)
        ]
        ProblemBlock.create(olymp.id, problems, BlockType.JUNIOR_1)
        examiner = Examiner.create_as_new_user(
            "examiner", "Имя", "Фамилия", "https://example.com", olymp.id,
            platform=Platform.VK, external_id=200,
            problems=[problems[0].id], is_busy=False,
        )
        queue_entry = participant.join_queue(problems[0])

        telegram = FakeMessenger(Platform.TELEGRAM)
        vk = FakeMessenger(Platform.VK)
        registry = MessengerRegistry()
        registry.add(telegram)
        registry.add(vk)

        with patch.object(bot, "notifier", registry), patch.object(bot, "current_olymp", olymp):
            bot.announce_queue_entry(queue_entry)

        self.assertEqual(len(telegram.calls), 1)
        self.assertEqual(len(vk.calls), 1)
        self.assertEqual(telegram.calls[0][1], 100)
        self.assertEqual(vk.calls[0][1], 200)
        self.assertIn("примет", telegram.calls[0][2])
        self.assertIn("идёт сдавать", vk.calls[0][2])
