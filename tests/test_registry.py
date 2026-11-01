import unittest
from types import SimpleNamespace

from enums import Platform
from messaging import Media, MessageRef, MessengerRegistry


class FakeMessenger:
    def __init__(self, platform):
        self.platform = platform
        self.calls = []

    def send_text(self, peer_id, text, *, keyboard=None, reply_to=None):
        self.calls.append(("text", peer_id, text, keyboard))
        return MessageRef(peer_id, 1, self.platform)

    def send_photo(self, peer_id, media, *, caption="", keyboard=None, show_caption_above_media=False):
        self.calls.append(("photo", peer_id, media, caption))
        return MessageRef(peer_id, 1, self.platform)

    def send_document(self, peer_id, media, *, caption="", keyboard=None):
        self.calls.append(("document", peer_id, media, caption))
        return MessageRef(peer_id, 1, self.platform)

    def copy_message(self, peer_id, ref):
        self.calls.append(("copy", peer_id, ref))
        return MessageRef(peer_id, 1, self.platform)


class MessengerRegistryTest(unittest.TestCase):
    def setUp(self):
        self.telegram = FakeMessenger(Platform.TELEGRAM)
        self.vk = FakeMessenger(Platform.VK)
        self.registry = MessengerRegistry()
        self.registry.add(self.telegram)
        self.registry.add(self.vk)

    def test_notify_routes_by_user_platform(self):
        telegram_user = SimpleNamespace(platform=Platform.TELEGRAM, external_id=10)
        vk_user = SimpleNamespace(platform=Platform.VK, external_id=20)

        self.registry.notify(telegram_user, "telegram")
        self.registry.notify(vk_user, "vk")

        self.assertEqual(self.telegram.calls[-1][:3], ("text", 10, "telegram"))
        self.assertEqual(self.vk.calls[-1][:3], ("text", 20, "vk"))
        self.assertEqual(len(self.telegram.calls), 1)
        self.assertEqual(len(self.vk.calls), 1)

    def test_notify_media_routes_by_platform(self):
        telegram_user = SimpleNamespace(platform=Platform.TELEGRAM, external_id=10)
        vk_user = SimpleNamespace(platform=Platform.VK, external_id=20)
        media = Media("file.pdf", filename="file.pdf")

        self.registry.notify_photo(telegram_user, media, caption="photo")
        self.registry.notify_document(vk_user, media, caption="document")

        self.assertEqual(self.telegram.calls[-1][:4], ("photo", 10, media, "photo"))
        self.assertEqual(self.vk.calls[-1][:4], ("document", 20, media, "document"))

    def test_copy_between_platforms_is_rejected(self):
        telegram_user = SimpleNamespace(platform=Platform.TELEGRAM, external_id=10)
        vk_message = MessageRef(20, 1, Platform.VK)
        with self.assertRaises(NotImplementedError):
            self.registry.copy_message(telegram_user, vk_message)

    def test_copy_same_platform(self):
        telegram_user = SimpleNamespace(platform=Platform.TELEGRAM, external_id=10)
        telegram_message = MessageRef(10, 1, Platform.TELEGRAM)
        self.registry.copy_message(telegram_user, telegram_message)
        self.assertEqual(self.telegram.calls[-1][:2], ("copy", 10))
