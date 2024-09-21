from enums import BlockType
import sqlite3
from db import DATABASE
from utils import UserError, update_in_table, provide_cursor, value_exists

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
        cursor: sqlite3.Cursor,
    ):
        """
        Добавить задачу в таблицу problems
        """
        cursor.execute("SELECT id, name FROM problems WHERE olymp_id = ?", (olymp_id,))
        fetch = cursor.fetchall()
        for f in fetch:
            pr_id = f[0]
            if f[1] == name:
                raise UserError(f"Название {name} уже занято задачей {pr_id}")
        values = (olymp_id, name)
        cursor.execute("INSERT INTO problems(olymp_id, name) VALUES (?, ?)", values)
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
    def name(self): return self.__name
    @name.setter
    def name(self, value: str):
        self.__set("name", value)
        self.__name = value

class ProblemBlock:
    def __init__(
        self,
        id: int,
        olymp_id: int,
        problems: list[Problem] | list[int],
        block_type: BlockType | None = None,
        path: str | None = None
    ):
        if len(problems) != 3:
            raise ValueError("В блоке должно быть три задачи")
        self.__id: int = id
        self.__olymp_id: int = olymp_id
        if isinstance(problems[0], int):
            problems = [Problem.from_id(pr_id) for pr_id in problems]
        self.__problems: list[Problem] = problems
        self.__block_type: BlockType | None = block_type
        self.__path: str | None = path

    @classmethod
    def __columns_to_init_args(cls, *args):
        return args[:2] + [args[4:]] + args[2:4]

    @classmethod
    def from_id(cls, id: int):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM problem_blocks WHERE id = ?", (id,))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError("Блок задач не найден")
        args = cls.__columns_to_init_args(*fetch)
        return cls(*args)
    
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
        args = cls.__columns_to_init_args(*fetch)
        return cls(*args)  
    
    @classmethod
    @provide_cursor
    def create(
        cls,
        olymp_id: int,
        problems: list[Problem] | list[int],
        block_type: BlockType | None = None,
        path: str | None = None,
        *,
        cursor: sqlite3.Cursor,
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
                raise UserError(f"Блок типа {block_type} уже есть: {block_id}")
        if isinstance(problems[0], Problem):
            problems = [pr.id for pr in problems]
        values = [olymp_id, block_type, path] + problems
        cursor.execute("INSERT INTO problem_blocks(olymp_id, block_type, path, first_problem, second_problem, third_problem)"
                       "VALUES (?, ?, ?, ?, ?, ?)", tuple(values))
        cursor.connection.commit()
        created_id = cursor.lastrowid
        return cls(created_id, olymp_id, problems, block_type, path)

    def __set(self, column: str, value):
        update_in_table("problem_blocks", column, value, "id", self.__id)
    
    @property
    def id(self): return self.__id
    @property
    def olymp_id(self): return self.__olymp_id
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
    
