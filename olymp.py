from datetime import datetime
from statuses import OlympStatus
import sqlite3
from db import DATABASE
from users import Participant, Examiner, UserError
from utils import value_exists, provide_cursor


class Olymp:
    def __init__(self, year: int | None = None, name: str | None = None):
        if not year:
            year = datetime.today().year
        if name:
            if not value_exists('olymps', {'year': year, 'name': name}):
                raise UserError(f"Олимпиады {name} за {year} год не существует")
            with sqlite3.connect(DATABASE) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, status FROM olymps WHERE year = ? AND name = ?", (year, name))
                id, status = cur.fetchone()
                status = OlympStatus(status)
        else:
            if not value_exists('olymps', {'year': year}):
                raise UserError(f"Нет олимпиад за {year} год")
            with sqlite3.connect(DATABASE) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, status, name FROM olymps WHERE year = ?", (year,))
                fetch = cur.fetchall()
                if len(fetch) > 1:
                    raise UserError(f"Есть несколько олимпиад за {year} год. Укажите имя")
                id, status, name = fetch[0]
                status = OlympStatus(status)
        self.__id: int = id
        self.__year: int = year
        self.__name: str = name
        self.__status: OlympStatus = status
    
    
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
        cursor.execute("INSERT INTO olymps(year, name, status) VALUES (?, ?, ?)", (year, name, status))
        cursor.connection.commit()
        return Olymp(year, name)


    def __set(self, column: str, value):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            q = f"UPDATE olymps SET {column} = ? WHERE id = ?"
            cur.execute(q, (value, self.id))
            conn.commit()

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
