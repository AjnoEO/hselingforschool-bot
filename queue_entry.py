import sqlite3
from statuses import QueueStatus
from utils import update_in_table

class QueueEntry:
    def __init__(
        self,
        id: int,
        olymp_id: int,
        participant_id: int,
        problem_id: int,
        status: QueueStatus | str,
        examiner_id: int | None
    ):
        self.__id: int = id
        self.__olymp_id: int = olymp_id
        self.__participant_id: int = participant_id
        self.__problem_id: int = problem_id
        self.__status: QueueStatus = QueueStatus(status) if isinstance(status, str) else status
        self.__examiner_id: int | None = examiner_id

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
