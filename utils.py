from sqlite3 import Cursor
from pathlib import Path
import html
import os
import secrets
from functools import wraps
from db import db_connection
from messaging.text import extract_command


class UserError(Exception):
    """Ошибки, вызванные неправильными действиями пользователей"""
    def __init__(self, *args, contact_note = True, reply_markup = None):
        super().__init__(*args)
        self.reply_markup = reply_markup
        self.contact_note = contact_note


def decline(numeral: int, stem: str, endings: tuple[str, str, str]):
    numeral = abs(numeral)
    if (numeral // 10) % 10 == 1:
        return stem + endings[2]
    if numeral % 10 == 1:
        return stem + endings[0]
    if numeral % 10 == 0:
        return stem + endings[2]
    if numeral % 10 <= 4:
        return stem + endings[1]
    return stem + endings[2]


def escape_html(text: str):
    return html.escape(text, quote=False)


def get_arg(text: str, no_arg_error: str):
    command_arg = text.split(maxsplit=1)
    if len(command_arg) == 1:
        raise UserError(no_arg_error)
    return command_arg[1]


def get_n_args(text: str, min: int, max: int, no_arg_error: str):
    command_arg = text.split(maxsplit=max)[1:]
    if len(command_arg) < min:
        raise UserError(no_arg_error)
    return tuple(command_arg)


def get_tags_args(text: str, no_arg_error: str | None = None):
    """
    :return include: Список обязательных тэгов
    :return exclude: Список исключённых тэгов
    """
    command_arg = text.split()
    if len(command_arg) == 1:
        if no_arg_error is None: return None, None
        else: raise UserError(no_arg_error)
    include = []
    exclude = []
    for arg in command_arg[1:]:
        if not arg.startswith(("+", "-")):
            raise UserError("Все тэги должны быть указаны со знаком + либо -")
        if arg.startswith("+"):
            include.append(arg[1:])
        else:
            exclude.append(arg[1:])
    return include, exclude


def save_downloaded_file(file: bytes):
    dir = "downloaded_files"
    Path(dir).mkdir(exist_ok=True)
    dir_list = os.listdir(dir)
    filename = secrets.token_hex(8)
    while filename + ".pdf" in dir_list:
        filename = secrets.token_hex(8)
    path = os.path.join(dir, filename + ".pdf")
    with open(path, "wb") as f:
        f.write(file)
    return path


def provide_cursor(func):
    """
    Даёт возможность не указывать курсор SQLite при вызове функции 
    — в таком случае база данных открывается отдельно внутри. 
    Функция должна содержать именованный аргумент `cursor` типа `sqlite3.Cursor`.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        if 'cursor' in kwargs:
            return func(*args, **kwargs)
        with db_connection() as conn:
            cursor = conn.cursor()
            result = func(*args, **kwargs, cursor=cursor)
            conn.commit()
        return result
    return wrapper

@provide_cursor
def value_exists(table: str, column_values: dict[str], *, cursor: Cursor | None = None) -> bool:
    conditions = []
    values = []
    for c, v in column_values.items():
        conditions.append(c + " = ?")
        values.append(v)
    cursor.execute(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {' AND '.join(conditions)})", tuple(values))
    result = cursor.fetchone()
    return bool(result[0])

def update_in_table(table: str, column: str, value, id_column: str, id_value):
    with db_connection() as conn:
        cur = conn.cursor()
        q = f"UPDATE {table} SET {column} = ? WHERE {id_column} = ?"
        cur.execute(q, (value, id_value))
        conn.commit()
