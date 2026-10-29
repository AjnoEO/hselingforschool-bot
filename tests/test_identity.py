from enums import Platform
from olymp import Olymp
from users import Participant, User
from utils import UserError
from tests import DatabaseTestCase


class UserIdentityTest(DatabaseTestCase):
    def test_create_adds_identity_fields(self):
        user = User.create("handle", "Имя", "Фамилия", external_id=123)
        self.assertEqual(user.platform, Platform.TELEGRAM)
        self.assertEqual(user.external_id, 123)
        self.assertEqual(user.handle, "handle")
        self.assertTrue(user.is_authenticated)

    def test_from_external_id_and_handle(self):
        user = User.create("handle", "Имя", "Фамилия", external_id=123)
        self.assertEqual(User.from_external_id(Platform.TELEGRAM, 123).user_id, user.user_id)
        self.assertEqual(User.from_handle(Platform.TELEGRAM, "@handle").user_id, user.user_id)
        self.assertIsNone(User.from_external_id(Platform.TELEGRAM, 456, no_error=True))

    def test_vk_user(self):
        user = User.create("vk_handle", "Имя", "Фамилия", platform=Platform.VK, external_id=456)
        self.assertEqual(user.platform, Platform.VK)
        self.assertEqual(User.from_external_id(Platform.VK, 456).user_id, user.user_id)
        self.assertIsNone(User.from_external_id(Platform.TELEGRAM, 456, no_error=True))

    def test_external_id_update(self):
        user = User.create("handle", "Имя", "Фамилия")
        user.external_id = 456
        self.assertEqual(User.from_external_id(Platform.TELEGRAM, 456).user_id, user.user_id)

    def test_handle_update(self):
        user = User.create("old_handle", "Имя", "Фамилия", external_id=123)
        user.handle = "new_handle"
        self.assertEqual(User.from_handle(Platform.TELEGRAM, "new_handle").user_id, user.user_id)
        self.assertIsNone(User.from_handle(Platform.TELEGRAM, "old_handle", no_error=True))

    def test_create_keeps_external_id_on_reupload(self):
        user = User.create("handle", "Имя", "Фамилия", external_id=123)
        User.create("handle", "Имя", "Фамилия", ok_if_exists=True)
        self.assertEqual(User.from_user_id(user.user_id).external_id, 123)
        self.assertEqual(User.from_external_id(Platform.TELEGRAM, 123).user_id, user.user_id)

    def test_conflate_moves_handle(self):
        old_user = User.create("old_handle", "Имя", "Фамилия", external_id=123)
        new_user = User.create("new_handle", "Имя", "Фамилия")
        old_user.conflate_with(new_user)
        self.assertIsNone(User.from_user_id(new_user.user_id, no_error=True))
        self.assertEqual(User.from_handle(Platform.TELEGRAM, "new_handle").user_id, old_user.user_id)
        self.assertEqual(User.from_external_id(Platform.TELEGRAM, 123).user_id, old_user.user_id)
        self.assertIsNone(User.from_handle(Platform.TELEGRAM, "old_handle", no_error=True))

    def test_conflate_rejects_different_platforms(self):
        telegram_user = User.create("handle", "Имя", "Фамилия", external_id=123)
        vk_user = User.create("vk_handle", "Имя", "Фамилия", platform=Platform.VK)
        with self.assertRaises(UserError):
            telegram_user.conflate_with(vk_user)

    def test_member_from_external_id(self):
        olymp = Olymp.create("Олимпиада")
        participant = Participant.create_as_new_user(
            "handle", "Имя", "Фамилия", 8, olymp.id, external_id=123
        )
        found = Participant.from_external_id(Platform.TELEGRAM, 123, olymp.id)
        self.assertEqual(found.id, participant.id)
        self.assertIsNone(Participant.from_external_id(Platform.TELEGRAM, 456, olymp.id, no_error=True))

    def test_member_not_found_message(self):
        olymp = Olymp.create("Олимпиада")
        User.create("outsider", "Имя", "Фамилия", external_id=123)
        for external_id in (123, 456):
            with self.assertRaises(UserError) as context:
                Participant.from_external_id(Platform.TELEGRAM, external_id, olymp.id)
            self.assertIn("Участник не найден", str(context.exception))
