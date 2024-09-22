import sqlite3
from db import DATABASE
from utils import UserError, provide_cursor, value_exists, update_in_table
from queue_entry import QueueEntry, QueueStatus
from data import OWNER_HANDLE


class User:
    def __init__(
        self,
        user_id: int,
        tg_id: int | None,
        tg_handle: str,
        name: str,
        surname: str
    ):
        self.__user_id: int = user_id
        self.__tg_id: int | None = tg_id
        self.__tg_handle: str = tg_handle.lower()
        self.__name: str = name
        self.__surname: str = surname

    @classmethod
    @provide_cursor
    def create(
        cls,
        tg_handle: str,
        name: str,
        surname: str,
        *,
        tg_id: int | None = None,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить пользователя в таблицу users
        """
        tg_handle = tg_handle.lower()
        exists = value_exists("users", {"tg_handle": tg_handle})
        if exists:
            raise ValueError(f"Пользователь @{tg_handle} уже есть в базе")
        if tg_id:
            exists = value_exists("users", {"tg_id": tg_id})
            if exists:
                raise ValueError(f"Пользователь с Telegram ID {tg_id} уже есть в базе")
        cursor.execute(
            "INSERT INTO users(tg_id, tg_handle, name, surname) VALUES (?, ?, ?, ?)", 
            (tg_id, tg_handle, name, surname)
        )
        cursor.connection.commit()
        return cls.from_tg_handle(tg_handle)

    @classmethod
    def from_db(
        cls,
        *,
        user_id: int | None = None,
        tg_id: int | None = None,
        tg_handle: str | None = None,
        error_no_id_provided: str = "Требуется идентификатор пользователя",
        error_user_not_found: str | None = "Пользователь не найден в базе",
        error_ids_dont_match: str = "Данные идентификаторы не соответствуют"
    ):
        if tg_handle:
            tg_handle = tg_handle.lower()
        if tg_id:
            checked_column = "tg_id"
            given_value = tg_id
        elif user_id:
            checked_column = "user_id"
            given_value = user_id
        elif tg_handle:
            checked_column = "tg_handle"
            given_value = tg_handle
        else:
            raise ValueError(error_no_id_provided)
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            if not value_exists("users", {checked_column: given_value}, cursor=cur):
                if not error_user_not_found:
                    return None
                raise UserError(error_user_not_found)
            q = f"""
                SELECT
                    user_id,
                    tg_id,
                    tg_handle,
                    name,
                    surname
                FROM
                    users
                WHERE
                    {checked_column} = ?
                """
            cur.execute(q, (given_value,))
            fetched_base_values = list(cur.fetchone())
        user = cls(*fetched_base_values)
        if ((tg_id and user.tg_id != tg_id)
            or (user_id and user.user_id != user_id)
            or (tg_handle and user.tg_handle != tg_handle)):
            raise ValueError(error_ids_dont_match)
        return user

    @classmethod
    def from_user_id(cls, user_id: int):
        return cls.from_db(user_id=user_id)

    @classmethod
    def from_tg_id(cls, tg_id: int):
        return cls.from_db(tg_id=tg_id)
    
    @classmethod
    def from_tg_handle(cls, tg_handle: str):
        return cls.from_db(tg_handle=tg_handle)
    
    def __set(self, column: str, value):
        update_in_table("users", column, value, "user_id", self.__user_id)

    @property
    def user_id(self): return self.__user_id
    @property
    def tg_handle(self): return self.__tg_handle
    @tg_handle.setter
    def tg_handle(self, value: str):
        self.__set('tg_handle', value.lower())
        self.__tg_handle = value
    @property
    def tg_id(self): return self.__tg_id
    @tg_id.setter
    def tg_id(self, value: int):
        self.__set('tg_id', value)
        self.__tg_id = value
    @property
    def name(self): return self.__name
    @name.setter
    def name(self, value: int):
        self.__set('name', value)
        self.__name = value
    @property
    def surname(self): return self.__surname
    @surname.setter
    def surname(self, value: int):
        self.__set('surname', value)
        self.__surname = value


class OlympMember(User):
    def __init__(
        self,
        olymp_id: int,
        user_id: int,
        tg_id: int | None,
        tg_handle: int,
        name: str,
        surname: str,
        **additional_values
    ):
        super().__init__(user_id, tg_id, tg_handle, name, surname)
        self.__olymp_id: int = olymp_id
        self.__id: int | None = None
        self._additional_values = additional_values

    @classmethod
    def from_db(
        cls,
        olymp_id: int,
        table: str,
        additional_keys: list[str],
        *,
        user_id: int | None = None,
        tg_id: int | None = None,
        tg_handle: str | None = None,
        error_no_id_provided: str = "Требуется идентификатор пользователя",
        error_user_not_found: str | None = "Пользователь не найден в базе",
        error_ids_dont_match: str = "Данные идентификаторы не соответствуют"
    ):
        user = User.from_db(user_id=user_id, tg_id=tg_id, tg_handle=tg_handle,
                            error_no_id_provided=error_no_id_provided,
                            error_user_not_found=error_user_not_found,
                            error_ids_dont_match=error_ids_dont_match)
        if not user:
            return None
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            if not value_exists(table, {"user_id": user.user_id, "olymp_id": olymp_id}, cursor = cur):
                if not error_user_not_found:
                    return None
                raise UserError(error_user_not_found)
            q = f"""
                SELECT
                    {", ".join(additional_keys)}
                FROM
                    {table}
                WHERE
                    user_id = ?
                    AND olymp_id = ?
                """
            cur.execute(q, (user.user_id, olymp_id))
            additional_values = dict(zip(additional_keys, cur.fetchone()))
        return cls(
            olymp_id,
            user.user_id,
            user.tg_id,
            user.tg_handle,
            user.name,
            user.surname,
            **additional_values,
        )

    @classmethod
    def from_user_id(cls, user_id: int, olymp_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(olymp_id, user_id=user_id, error_user_not_found=None)
        return cls.from_db(olymp_id, user_id=user_id) 

    @classmethod
    def from_tg_id(cls, tg_id: int, olymp_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(olymp_id, tg_id=tg_id, error_user_not_found=None)
        return cls.from_db(olymp_id, tg_id=tg_id)

    @classmethod
    def from_tg_handle(cls, tg_handle: str, olymp_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(olymp_id, tg_handle=tg_handle, error_user_not_found=None)
        return cls.from_db(olymp_id, tg_handle=tg_handle)

    @classmethod
    def from_id(
        cls, 
        id: int, 
        table: str,
        *,
        error_user_not_found: str | None = "Пользователь не найден в базе",
    ):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT user_id, olymp_id FROM {table} WHERE id = ?", (id,))
            fetch = cur.fetchone()
            if fetch is None:
                if not error_user_not_found:
                    return None
                raise UserError(error_user_not_found)
            user_id, olymp_id = fetch
            return cls.from_user_id(user_id, olymp_id)

    def _queue_entry(self, id_column: str):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            q = (f"SELECT * FROM queue WHERE {id_column} = ?"
                 f"AND status IN ({', '.join(map(str, QueueStatus.active(as_numbers=True)))})")
            cur.execute(q, (self.__id,))
            fetch = cur.fetchone()
        if fetch is not None:
            return QueueEntry(*fetch)
        return None

    @property
    def olymp_id(self):
        return self.__olymp_id


class Participant(OlympMember):
    def __init__(
        self,
        olymp_id: int,
        user_id: int,
        tg_id: int | None,
        tg_handle: int,
        name: str,
        surname: str,
        grade: int,
        id: int
    ):
        super().__init__(olymp_id, user_id, tg_id, tg_handle, name, surname, id=id, grade=grade)
        self.__id: int = id
        self.__grade: int = grade

    
    @classmethod
    @provide_cursor
    def create_for_existing_user(
        cls,
        user: User | int,
        grade: int,
        olymp_id: int,
        *,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить пользователя как участника в таблицу participants
        """
        if isinstance(user, User):
            user_id = user.user_id
        else:
            user_id = user
        exists = value_exists("participants", {"user_id": user_id, "olymp_id": olymp_id})
        if exists:
            raise ValueError(f"Пользователь {user_id} уже участник олимпиады {olymp_id}")
        cursor.execute(
            "INSERT INTO participants(user_id, olymp_id, grade) VALUES (?, ?, ?)", 
            (user_id, olymp_id, grade)
        )
        cursor.connection.commit()
        return Participant.from_user_id(user_id, olymp_id)

    @classmethod
    @provide_cursor
    def create_as_new_user(
        cls,
        tg_handle: str,
        name: str,
        surname: str,
        grade: str,
        olymp_id: int,
        *,
        tg_id: int | None = None,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить пользователя в таблицу users и добавить его как участника в таблицу participants
        """
        user = User.create(tg_handle, name, surname, tg_id=tg_id, cursor=cursor)
        return Participant.create_for_existing_user(user, grade, olymp_id, cursor=cursor)
    
    @classmethod
    def from_db(
        cls,
        olymp_id: int,
        *,
        user_id: int | None = None,
        tg_id: int | None = None,
        tg_handle: str | None = None,
        error_user_not_found: str | None = ("Участник не найден. Если вы участник, "
                                            "авторизуйтесь при помощи команды /start.")
    ):
        participant = super().from_db(
            olymp_id,
            "participants", 
            ["id", "grade"], 
            user_id = user_id, 
            tg_id = tg_id,
            tg_handle = tg_handle,
            error_no_id_provided = "Требуется идентификатор участника",
            error_user_not_found = error_user_not_found
        )
        if not participant:
            return None
        participant.__id = participant._additional_values["id"]
        participant.__grade = participant._additional_values["grade"]
        return participant

    @classmethod
    def from_id(cls, id: int, no_error: bool = False):
        if no_error:
            return super().from_id(id, "participants", error_user_not_found=None)
        return super().from_id(id, "participants", error_user_not_found="Участник не найден")

    def display_data(self):
        return f"{self.name} {self.surname}, {self.grade} класс\nЕсли в данных есть ошибка, сообщи {OWNER_HANDLE}"

    @property
    def queue_entry(self):
        return self._queue_entry("participant_id")

    def __set(self, column: str, value):
        update_in_table("participants", column, value, "id", self.__id)
    
    @property
    def id(self): return self.__id
    @property
    def grade(self): return self.__grade
    @grade.setter
    def grade(self, value: int):
        self.__set('grade', value)
        self.__grade = value


class Examiner(OlympMember):
    def __init__(
        self,
        olymp_id: int,
        user_id: int,
        tg_id: int | None,
        tg_handle: int,
        name: str,
        surname: str,
        conference_link: str,
        busyness_level: int,
        is_busy: bool | int,
        id: int,
        problems: list[int] | str | None = None
    ):
        super().__init__(olymp_id, user_id, tg_id, tg_handle, name, surname,
                         id=id, conference_link=conference_link, problems=problems,
                         busyness_level=busyness_level, is_busy=is_busy)
        if isinstance(problems, str):
            problems = list(map(int, problems.split(",")))
        if problems is None:
            problems = []
        self.__id: int = id
        self.__conference_link: str = conference_link
        self.__problems: list[int] = problems
        self.__busyness_level: int = busyness_level
        self.__is_busy: bool = bool(is_busy)


    @classmethod
    @provide_cursor
    def create_for_existing_user(
        cls,
        user: User | int,
        conference_link: str,
        problems: list[int],
        olymp_id: int,
        *,
        busyness_level: int = 0,
        is_busy: bool = True,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить пользователя как принимающего в таблицу examiners
        """
        if isinstance(user, User):
            user_id = user.user_id
        else:
            user_id = user
        exists = value_exists("examiners", {"user_id": user_id, "olymp_id": olymp_id})
        if exists:
            raise ValueError(f"Пользователь {user_id} уже проверяющий в олимпиаде {olymp_id}")
        cursor.execute(
            "INSERT INTO examiners(user_id, olymp_id, conference_link, busyness_level, is_busy) VALUES (?, ?, ?, ?, ?)", 
            (user_id, olymp_id, conference_link, busyness_level, int(is_busy))
        )
        examiner_id = cursor.lastrowid
        q = "INSERT INTO examiner_problems(examiner_id, problem_id) VALUES " + ", ".join(["(?, ?)"] * len(problems))
        p = []
        for problem_id in problems:
            p += [examiner_id, problem_id]
        cursor.execute(q, tuple(p))
        cursor.connection.commit()
        return Examiner.from_user_id(user_id, olymp_id)

    @classmethod
    @provide_cursor
    def create_as_new_user(
        cls,
        tg_handle: str,
        name: str,
        surname: str,
        conference_link: str,
        problems: list[int],
        olymp_id: int,
        *,
        tg_id: int | None = None,
        busyness_level: int = 0,
        is_busy: bool = True,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить пользователя в таблицу users и добавить его как принимающего в таблицу examiners
        """
        user = User.create(tg_handle, name, surname, tg_id=tg_id, cursor=cursor)
        return Examiner.create_for_existing_user(
            user, conference_link, problems, olymp_id, 
            busyness_level=busyness_level, is_busy=is_busy, cursor=cursor
        )
    
    @classmethod
    def from_db(
        cls,
        olymp_id: int,
        *,
        user_id: int | None = None,
        tg_id: int | None = None,
        tg_handle: str | None = None,
        error_user_not_found: str | None = ("Принимающий не найден. Если вы принимающий, "
                                            "авторизуйтесь при помощи команды /start.")
    ):
        examiner = super().from_db(
            olymp_id,
            "examiners", 
            ["id", "conference_link", "busyness_level", "is_busy"], 
            user_id = user_id, 
            tg_id = tg_id,
            tg_handle = tg_handle,
            error_no_id_provided = "Требуется идентификатор принимающего",
            error_user_not_found = error_user_not_found
        )
        if not examiner:
            return None
        examiner.__id = examiner._additional_values["id"]
        examiner.__conference_link = examiner._additional_values["conference_link"]
        examiner.__busyness_level = examiner._additional_values["busyness_level"]
        examiner.__is_busy = bool(examiner._additional_values["is_busy"])
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT problem_id FROM examiner_problems WHERE examiner_id = ?", (examiner.__id,))
            result = cur.fetchall()
        examiner.__problems = [fetch[0] for fetch in result]
        return examiner

    @classmethod
    def from_id(cls, id: int, no_error: bool = False):
        if no_error:
            return super().from_id(id, "examiners", error_user_not_found=None)
        return super().from_id(id, "examiners", error_user_not_found="Принимающий не найден")

    def display_data(self):
        return f"{self.name} {self.surname}, ссылка: {self.conference_link}\nЕсли в данных есть ошибка, сообщи {OWNER_HANDLE}"
    
    @property
    def queue_entry(self):
        return self._queue_entry("examiner_id")
    
    def __set(self, column: str, value):
        update_in_table("examiners", column, value, "id", self.__id)

    @property
    def id(self): return self.__id
    @property
    def conference_link(self): return self.__conference_link
    @conference_link.setter
    def conference_link(self, value: str):
        self.__set('conference_link', value)
        self.__conference_link = value
    @property
    def problems(self): return self.__problems
    @problems.setter
    def problems(self, value: list[int]):
        self.__set('problems', ",".join(map(str, value)))
        self.problems = value
    @property
    def busyness_level(self): return self.__busyness_level
    @busyness_level.setter
    def busyness_level(self, value: int):
        self.__set('busyness_level', value)
        self.__busyness_level = value
    @property
    def is_busy(self): return self.__is_busy
    @is_busy.setter
    def is_busy(self, value: int):
        self.__set('is_busy', value)
        self.__is_busy = value
