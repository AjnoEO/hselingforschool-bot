from sqlite3 import Cursor
from db import db_connection
from utils import UserError, decline, provide_cursor, value_exists, update_in_table
from tag import Tag
from enums import OlympStatus, Platform
from queue_entry import QueueEntry, QueueStatus
from problem import Problem, ProblemBlock, BlockType
from data import OWNER_HANDLE
from telebot.formatting import escape_html


class User:
    def __init__(
        self,
        user_id: int,
        platform: Platform,
        external_id: int | None,
        handle: str,
        name: str,
        surname: str,
        tags: list[int],
    ):
        self.__user_id: int = user_id
        self.__platform: Platform = platform if isinstance(platform, Platform) else Platform(platform)
        self.__external_id: int | None = external_id
        self.__handle: str = self.conform_handle(handle)
        self.__name: str = name
        self.__surname: str = surname
        self.__tags: list[int] = tags

    @classmethod
    @provide_cursor
    def create(
        cls,
        handle: str,
        name: str,
        surname: str,
        *,
        platform: Platform = Platform.TELEGRAM,
        external_id: int | None = None,
        tags: list[int] | None = None,
        ok_if_exists: bool = False,
        cursor: Cursor | None = None,
    ):
        """
        Добавить пользователя в таблицу users
        """
        platform = platform if isinstance(platform, Platform) else Platform(platform)
        handle = cls.conform_handle(handle)
        exists = value_exists("users", {"platform": platform, "handle": handle})
        if exists and not ok_if_exists:
            raise UserError(f"Пользователь @{handle} уже есть в базе")
        if external_id is not None:
            exists = exists or value_exists("users", {"platform": platform, "external_id": external_id})
            if exists and not ok_if_exists:
                raise UserError(f"Пользователь с ID {external_id} уже есть в базе")
        if not exists:
            cursor.execute(
                "INSERT INTO users(platform, external_id, handle, name, surname) VALUES (?, ?, ?, ?, ?)",
                (platform, external_id, handle, name, surname)
            )
        elif external_id is not None:
            cursor.execute(
                "UPDATE users SET external_id = ?, name = ?, surname = ? WHERE platform = ? AND handle = ?",
                (external_id, name, surname, platform, handle)
            )
        else:
            cursor.execute(
                "UPDATE users SET name = ?, surname = ? WHERE platform = ? AND handle = ?",
                (name, surname, platform, handle)
            )
        cursor.execute("SELECT user_id FROM users WHERE platform = ? AND handle = ?", (platform, handle))
        user_id = cursor.fetchone()[0]
        if tags and len(tags) > 0:
            q = "INSERT INTO user_tags(user_id, tag_id) VALUES " + ", ".join(["(?, ?)"] * len(tags))
            t = []
            for tag_id in tags:
                t += [user_id, tag_id]
            cursor.execute(q, tuple(t))
        cursor.connection.commit()
        return cls.from_handle(platform, handle)

    @classmethod
    def from_db(
        cls,
        *,
        user_id: int | None = None,
        platform: Platform | None = None,
        external_id: int | None = None,
        handle: str | None = None,
        error_no_id_provided: str = "Требуется идентификатор пользователя",
        error_user_not_found: str | None = "Пользователь не найден в базе",
        error_ids_dont_match: str = "Данные идентификаторы не соответствуют"
    ):
        if platform is not None and not isinstance(platform, Platform):
            platform = Platform(platform)
        if external_id is not None:
            if platform is None:
                raise ValueError("Для поиска по ID мессенджера необходимо указать платформу")
            conditions = {"platform": platform, "external_id": external_id}
        elif handle is not None:
            if platform is None:
                raise ValueError("Для поиска по хэндлу необходимо указать платформу")
            handle = cls.conform_handle(handle)
            conditions = {"platform": platform, "handle": handle}
        elif user_id is not None:
            conditions = {"user_id": user_id}
        else:
            raise ValueError(error_no_id_provided)
        with db_connection() as conn:
            cur = conn.cursor()
            if not value_exists("users", conditions, cursor=cur):
                if not error_user_not_found:
                    return None
                raise UserError(error_user_not_found)
            q = (
                "SELECT user_id, platform, external_id, handle, name, surname FROM users WHERE "
                + " AND ".join(f"{column} = ?" for column in conditions)
            )
            cur.execute(q, tuple(conditions.values()))
            fetched_base_values = list(cur.fetchone())
        user = cls(*fetched_base_values, tags=[])
        if ((user_id is not None and user.user_id != user_id)
            or (platform is not None and user.platform != platform)
            or (external_id is not None and user.external_id != external_id)
            or (handle is not None and user.handle != handle)):
            raise ValueError(error_ids_dont_match)
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT tag_id FROM user_tags WHERE user_id = ?", (user.__user_id,))
            result = cur.fetchall()
        user.__tags = [fetch[0] for fetch in result]
        return user

    @classmethod
    def from_user_id(cls, user_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(user_id=user_id, error_user_not_found=None)
        return cls.from_db(user_id=user_id)

    @classmethod
    def from_external_id(cls, platform: Platform, external_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(platform=platform, external_id=external_id, error_user_not_found=None)
        return cls.from_db(platform=platform, external_id=external_id)

    @classmethod
    def from_handle(cls, platform: Platform, handle: str, no_error: bool = False):
        if no_error:
            return cls.from_db(platform=platform, handle=handle, error_user_not_found=None)
        return cls.from_db(platform=platform, handle=handle)

    @staticmethod
    def conform_handle(handle: str):
        handle = handle.lower().strip()
        if handle.startswith("@"):
            handle = handle[1:]
        return handle


    @provide_cursor
    def remove(self, *, cursor: Cursor | None = None):
        cursor.execute("DELETE FROM users WHERE user_id = ?", (self.user_id,))


    def conflate_with(self, new_user: 'User'):
        if not self.external_id and new_user.external_id:
            self, new_user = new_user, self
        if self.platform != new_user.platform:
            raise UserError("Нельзя объединить пользователей разных платформ")
        new_name = new_user.name
        new_surname = new_user.surname
        new_handle = new_user.handle
        new_tags = new_user.tags
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE participants SET user_id = ? WHERE user_id = ?", (self.user_id, new_user.user_id))
            cur.execute("UPDATE examiners SET user_id = ? WHERE user_id = ?", (self.user_id, new_user.user_id))
            new_user.remove(cursor=cur)
            conn.commit()
        self.name = new_name
        self.surname = new_surname
        self.handle = new_handle
        self.set_tags(new_tags)
    

    def display_handle(self, hide_id: bool = False) -> str:
        return (
            (f"Без хэндла" if hide_id else f"ID: <code>{self.handle}</code>") if self.handle.isnumeric()
            else f"@{self.handle}"
        )
    
    def display_tags(self, verbose: bool = False, hide_name: bool = True, sep: str = "\n", end: str = "") -> str:
        result = ""
        if verbose:
            amount = len(self.tags)
            if amount == 0:
                return "Тэгов нет"
            result = f"{amount} {decline(amount, 'тэг', ('', 'а', 'ов'))}:\n"
        tag_list = []
        for tag_id in self.tags:
            tag = Tag.from_id(tag_id)
            if not hide_name:
                tag_list.append(f"<code>{escape_html(tag.name)}</code>: {tag}")
            else:
                tag_list.append(str(tag))
        if tag_list:
            return result + sep.join(tag_list) + end
        else:
            return result + end

    def add_tag(self, tag: Tag | int):
        if isinstance(tag, Tag):
            tag = tag.id
        if tag in self.tags:
            raise ValueError(f"У пользователя {self.user_id} уже есть тэг {tag}")
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO user_tags(user_id, tag_id) VALUES (?, ?)", (self.user_id, tag))
            conn.commit()
        self.__tags.append(tag)

    def remove_tag(self, tag: Tag | int):
        if isinstance(tag, Tag):
            tag = tag.id
        if tag not in self.tags:
            raise ValueError(f"У пользователя {self.user_id} уже нет тэга {tag}")
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM user_tags WHERE user_id = ? AND tag_id = ?", (self.user_id, tag))
            conn.commit()
        self.__tags.remove(tag)

    def set_tags(self, tags: list[Tag] | list[int] | None):
        if tags is None:
            tags = []
        elif tags and isinstance(tags[0], Tag):
            tags = [tag.id for tag in tags]
        remove = []
        add = []
        for tag in self.tags:
            if tag not in tags:
                remove.append(tag)
        for tag in tags:
            if tag not in self.tags:
                add.append(tag)
        with db_connection() as conn:
            cur = conn.cursor()
            for tag in remove:
                cur.execute("DELETE FROM user_tags WHERE user_id = ? AND tag_id = ?", (self.user_id, tag))
            for tag in add:
                cur.execute("INSERT INTO user_tags(user_id, tag_id) VALUES (?, ?)", (self.user_id, tag))
            conn.commit()
        self.__tags = tags


    def __set(self, column: str, value):
        update_in_table("users", column, value, "user_id", self.__user_id)

    
    def __str__(self):
        return f"{self.full_name} ({self.display_handle()})"


    @property
    def user_id(self): return self.__user_id
    @property
    def handle(self): return self.__handle
    @handle.setter
    def handle(self, value: str):
        value = self.conform_handle(value)
        self.__set('handle', value)
        self.__handle = value
    @property
    def external_id(self): return self.__external_id
    @external_id.setter
    def external_id(self, value: int | None):
        self.__set('external_id', value)
        self.__external_id = value
    @property
    def platform(self): return self.__platform
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
    @property
    def full_name(self): return f"{self.name} {self.surname}"
    @property
    def tags(self): return self.__tags

    @property
    def is_authenticated(self) -> bool:
        return self.external_id is not None


class OlympMember(User):
    def __init__(
        self,
        olymp_id: int,
        user_id: int,
        platform: Platform,
        external_id: int | None,
        handle: str,
        name: str,
        surname: str,
        tags: list[int],
        **additional_values
    ):
        super().__init__(user_id, platform, external_id, handle, name, surname, tags)
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
        platform: Platform | None = None,
        external_id: int | None = None,
        handle: str | None = None,
        error_no_id_provided: str = "Требуется идентификатор пользователя",
        error_user_not_found: str | None = "Пользователь не найден в базе",
        error_ids_dont_match: str = "Данные идентификаторы не соответствуют"
    ):
        user = User.from_db(user_id=user_id, platform=platform, external_id=external_id, handle=handle,
                            error_no_id_provided=error_no_id_provided,
                            error_user_not_found=error_user_not_found,
                            error_ids_dont_match=error_ids_dont_match)
        if not user:
            return None
        with db_connection() as conn:
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
            user.platform,
            user.external_id,
            user.handle,
            user.name,
            user.surname,
            user.tags,
            **additional_values,
        )

    @classmethod
    def from_user_id(cls, user_id: int, olymp_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(olymp_id, user_id=user_id, error_user_not_found=None)
        return cls.from_db(olymp_id, user_id=user_id) 

    @classmethod
    def from_external_id(cls, platform: Platform, external_id: int, olymp_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(olymp_id, platform=platform, external_id=external_id, error_user_not_found=None)
        return cls.from_db(olymp_id, platform=platform, external_id=external_id)

    @classmethod
    def from_handle(cls, platform: Platform, handle: str, olymp_id: int, no_error: bool = False):
        if no_error:
            return cls.from_db(olymp_id, platform=platform, handle=handle, error_user_not_found=None)
        return cls.from_db(olymp_id, platform=platform, handle=handle)

    @classmethod
    def from_id(
        cls, 
        id: int, 
        table: str,
        *,
        error_user_not_found: str | None = "Пользователь не найден в базе",
    ):
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT user_id, olymp_id FROM {table} WHERE id = ?", (id,))
            fetch = cur.fetchone()
            if fetch is None:
                if not error_user_not_found:
                    return None
                raise UserError(error_user_not_found)
            user_id, olymp_id = fetch
            return cls.from_user_id(user_id, olymp_id)

    @classmethod
    def create_for_existing_user(
        cls,
        *args,
        **kwargs
    ):
        if cls == OlympMember:
            raise TypeError("Метод `create_for_existing_user` предназначен для использования с классами `Participant` и `Examiner`")
        cls.create_for_existing_user(*args, **kwargs)

    @classmethod
    def create_as_new_user(
        cls,
        *args,
        **kwargs
    ):
        if cls == OlympMember:
            raise TypeError("Метод `create_as_new_user` предназначен для использования с классами `Participant` и `Examiner`")
        cls.create_as_new_user(*args, **kwargs)

    def display_data(
        self, verbose: bool = False, olymp_status: OlympStatus | None = None, 
        technical_info: bool = False, contact_note: bool = True
    ):
        if type(self) == OlympMember:
            raise TypeError("Метод `display_data` предназначен для использования с классами `Participant` и `Examiner`")
        self.display_data(verbose, olymp_status, technical_info, contact_note)

    def _queue_entry(self, id_column: str):
        with db_connection() as conn:
            cur = conn.cursor()
            q = (f"SELECT * FROM queue WHERE {id_column} = ? "
                 f"AND status IN ({', '.join(map(str, QueueStatus.active(as_numbers=True)))})")
            cur.execute(q, (self.id,))
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
        platform: Platform,
        external_id: int | None,
        handle: str,
        name: str,
        surname: str,
        tags: list[int],
        grade: int,
        last_block_number: int,
        finished: bool | int,
        id: int
    ):
        super().__init__(
            olymp_id, user_id, platform, external_id, handle, name, surname, tags,
            id=id, grade=grade, last_block_number=last_block_number, finished=finished
        )
        self.__id: int = id
        self.__grade: int = grade
        self.__last_block_number: int = last_block_number
        self.__finished: bool = bool(finished)

    
    @classmethod
    @provide_cursor
    def create_for_existing_user(
        cls,
        user: User | int,
        grade: int,
        olymp_id: int,        
        *,
        last_block_number: int | None = None,
        finished: bool = False,
        ok_if_exists: bool = False,
        cursor: Cursor | None = None,
    ) -> Participant:
        """
        Добавить пользователя как участника в таблицу participants
        """
        if isinstance(user, User):
            user_id = user.user_id
        else:
            user_id = user
        exists = value_exists("participants", {"user_id": user_id, "olymp_id": olymp_id})
        if exists and not ok_if_exists:
            raise UserError(f"Пользователь {user_id} уже участник олимпиады {olymp_id}")
        if exists:
            q = (f"UPDATE participants SET grade = ?, finished = ?"
                 f"{', last_block_number = ?' if last_block_number else ''} "
                 f"WHERE olymp_id = ? AND user_id = ?")
            p = [grade, finished] + ([last_block_number] if last_block_number else []) + [olymp_id, user_id]
        else:
            p = [user_id, olymp_id, grade, finished] + ([last_block_number] if last_block_number else [])
            q = (f"INSERT INTO participants(user_id, olymp_id, grade, finished"
                f"{', last_block_number' if last_block_number else ''}) VALUES "
                f"({', '.join(['?']*len(p))})")
        cursor.execute(q, tuple(p))
        cursor.connection.commit()
        return Participant.from_user_id(user_id, olymp_id)

    @classmethod
    @provide_cursor
    def create_as_new_user(
        cls,
        handle: str,
        name: str,
        surname: str,
        grade: str,
        olymp_id: int,
        *,
        platform: Platform = Platform.TELEGRAM,
        external_id: int | None = None,
        tags: list[int] | None = None,
        last_block_number: int | None = None,
        finished: bool = False,
        ok_if_user_exists: bool = False,
        ok_if_exists: bool = False,
        cursor: Cursor | None = None,
    ):
        """
        Добавить пользователя в таблицу users и добавить его как участника в таблицу participants
        """
        user = User.create(
            handle, name, surname, platform=platform, external_id=external_id,
            tags=tags, ok_if_exists=ok_if_user_exists, cursor=cursor
        )
        return Participant.create_for_existing_user(
            user, grade, olymp_id, last_block_number=last_block_number, finished=finished,
            ok_if_exists=ok_if_exists, cursor=cursor
        )
    
    @classmethod
    def from_db(
        cls,
        olymp_id: int,
        *,
        user_id: int | None = None,
        platform: Platform | None = None,
        external_id: int | None = None,
        handle: str | None = None,
        error_user_not_found: str | None = ("Участник не найден. Если ты участник, "
                                            "авторизуйся при помощи команды /start.")
    ):
        participant = super().from_db(
            olymp_id,
            "participants", 
            ["id", "grade", "last_block_number", "finished"], 
            user_id = user_id, 
            platform = platform,
            external_id = external_id,
            handle = handle,
            error_no_id_provided = "Требуется идентификатор участника",
            error_user_not_found = error_user_not_found
        )
        if not participant:
            return None
        participant.__id = participant._additional_values["id"]
        participant.__grade = participant._additional_values["grade"]
        participant.__last_block_number = participant._additional_values["last_block_number"]
        participant.__finished = bool(participant._additional_values["finished"])
        return participant

    @classmethod
    def from_id(cls, id: int, no_error: bool = False) -> (Participant | None):
        if no_error:
            return super().from_id(id, "participants", error_user_not_found=None)
        return super().from_id(id, "participants", error_user_not_found="Участник не найден")


    def display_data(
        self, verbose: bool = False, olymp_status: OlympStatus | None = None, 
        technical_info: bool = False, contact_note: bool = True
    ):
        response = f"{self.name} {self.surname}, {self.grade} класс"
        if technical_info:
            response += f" ({self.display_handle()})"
        display_tags = self.display_tags(verbose = verbose, hide_name = not technical_info)
        if display_tags: response += "\n" + display_tags
        if technical_info:
            response += f"\nАвторизация " + ("пройдена" if self.is_authenticated else "не пройдена")
            response += (
                "\nУчастие завершено"
                if self.finished or olymp_status == OlympStatus.RESULTS else 
                "\nУчаствует"
            )
        if contact_note: response += f"\nЕсли в данных есть ошибка, сообщи {OWNER_HANDLE}"
        return response
    
    def problem_block_from_number(self, number: int):
        block_type = BlockType[('JUNIOR' if self.is_junior else 'SENIOR') + '_' + str(number)]
        return ProblemBlock.from_block_type(self.olymp_id, block_type)
    
    def problem_from_number(self, number: int):
        number = number - 1
        block_number, problem_number = number // 3 + 1, number % 3
        problem_block = self.problem_block_from_number(block_number)
        return problem_block.problems[problem_number]
    
    def has_problem(self, problem: Problem | int):
        if isinstance(problem, Problem):
            for block_number in range(1, self.last_block_number + 1):
                block = self.problem_block_from_number(block_number)
                if problem in block.problems:
                    return True
            return False
        else:
            return self.last_block_number * 3 >= problem

    def get_problem_number(self, problem: Problem):
        for block_number in range(1, self.last_block_number + 1):
            block = self.problem_block_from_number(block_number)
            if problem not in block.problems:
                continue
            problem_number = block.problems.index(problem)
            return (block_number - 1) * 3 + problem_number + 1
        raise UserError(f"Задача {problem.id} не дана участнику {self.id}")

    def should_get_new_problem(self, problem: Problem | int):
        if not self.has_problem(problem):
            raise UserError("Задача недоступна")
        if self.last_block_number == 3:
            return False
        if isinstance(problem, Problem):
            problem = self.get_problem_number(problem)
        return (self.last_block_number == (problem - 1) // 3 + 1)
    
    def give_next_problem_block(self):
        if self.last_block_number >= 3:
            raise ValueError("Участник уже получил все блоки задач")
        self.last_block_number += 1
        return self.last_block
    
    def problems(self):
        problem_list: list[Problem] = []
        for problem_block_number in range(1, self.last_block_number + 1):
            problem_block = self.problem_block_from_number(problem_block_number)
            problem_list += problem_block.problems
        return problem_list

    def join_queue(self, problem: Problem | int):
        if not self.has_problem(problem):
            raise UserError("Задача недоступна")
        
        if isinstance(problem, int):
            problem = self.problem_from_number(problem)

        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO queue(olymp_id, participant_id, problem_id) VALUES (?, ?, ?)", (self.olymp_id, self.id, problem.id))
            conn.commit()
            queue_entry = QueueEntry(cur.lastrowid, self.olymp_id, self.id, problem.id)

        examiner_id = queue_entry.look_for_examiner()
        if examiner_id:
            examiner: Examiner = Examiner.from_id(examiner_id)
            examiner.assign_to_queue_entry(queue_entry)
        return queue_entry

    def solved(self, problem: Problem | int):
        if isinstance(problem, int):
            problem = self.problem_from_number(problem)
        return value_exists("queue", {"participant_id": self.id, "problem_id": problem.id, "status": QueueStatus.SUCCESS})

    def attempts_left(self, problem: Problem | int):
        """
        Считает, сколько осталось попыток. Потраченными попытками считаются только неуспешные сдачи,
        успешные попытки не учитываются. Например, если участник сдал задачу со второй попытки,
        он потратил две попытки из трёх, но метод `Participant.attempts_left` вернёт `2`.
        """
        if isinstance(problem, int):
            problem = self.problem_from_number(problem)
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM queue WHERE participant_id = ? AND problem_id = ? AND status = ?", 
                (self.id, problem.id, QueueStatus.FAIL)
            )
            fetch = cur.fetchall()
        if not fetch:
            return 3
        return 3 - len(fetch)

    def results(self) -> tuple[int, list[tuple[Problem, bool, int]]]:
        """
        Подсчитывает все результаты. Возвращает (`sum`, [(`problem`, `is_succesful`, `attempts_left_or_points`)])
        """
        sum = 0
        detailed_results: list[tuple[Problem, bool, int]] = []
        for i, problem in enumerate(self.problems()):
            attempts_left_or_points = self.attempts_left(problem)
            if is_successful := self.solved(problem):
                attempts_left_or_points = (2+(i//3)*2) + attempts_left_or_points
                sum += attempts_left_or_points
            detailed_results.append((problem, is_successful, attempts_left_or_points))
        return (sum, detailed_results)


    @property
    def queue_entry(self) -> QueueEntry | None:
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
    @property
    def last_block_number(self): return self.__last_block_number
    @last_block_number.setter
    def last_block_number(self, value: int):
        self.__set('last_block_number', value)
        self.__last_block_number = value
    @property
    def finished(self): return self.__finished
    @finished.setter
    def finished(self, value: bool):
        self.__set('finished', value)
        self.__finished = value
    @property
    def is_junior(self): return (self.grade < 10)
    @property
    def is_senior(self): return not self.is_junior
    @property
    def last_block(self): return self.problem_block_from_number(self.last_block_number)


class Examiner(OlympMember):
    def __init__(
        self,
        olymp_id: int,
        user_id: int,
        platform: Platform,
        external_id: int | None,
        handle: str,
        name: str,
        surname: str,
        tags: list[int],
        conference_link: str,
        busyness_level: int,
        is_busy: bool | int,
        id: int,
        problems: list[int] | str | None = None
    ):
        super().__init__(olymp_id, user_id, platform, external_id, handle, name, surname, tags,
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
        olymp_id: int,
        *,
        problems: list[int] | None = None,
        busyness_level: int = 0,
        is_busy: bool = True,
        ok_if_exists: bool = False,
        cursor: Cursor | None = None,
    ) -> Examiner:
        """
        Добавить пользователя как принимающего в таблицу examiners
        """
        if isinstance(user, User):
            user_id = user.user_id
        else:
            user_id = user
        exists = value_exists("examiners", {"user_id": user_id, "olymp_id": olymp_id})
        if exists and not ok_if_exists:
            raise UserError(f"Пользователь {user_id} уже проверяющий в олимпиаде {olymp_id}")
        if exists:
            cursor.execute(
                "UPDATE examiners SET conference_link = ?, busyness_level = ?, is_busy = ? WHERE olymp_id = ? AND user_id = ?",
                (conference_link, busyness_level, int(is_busy), olymp_id, user_id)
            )
        else:
            cursor.execute(
                "INSERT INTO examiners(user_id, olymp_id, conference_link, busyness_level, is_busy) VALUES (?, ?, ?, ?, ?)",
                (user_id, olymp_id, conference_link, busyness_level, int(is_busy))
            )
        examiner_id = cursor.lastrowid
        if problems and len(problems) > 0:
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
        handle: str,
        name: str,
        surname: str,
        conference_link: str,
        olymp_id: int,
        *,
        platform: Platform = Platform.TELEGRAM,
        external_id: int | None = None,
        tags: list[int] | None = None,
        problems: list[int] | None = None,
        busyness_level: int = 0,
        is_busy: bool = True,
        ok_if_user_exists: bool = False,
        ok_if_exists: bool = False,
        cursor: Cursor | None = None,
    ):
        """
        Добавить пользователя в таблицу users и добавить его как принимающего в таблицу examiners
        """
        user = User.create(
            handle, name, surname, platform=platform, external_id=external_id,
            tags=tags, ok_if_exists=ok_if_user_exists, cursor=cursor
        )
        return Examiner.create_for_existing_user(
            user, conference_link, olymp_id, problems=problems,
            busyness_level=busyness_level, is_busy=is_busy, ok_if_exists=ok_if_exists, cursor=cursor
        )
    
    @classmethod
    def from_db(
        cls,
        olymp_id: int,
        *,
        user_id: int | None = None,
        platform: Platform | None = None,
        external_id: int | None = None,
        handle: str | None = None,
        error_user_not_found: str | None = ("Принимающий не найден. Если ты принимающий, "
                                            "авторизуйся при помощи команды /start.")
    ):
        examiner = super().from_db(
            olymp_id,
            "examiners", 
            ["id", "conference_link", "busyness_level", "is_busy"], 
            user_id = user_id, 
            platform = platform,
            external_id = external_id,
            handle = handle,
            error_no_id_provided = "Требуется идентификатор принимающего",
            error_user_not_found = error_user_not_found
        )
        if not examiner:
            return None
        examiner.__id = examiner._additional_values["id"]
        examiner.__conference_link = examiner._additional_values["conference_link"]
        examiner.__busyness_level = examiner._additional_values["busyness_level"]
        examiner.__is_busy = bool(examiner._additional_values["is_busy"])
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT problem_id FROM examiner_problems WHERE examiner_id = ?", (examiner.__id,))
            result = cur.fetchall()
        examiner.__problems = [fetch[0] for fetch in result]
        return examiner

    @classmethod
    def from_id(cls, id: int, no_error: bool = False) -> (Examiner | None):
        if no_error:
            return super().from_id(id, "examiners", error_user_not_found=None)
        return super().from_id(id, "examiners", error_user_not_found="Принимающий не найден")


    def display_problem_data(self):
        amount = len(self.problems)
        if amount == 0:
            return "Задач не выбрано"
        result = f"{decline(amount, 'Выбран', ('а', 'о', 'о'))} {amount} {decline(amount, 'задач', ('а', 'и', ''))}:"
        for problem_id in self.problems:
            problem = Problem.from_id(problem_id)
            result += f"\n- <em>{escape_html(problem.name)}</em>"
        return result
    
    def display_data(
        self, verbose: bool = False, olymp_status: OlympStatus | None = None, 
        technical_info: bool = False, contact_note: bool = True
    ):
        response = f"{self.name} {self.surname}"
        if technical_info:
            response += f" ({self.display_handle()})"
        display_tags = self.display_tags(verbose = verbose, hide_name = not technical_info)
        if display_tags: response += "\n" + display_tags
        if technical_info:
            response += f"\nАвторизация " + ("пройдена" if self.is_authenticated else "не пройдена")
        response += f"\nCсылка на конференцию: {self.conference_link}"
        if verbose:
            if not olymp_status:
                raise ValueError("При verbose=True необходимо указать olymp_status")
            response += "\n" + self.display_problem_data()
            if olymp_status in [OlympStatus.CONTEST, OlympStatus.QUEUE]:
                response += f"\nСтатус: {'занят(-а)' if self.is_busy else 'свободен(-на)'}"
            if olymp_status in [OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]:
                response += f"\nОбсуждений с участниками: {self.busyness_level}"
        if contact_note: response += f"\nЕсли в данных есть ошибка, сообщи {OWNER_HANDLE}"
        return response
    
    def assign_to_queue_entry(self, queue_entry: QueueEntry):
        if self.queue_entry:
            raise UserError(f"Принимающий {self.id} уже есть в очереди (запись {self.queue_entry.id})")
        if queue_entry.status != QueueStatus.WAITING:
            raise UserError(f"Нельзя записать принимающего в очередь на запись не со статусом ожидания")
        queue_entry.examiner_id = self.id
        queue_entry.status = QueueStatus.DISCUSSING
        self.is_busy = True
        self.busyness_level += 1

    def withdraw_from_queue_entry(self):
        if not self.queue_entry:
            raise UserError(f"Принимающий {self.id} не в очереди")
        queue_entry = self.queue_entry
        if queue_entry.status != QueueStatus.DISCUSSING:
            raise UserError("Нельзя списать принимающего с уже завершённой записи")
        queue_entry.status = QueueStatus.WAITING
        queue_entry.examiner_id = None
        self.busyness_level -= 1

    def look_for_queue_entry(self):
        """
        Найти подходящую запись в очереди. Если найдена, возвращает её как `QueueEntry`, если нет — возвращает `None`.

        Само `QueueEntry` не меняется! Принимающий не назначается!
        Чтобы назначить принимающего, используй метод `Examiner.assign_to_queue_entry`
        """
        if self.queue_entry:
            raise ValueError(f"Принимающий {self.id} уже есть в очереди (запись {self.queue_entry.id})")
        with db_connection() as conn:
            cur = conn.cursor()            
            q = f"""
                SELECT
                    *
                FROM
                    queue 
                WHERE 
                    status = ?
                    AND problem_id IN ({', '.join(map(str, self.problems))})
                ORDER BY
                    id ASC
                LIMIT 1
                """
            cur.execute(q, (QueueStatus.WAITING,))
            fetch = cur.fetchone()
        return QueueEntry(*fetch) if fetch else None
    
    def add_problem(self, problem: Problem | int):
        if isinstance(problem, Problem):
            problem = problem.id
        if problem in self.problems:
            raise ValueError(f"Принимающий {self.id} уже принимает задачу {problem}")
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO examiner_problems(examiner_id, problem_id) VALUES (?, ?)", (self.id, problem))
            conn.commit()
        self.__problems.append(problem)

    def remove_problem(self, problem: Problem | int):
        if isinstance(problem, Problem):
            problem = problem.id
        if problem not in self.problems:
            raise ValueError(f"Принимающий {self.id} уже не принимает задачу {problem}")
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM examiner_problems WHERE examiner_id = ? AND problem_id = ?", (self.id, problem))
            conn.commit()
        self.__problems.remove(problem)

    def set_problems(self, problems: list[Problem] | list[int] | None):
        if problems is None:
            problems = []
        elif isinstance(problems[0], Problem):
            problems = [problem.id for problem in problems]
        remove = []
        add = []
        for problem in self.problems:
            if problem not in problems:
                remove.append(problem)
        for problem in problems:
            if problem not in self.problems:
                add.append(problem)
        with db_connection() as conn:
            cur = conn.cursor()
            for problem in remove:
                cur.execute("DELETE FROM examiner_problems WHERE examiner_id = ? AND problem_id = ?", (self.id, problem))
            for problem in add:
                cur.execute("INSERT INTO examiner_problems(examiner_id, problem_id) VALUES (?, ?)", (self.id, problem))
            conn.commit()
        self.__problems = problems
    

    @property
    def queue_entry(self) -> QueueEntry | None:
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
    @property
    def busyness_level(self): return self.__busyness_level
    @busyness_level.setter
    def busyness_level(self, value: int):
        self.__set('busyness_level', value)
        self.__busyness_level = value
    @property
    def is_busy(self): return self.__is_busy
    @is_busy.setter
    def is_busy(self, value: bool):
        self.__set('is_busy', value)
        self.__is_busy = value
