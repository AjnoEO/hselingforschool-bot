from datetime import datetime
from statuses import OlympStatus
import sqlite3
from db import DATABASE
from users import Participant, Examiner
from utils import UserError, value_exists, provide_cursor, update_in_table


class Olymp:
    def __init__(
        self,
        id: int,
        year: int,
        name: str,
        status: int | OlympStatus
    ):
        self.__id = id
        self.__year = year
        self.__name = name
        self.__status = OlympStatus(status) if isinstance(status, int) else status

    @classmethod
    def from_year_name(cls, year: int | None = None, name: str | None = None):
        if not year:
            year = datetime.today().year
        q = "SELECT * FROM olymps WHERE year = ?"
        parameters = [year]
        if name:
            q += " AND name = ?"
            parameters.append(name)
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute(q, tuple(parameters))
            fetch = cur.fetchall()
        if not fetch:
            raise UserError(
                f"Олимпиады {name} за {year} год не найдено"
                if name else
                f"Нет олимпиад за {year} год"
            )
        if len(fetch) > 1:
            raise UserError(f"Есть несколько олимпиад за {year} год. Укажите имя")
        return cls(*fetch[0])
    
    
    @classmethod
    @provide_cursor
    def create(
        cls,
        year: int,
        name: str,
        status: OlympStatus = OlympStatus.TBA,
        *,
        cursor: sqlite3.Cursor,
    ):
        """
        Добавить год в таблицу olymps
        """
        exists = value_exists("olymps", {"year": year, "name": name})
        if exists:
            raise ValueError(f"Олимпиада {name} за {year} год уже есть в базе")
        values = (year, name, status)
        cursor.execute("INSERT INTO olymps(year, name, status) VALUES (?, ?, ?)", values)
        cursor.connection.commit()
        created_id = cursor.lastrowid
        return cls(created_id, *values)


    def __set(self, column: str, value):
        update_in_table("olymps", column, value, "id", self.__id)

    @property
    def id(self): return self.__id
    @property
    def year(self): return self.__year
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
        self.__set('name', status.value)
        self.__status = status

    @provide_cursor
    def get_participants(self, *, cursor: sqlite3.Cursor) -> list[Participant]:
        cursor.execute("SELECT user_id FROM participants WHERE olymp_id = ?", (self.id,))
        results = cursor.fetchall()
        return [Participant.from_user_id(user_id) for user_id in results]
    
    @provide_cursor
    def get_examiners(self, *, only_free: bool = False, order_by_busyness: bool = False, cursor: sqlite3.Cursor) -> list[Participant]:
        q = "SELECT user_id FROM examiners WHERE olymp_id = ?"
        if only_free:
            q += " AND is_busy = 0"
        if order_by_busyness:
            q += " ORDER BY busyness_level ASC"
        cursor.execute(q, (self.id,))
        results = cursor.fetchall()
        return [Examiner.from_user_id(user_id) for user_id in results]
