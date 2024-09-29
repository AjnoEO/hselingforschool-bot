import sqlite3
from enums import QueueStatus
from db import DATABASE
from utils import update_in_table, UserError

class QueueEntry:
    def __init__(
        self,
        id: int,
        olymp_id: int,
        participant_id: int,
        problem_id: int,
        status: QueueStatus | int = QueueStatus.WAITING,
        examiner_id: int | None = None
    ):
        self.__id: int = id
        self.__olymp_id: int = olymp_id
        self.__participant_id: int = participant_id
        self.__problem_id: int = problem_id
        self.__status: QueueStatus = QueueStatus(status) if not isinstance(status, QueueStatus) else status
        self.__examiner_id: int | None = examiner_id

    @classmethod
    def from_id(cls, id: int):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM queue WHERE id = ?", (id,))
            fetch = cur.fetchone()
            if fetch is None:
                raise UserError("Запись не найдена")
            return cls(*fetch)

    def look_for_examiner(self) -> int | None:
        """
        Найти подходящего принимающего. Если найден, возвращает его ID, если нет — возвращает `None`. 

        Само `QueueEntry` не меняется! Принимающий не назначается! 
        Чтобы назначить принимающего, используй метод `Examiner.assign_to_queue_entry`
        """
        if self.status != QueueStatus.WAITING:
            raise ValueError("Нельзя искать принимающих для записей не в статусе ожидания")
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()            
            q = """
                SELECT
                    id
                FROM 
                    examiners 
                    RIGHT JOIN examiner_problems ON examiners.id = examiner_problems.examiner_id
                WHERE 
                    is_busy = 0 AND problem_id = ?
                ORDER BY
                    busyness_level DESC 
                LIMIT 1
                """
            cur.execute(q, (self.problem_id,))
            fetch = cur.fetchone()
        return fetch[0] if fetch else None

    def __set(self, column, value):
        update_in_table("queue", column, value, "id", self.__id)

    @property
    def id(self): return self.__id
    @property
    def olymp_id(self): return self.__olymp_id
    @property
    def participant_id(self): return self.__participant_id
    @property
    def problem_id(self): return self.__problem_id
    @problem_id.setter
    def problem_id(self, value: int):
        self.__set("problem_id", value)
        self.__problem_id = value
    @property
    def status(self): return self.__status
    @status.setter
    def status(self, value: QueueStatus):
        self.__set("status", value)
        self.__status = value
    @property
    def examiner_id(self): return self.__examiner_id
    @examiner_id.setter
    def examiner_id(self, value: int | None):
        self.__set("examiner_id", value)
        self.__examiner_id = value
