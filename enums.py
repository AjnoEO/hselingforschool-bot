from enum import Enum
import sqlite3


class SqliteCompatibleEnum(Enum):
    def __conform__(self, protocol):
        if protocol is sqlite3.PrepareProtocol:
            return self.value


class OlympStatus(SqliteCompatibleEnum):
    TBA = 0
    REGISTRATION = 1
    CONTEST = 2
    QUEUE = 3
    RESULTS = 4

        
class QueueStatus(SqliteCompatibleEnum):
    WAITING = 0
    CANCELED = 1
    DISCUSSING = 2
    SUCCESS = 3
    FAIL = 4

    @classmethod
    def active(cls, *, as_numbers: bool = False):
        active_statuses = [cls.WAITING, cls.DISCUSSING]
        if as_numbers:
            return [s.value for s in active_statuses]
        else:
            return active_statuses


class BlockType(Enum):
    JUNIOR_1 = 0
    JUNIOR_2 = 1
    JUNIOR_3 = 2
    SENIOR_1 = 3
    SENIOR_2 = 4
    SENIOR_3 = 5