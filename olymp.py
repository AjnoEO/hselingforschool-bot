from enums import OlympStatus, QueueStatus
import sqlite3
from db import DATABASE
from users import Participant, Examiner
from problem import Problem
from queue_entry import QueueEntry
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
            raise UserError(f"Олимпиады <em>{name}</em> не найдено")
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
            raise UserError(f"Олимпиада <em>{name}</em> уже есть в базе")
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
    
    def last_queue_entries(
        self, limit: int = 10, *, 
        participant: Participant | int | None = None, 
        examiner: Examiner | int | None = None,
        problem: Problem | int | None = None
    ):
        if isinstance(participant, Participant): participant = participant.id
        if isinstance(examiner, Examiner): examiner = examiner.id
        if isinstance(problem, Problem): problem = problem.id
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            q = "SELECT * FROM queue WHERE olymp_id = ?"
            params = [self.id]
            if participant:
                q += " AND participant_id = ?"
                params.append(participant)
            if examiner:
                q += " AND examiner_id = ?"
                params.append(examiner)
            if problem:
                q += " AND problem_id = ?"
                params.append(problem)
            q += f" ORDER BY id DESC LIMIT {limit}"
            cur.execute(q, tuple(params))
            results = cur.fetchall()
            queue_entries = [QueueEntry(*fetch) for fetch in results]
            return queue_entries[::-1]


    @staticmethod
    @provide_cursor
    def list_all(*, cursor: sqlite3.Cursor | None = None):
        cursor.execute("SELECT * FROM olymps")
        results = cursor.fetchall()
        return [Olymp(*fetch) for fetch in results]


    @provide_cursor
    def __amount(self, table: str, *, cursor: sqlite3.Cursor | None = None) -> int:
        cursor.execute(f"SELECT 1 FROM {table} WHERE olymp_id = ?", (self.id,))
        return len(cursor.fetchall())

    @provide_cursor
    def get_participants(
        self, start: int | None = None, limit: int | None = None, *, sort: bool = False, cursor: sqlite3.Cursor | None = None
    ) -> list[Participant]:
        if not limit and start:
            raise ValueError("Нельзя устанавливать начало списка участников, не устанавливая ограничение на количество")
        q = "SELECT user_id FROM participants WHERE olymp_id = ?"
        if sort:
            q += " ORDER BY id ASC"
        if limit:
            q += f" LIMIT {limit}"
            if start:
                q += f" OFFSET {start}"
        cursor.execute(q, (self.id,))
        results = cursor.fetchall()
        return [Participant.from_user_id(user_id_tuple[0], self.id) for user_id_tuple in results]
    
    def participants_amount(self) -> int:
        return self.__amount("participants")
    
    @provide_cursor
    def get_examiners(
        self, start: int | None = None, limit: int | None = None, *,
        sort: bool = False, only_free: bool = False, order_by_busyness: bool = False, cursor: sqlite3.Cursor | None = None
    ) -> list[Examiner]:
        if not limit and start:
            raise ValueError("Нельзя устанавливать начало списка принимающих, не устанавливая ограничение на количество")
        q = "SELECT user_id FROM examiners WHERE olymp_id = ?"
        if only_free:
            q += " AND is_busy = 0"
        if order_by_busyness:
            q += " ORDER BY busyness_level ASC"
        elif sort:
            q += " ORDER BY id ASC"
        if limit:
            q += f" LIMIT {limit}"
            if start:
                q += f" OFFSET {start}"
        cursor.execute(q, (self.id,))
        results = cursor.fetchall()
        return [Examiner.from_user_id(user_id_tuple[0], self.id) for user_id_tuple in results]
    
    def examiners_amount(self) -> int:
        return self.__amount("examiners")
    
    @provide_cursor
    def get_problems(self, *, cursor: sqlite3.Cursor | None = None) -> list[Problem]:
        cursor.execute("SELECT * FROM problems WHERE olymp_id = ? ORDER BY id", (self.id,))
        results = cursor.fetchall()
        return [Problem(*fetch) for fetch in results]
    
    def problems_amount(self) -> int:
        return self.__amount("problems")


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
