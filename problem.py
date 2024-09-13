import sqlite3
from db import DATABASE
from utils import UserError, update_in_table, provide_cursor, value_exists

class Problem:
    def __init__(
        self,
        id: int,
        olymp_id: int,
        junior_number: int | None,
        senior_number: int | None,
        name: str
    ):
        self.__id = id
        self.__olymp_id = olymp_id
        self.__junior_number = junior_number
        self.__senior_number = senior_number
        self.__name = name

    @classmethod
    def from_id(self, id: int):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM problems WHERE id = ?", (id,))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError("Задача не найдена")
        return Problem(*fetch)
        
    @classmethod
    def __from_number(cls, olymp_id: int, number_column: str, number_value: int):
        if not number_value:
            raise UserError(f"Необходимо указать номер задачи")
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM problems WHERE {number_column} = ?", (number_value,))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError(f"Задача номер {number_value} не найдена")
        return cls(*fetch)
    
    @classmethod
    def from_junior_number(cls, olymp_id: int, number: int):
        return cls.__from_number(olymp_id, "junior_no", number)
    
    @classmethod
    def from_senior_number(cls, olymp_id: int, number: int):
        return cls.__from_number(olymp_id, "senior_no", number)

    @classmethod
    @provide_cursor
    def create(
        cls,
        olymp_id: int,
        junior_no: int | None,
        senior_no: int | None,
        name: str,
        *,
        cursor: sqlite3.Cursor,
    ):
        """
        Добавить задачу в таблицу problems
        """
        cursor.execute("SELECT * FROM problems WHERE olymp_id = ?", (olymp_id,))
        fetch = cursor.fetchall()
        for f in fetch:
            pr_id = f[0]
            if f[1] == junior_no:
                UserError(f"Номер {junior_no} для младших классов уже занят задачей {pr_id}")
            if f[2] == senior_no:
                UserError(f"Номер {senior_no} для старших классов уже занят задачей {pr_id}")
            if f[3] == name:
                UserError(f"Название {name} уже занято задачей {pr_id}")
        values = (olymp_id, junior_no, senior_no, name)
        cursor.execute("INSERT INTO olymps(olymp_id, junior_no, senior_no, name) VALUES (?, ?, ?, ?)", values)
        cursor.connection.commit()
        created_id = cursor.lastrowid
        return cls(created_id, *values)

    def __set(self, column: str, value):
        update_in_table("problems", column, value, "id", self.__id)

    @property
    def id(self): return self.__id
    @property
    def olymp_id(self): return self.__olymp_id
    @property
    def junior_number(self): return self.__junior_number
    @junior_number.setter
    def junior_number(self, value: int):
        self.__set("junior_no", value)
        self.__junior_number = value
    @property
    def senior_number(self): return self.__senior_number
    @senior_number.setter
    def senior_number(self, value: int):
        self.__set("senior_no", value)
        self.__senior_number = value
    @property
    def name(self): return self.__name
    @name.setter
    def name(self, value: str):
        self.__set("name", value)
        self.__name = value
