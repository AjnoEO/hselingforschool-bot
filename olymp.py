from enums import OlympStatus, QueueStatus
import sqlite3
from db import DATABASE
from users import Participant, Examiner
from problem import Problem
from utils import UserError, value_exists, provide_cursor, update_in_table


class Olymp:
    def __init__(
        self,
        id: int,
        name: str,
        status: int | OlympStatus
    ):
        self.__id = id
        self.__name = name
        self.__status = OlympStatus(status) if isinstance(status, int) else status


    @classmethod
    def from_name(cls, name: str):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM olymps WHERE name = ?", (name,))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError(f"Олимпиады {name} не найдено")
        return cls(*fetch)
    
    
    @classmethod
    @provide_cursor
    def create(
        cls,
        name: str,
        status: OlympStatus = OlympStatus.TBA,
        *,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить год в таблицу olymps
        """
        exists = value_exists("olymps", {"name": name})
        if exists:
            raise ValueError(f"Олимпиада {name} уже есть в базе")
        values = (name, status)
        cursor.execute("INSERT INTO olymps(name, status) VALUES (?, ?)", values)
        cursor.connection.commit()
        created_id = cursor.lastrowid
        return cls(created_id, *values)


    @classmethod
    @provide_cursor
    def current(cls, *, cursor: sqlite3.Cursor | None = None):
        cursor.execute("SELECT * FROM olymps WHERE status != ?", (OlympStatus.RESULTS,))
        fetch = cursor.fetchall()
        if len(fetch) > 1:
            raise ValueError("Найдено более одной текущей олимпиады")
        if len(fetch) == 0:
            return None
        return Olymp(*fetch[0])
    

    def unhandled_queue_left(self) -> bool:
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            q = (f"SELECT EXISTS(SELECT 1 FROM queue WHERE olymp_id = ?"
                 f"AND status IN ({','.join(map(str, QueueStatus.active(as_numbers=True)))}))")
            cur.execute(q, (self.id,))
            result = cur.fetchone()
            return bool(result[0])


    @provide_cursor
    def get_participants(self, *, cursor: sqlite3.Cursor | None = None) -> list[Participant]:
        cursor.execute("SELECT user_id FROM participants WHERE olymp_id = ?", (self.id,))
        results = cursor.fetchall()
        return [Participant.from_user_id(user_id_tuple[0], self.id) for user_id_tuple in results]
    
    @provide_cursor
    def get_examiners(self, *, only_free: bool = False, order_by_busyness: bool = False, cursor: sqlite3.Cursor | None = None) -> list[Participant]:
        q = "SELECT user_id FROM examiners WHERE olymp_id = ?"
        if only_free:
            q += " AND is_busy = 0"
        if order_by_busyness:
            q += " ORDER BY busyness_level ASC"
        cursor.execute(q, (self.id,))
        results = cursor.fetchall()
        return [Examiner.from_user_id(user_id_tuple[0], self.id) for user_id_tuple in results]
    
    @provide_cursor
    def get_problems(self, *, cursor: sqlite3.Cursor | None = None) -> list[Problem]:
        cursor.execute("SELECT * FROM problems WHERE olymp_id = ? ORDER BY id", (self.id,))
        results = cursor.fetchall()
        return [Problem(*fetch) for fetch in results]


    def __set(self, column: str, value):
        update_in_table("olymps", column, value, "id", self.__id)

    @property
    def id(self): return self.__id
    @property
    def name(self): return self.__name
    @name.setter
    def name(self, value: str):
        self.__set('name', value)
        self.__name = value
    @property
    def status(self): return self.__status
    @status.setter
    def status(self, status: OlympStatus):
        self.__set('status', status.value)
        self.__status = status
