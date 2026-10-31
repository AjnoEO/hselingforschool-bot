from db import db_connection
from enums import Platform


class ConversationState: # TODO: Ну и зачем два класса вместо одного
    """Состояние диалога пользователя"""

    def __init__(self, store: "StateStore", platform: Platform, peer_id: int):
        self.store = store
        self.platform = platform
        self.peer_id = peer_id

    def get(self) -> str | None:
        return self.store.get(self.platform, self.peer_id)

    def set(self, state: str):
        self.store.set(self.platform, self.peer_id, state)

    def delete(self):
        self.store.delete(self.platform, self.peer_id)


class StateStore:
    """Хранилище состояний диалогов в SQLite"""

    def get(self, platform: Platform, peer_id: int) -> str | None:
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM conversation_states WHERE platform = ? AND peer_id = ?",
                (platform, peer_id),
            )
            fetch = cur.fetchone()
        return fetch[0] if fetch else None

    def set(self, platform: Platform, peer_id: int, state: str):
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO conversation_states(platform, peer_id, state)
                VALUES (?, ?, ?)
                ON CONFLICT(platform, peer_id) DO UPDATE SET state = excluded.state
                """,
                (platform, peer_id, state),
            )
            conn.commit()

    def delete(self, platform: Platform, peer_id: int):
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM conversation_states WHERE platform = ? AND peer_id = ?",
                (platform, peer_id),
            )
            conn.commit()
