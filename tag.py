import sqlite3
from db import DATABASE
from utils import UserError, update_in_table, provide_cursor
from telebot.formatting import escape_html

class Tag:
    def __init__(
        self,
        id: int,
        name: str,
        description: str
    ):
        self.__id = id
        self.__name = name
        self.__description = description

    @classmethod
    def from_id(cls, id: int):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM tags WHERE id = ?", (id,))
            fetch = cur.fetchone()
        if not fetch:
            raise UserError("Тэг не найден")
        return cls(*fetch)
    
    @classmethod
    def from_name(cls, name: str, no_error: bool = False):
        with sqlite3.connect(DATABASE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM tags WHERE name = ?", (name,))
            fetch = cur.fetchone()
        if not fetch:
            if no_error:
                return None
            raise UserError("Тэг не найден")
        return cls(*fetch)

    @classmethod
    @provide_cursor
    def create(
        cls,
        name: str,
        description: str,
        *,
        no_error: bool = False,
        cursor: sqlite3.Cursor | None = None,
    ):
        """
        Добавить тэг в таблицу tags
        """
        cursor.execute("SELECT * FROM tags")
        fetch = cursor.fetchall()
        for f in fetch:
            t = cls(*f)
            if t.name == name:
                if no_error:
                    return t
                raise UserError(f"Название <em>{escape_html(t.name)}</em> уже занято тэгом <code>{t.id}</code>")
        values = (name, description)
        cursor.execute("INSERT INTO tags(name, description) VALUES (?, ?)", values)
        cursor.connection.commit()
        created_id = cursor.lastrowid
        return cls(created_id, *values)
    
    @classmethod
    @provide_cursor
    def get_all(cls, *, cursor: sqlite3.Cursor | None = None):
        cursor.execute("SELECT * FROM tags")
        results = cursor.fetchall()
        return [cls(*fetch) for fetch in results]

    @provide_cursor
    def delete(self, *, cursor: sqlite3.Cursor | None = None):
        cursor.execute("DELETE FROM tags WHERE id = ?", (self.id,))


    def __str__(self):
        return f"{escape_html(self.description)}"


    def __set(self, column: str, value):
        update_in_table("tags", column, value, "id", self.__id)

    @property
    def id(self): return self.__id
    @property
    def name(self): return self.__name
    @name.setter
    def name(self, value: str):
        tag = Tag.from_name(value, no_error=True)
        if tag:
            raise UserError(f"Название <em>{escape_html(tag.name)}</em> уже занято тэгом <code>{tag.id}</code>")
        self.__set("name", value)
        self.__name = value
    @property
    def description(self): return self.__description
    @description.setter
    def description(self, value: str):
        self.__set("description", value)
        self.__description = value

    def __eq__(self, other): return isinstance(other, self.__class__) and self.id == other.id
