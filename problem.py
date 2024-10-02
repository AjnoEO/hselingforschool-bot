import os
from enums import BlockType
import sqlite3
from db import DATABASE
from utils import UserError, update_in_table, provide_cursor
from telebot.formatting import escape_html

class Problem:
    def __init__(
        self,
        id: int,
        olymp_id: int,
        name: str
    ):
        self.__id = id
        self.__olymp_id = olymp_id
        self.__name = name

    @classmethod
    def from_id(cls, id: int):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM problems WHERE id = ?", (id,))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError("Задача не найдена")
        return cls(*fetch)
    
    @classmethod
    def from_name(cls, name: str, olymp_id: int, no_error: bool = False):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM problems WHERE name = ? AND olymp_id = ?", (name, olymp_id))
            fetch = cur.fetchone()
        if not fetch:
            if no_error:
                return None
            raise UserError("Задача не найдена")
        return cls(*fetch)
        
    # @classmethod
    # def __from_number(cls, olymp_id: int, number_column: str, number_value: int):
    #     if not number_value:
    #         raise UserError(f"Необходимо указать номер задачи")
    #     with sqlite3.connect(DATABASE) as conn:
    #         cur = conn.cursor()
    #         cur.execute(f"SELECT * FROM problems WHERE {number_column} = ?", (number_value,))
    #         fetch = cur.fetchone()
    #     if not fetch:
    #         raise UserError(f"Задача номер {number_value} не найдена")
    #     return cls(*fetch)

    @classmethod
    @provide_cursor
    def create(
        cls,
        olymp_id: int,
        name: str,
        *,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить задачу в таблицу problems
        """
        cursor.execute("SELECT id, name FROM problems WHERE olymp_id = ?", (olymp_id,))
        fetch = cursor.fetchall()
        for f in fetch:
            pr_id = f[0]
            if f[1] == name:
                raise UserError(f"Название <em>{escape_html(name)}</em> уже занято задачей <code>{pr_id}</code>")
        values = (olymp_id, name)
        cursor.execute("INSERT INTO problems(olymp_id, name) VALUES (?, ?)", values)
        cursor.connection.commit()
        created_id = cursor.lastrowid
        return cls(created_id, *values)


    @provide_cursor
    def get_blocks(self, *, cursor: sqlite3.Cursor | None = None):
        cursor.execute(
            "SELECT * FROM problem_blocks WHERE first_problem = ? OR second_problem = ? OR third_problem = ?",
            (self.id, self.id, self.id)
        )
        results = cursor.fetchall()
        return [ProblemBlock.from_columns(*fetch) for fetch in results]


    def __set(self, column: str, value):
        update_in_table("problems", column, value, "id", self.__id)

    @property
    def id(self): return self.__id
    @property
    def olymp_id(self): return self.__olymp_id
    @property
    def name(self): return self.__name
    @name.setter
    def name(self, value: str):
        problem = Problem.from_name(value, self.olymp_id, no_error=True)
        if problem:
            raise UserError(f"Название <em>{escape_html(problem.name)}</em> уже занято задачей <code>{problem.id}</code>")
        self.__set("name", value)
        self.__name = value

    def __eq__(self, other): return isinstance(other, self.__class__) and self.id == other.id

class ProblemBlock:
    def __init__(
        self,
        id: int,
        olymp_id: int,
        problems: list[Problem] | list[int],
        block_type: BlockType | int | None = None,
        path: str | None = None
    ):
        """! Для создания из результата запроса к БД используй `ProblemBlock.from_columns`"""
        if len(problems) != 3:
            raise ValueError("В блоке должно быть три задачи")
        self.__id: int = id
        self.__olymp_id: int = olymp_id
        if not isinstance(problems[0], Problem):
            problems = [Problem.from_id(pr_id) for pr_id in problems]
        self.__problems: list[Problem] = problems
        if isinstance(block_type, int):
            block_type = BlockType(block_type)
        self.__block_type: BlockType | None = block_type
        self.__path: str | None = path

    @classmethod
    def from_columns(cls, *args):
        args = list(args)
        return ProblemBlock(*args[:2], *[args[4:]], *args[2:4])

    @classmethod
    def from_id(cls, id: int):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM problem_blocks WHERE id = ?", (id,))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError("Блок задач не найден")
        return cls.from_columns(*fetch)
    
    @classmethod
    def from_block_type(
        cls,
        olymp_id: int,
        block_type: BlockType
    ):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM problem_blocks WHERE olymp_id = ? AND block_type = ?", (olymp_id, block_type))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError("Блок задач не найден")
        return cls.from_columns(*fetch)
    
    @classmethod
    @provide_cursor
    def create(
        cls,
        olymp_id: int,
        problems: list[Problem] | list[int],
        block_type: BlockType | None = None,
        path: str | None = None,
        *,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить блок задач в таблицу problem_blocks
        """
        if len(problems) != 3:
            raise ValueError("В блоке должно быть три задачи")
        if block_type is not None:
            cursor.execute("SELECT id FROM problem_blocks WHERE olymp_id = ? AND block_type = ?", (olymp_id, block_type))
            fetch = cursor.fetchone()
            if fetch is not None:
                block_id = fetch[0]
                raise UserError(f"{block_type} уже есть: <code>{block_id}</code>")
        if not isinstance(problems[0], int):
            problems = [pr.id for pr in problems]
        values = [olymp_id, block_type, path] + problems
        cursor.execute("INSERT INTO problem_blocks(olymp_id, block_type, path, first_problem, second_problem, third_problem)"
                       "VALUES (?, ?, ?, ?, ?, ?)", tuple(values))
        cursor.connection.commit()
        created_id = cursor.lastrowid
        return cls(created_id, olymp_id, problems, block_type, path)
    

    def delete_file(self):
        if not self.path:
            raise UserError("Файла уже нет")
        os.remove(self.path)
        self.path = None

    @provide_cursor
    def delete(self, *, cursor: sqlite3.Cursor | None = None):
        self.delete_file()
        cursor.execute("DELETE FROM problem_blocks WHERE id = ?", (self.id,))


    def __set(self, column: str, value):
        update_in_table("problem_blocks", column, value, "id", self.__id)
    
    @property
    def id(self): return self.__id
    @property
    def olymp_id(self): return self.__olymp_id
    @property
    def problems(self): return self.__problems
    @property
    def block_type(self): return self.__block_type
    @block_type.setter
    def block_type(self, value: BlockType | None):
        self.__set("block_type", value)
        self.__block_type = value
    @property
    def path(self): return self.__path
    @path.setter
    def path(self, value: str | None):
        self.__set("path", value)
        self.__path = value
    
