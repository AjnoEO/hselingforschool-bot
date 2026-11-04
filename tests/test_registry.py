import unittest
from types import SimpleNamespace

from enums import Platform
from messaging import Media, FileMedia, MessageRef, MessengerRegistry

from .fake_messenger import FakeMessenger


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
        img_media = Media("file.png", filename="file.png")
        file_media = FileMedia("file.png", filename="file.png")

        self.registry.notify(telegram_user, "photo", media=img_media)
        self.registry.notify(vk_user, "document", media=file_media)

        self.assertEqual(self.telegram.calls[-1][:4], ("photo", 10, "photo", img_media))
        self.assertEqual(self.vk.calls[-1][:4], ("document", 20, "document", file_media))

    def test_copy_between_platforms(self):
        telegram_user = SimpleNamespace(platform=Platform.TELEGRAM, external_id=10)
        vk_message = MessageRef(20, 1, Platform.VK)
        self.registry.copy_message(telegram_user, vk_message)
        self.assertEqual(self.telegram.calls[-1][:2], ("copy", 10))        

    def test_copy_same_platform(self):
        telegram_user = SimpleNamespace(platform=Platform.TELEGRAM, external_id=10)
        telegram_message = MessageRef(10, 1, Platform.TELEGRAM)
        self.registry.copy_message(telegram_user, telegram_message)
        self.assertEqual(self.telegram.calls[-1][:2], ("copy", 10))
