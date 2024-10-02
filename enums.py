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

    __DESCRIPTIONS = ["ожидание", "отмена", "обсуждение", "принято", "не принято"]

    @classmethod
    def active(cls, *, as_numbers: bool = False):
        active_statuses = [cls.WAITING, cls.DISCUSSING]
        if as_numbers:
            return [s.value for s in active_statuses]
        else:
            return active_statuses
        
    @classmethod
    def from_text(cls, text: str, no_error: bool = False):
        text = text.lower().strip()
        if text in cls.__DESCRIPTIONS:
            return cls(cls.__DESCRIPTIONS.index(text))
        if no_error:
            return None
        raise ValueError(f"Неизвестный статус сдачи: {text}")

    def __str__(self) -> str:
        return self.__DESCRIPTIONS[self.value].capitalize()


class BlockType(SqliteCompatibleEnum):
    JUNIOR_1 = 0
    JUNIOR_2 = 1
    JUNIOR_3 = 2
    SENIOR_1 = 3
    SENIOR_2 = 4
    SENIOR_3 = 5

    @property
    def is_junior(self) -> bool: return self.value < 3
    
    @property
    def is_senior(self) -> bool: return not self.is_junior
    
    @property
    def number(self) -> int: return self.value % 3 + 1
    
    def __str__(self) -> str:
        return f"{self.number} блок для {'младших' if self.is_junior else 'старших'}"
