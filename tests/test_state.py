from enums import Platform
from messaging import ConversationState, StateStore
from tests import DatabaseTestCase


class StateStoreTest(DatabaseTestCase):
    def test_set_get_delete(self):
        state = ConversationState(StateStore(), Platform.TELEGRAM, 999)
        state.delete()
        self.assertIsNone(state.get())

        state.set("choosing_problems")
        self.assertEqual(state.get(), "choosing_problems")

        state.delete()
        self.assertIsNone(state.get())
