import sqlite3
from telebot.types import Message, Document
from telebot.util import generate_random_token
from telebot import TeleBot
from pathlib import Path
import os
from data import TOKEN
import requests
from functools import wraps
from db import DATABASE

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


def get_arg(message: Message, no_arg_error: str):
    command_arg = message.text.split(maxsplit=1)
    if len(command_arg) == 1:
        raise UserError(no_arg_error)
    return command_arg[1]


def get_n_args(message: Message, min: int, max: int, no_arg_error: str):
    command_arg = message.text.split(maxsplit=max)[1:]
    if len(command_arg) < min:
        raise UserError(no_arg_error)
    return tuple(command_arg)


def get_file(message: Message, bot: TeleBot, no_file_error: str, expected_type: str | None = None):
    """
    Получить файл от пользователя
    """
    if not message.document and not (message.reply_to_message and message.reply_to_message.document):
        raise UserError(no_file_error)
    document: Document = message.document or message.reply_to_message.document
    if expected_type and not document.file_name.endswith(expected_type):
        raise UserError(f"Файл должен иметь расширение `{expected_type}`")
    file_path = bot.get_file(document.file_id).file_path
    return requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}").content


def save_downloaded_file(file: bytes):
    dir = "downloaded_files"
    Path(dir).mkdir(exist_ok=True)
    dir_list = os.listdir(dir)
    filename = generate_random_token()
    while filename + ".pdf" in dir_list:
        filename = generate_random_token()
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
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            result = func(*args, **kwargs, cursor=cursor)
            conn.commit()
        return result
    return wrapper

@provide_cursor
def value_exists(table: str, column_values: dict[str], *, cursor: sqlite3.Cursor | None = None) -> bool:
    conditions = []
    values = []
    for c, v in column_values.items():
        conditions.append(c + " = ?")
        values.append(v)
    cursor.execute(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {' AND '.join(conditions)})", tuple(values))
    result = cursor.fetchone()
    return bool(result[0])

def update_in_table(table: str, column: str, value, id_column: str, id_value):
    with sqlite3.connect(DATABASE) as conn:
        cur = conn.cursor()
        q = f"UPDATE {table} SET {column} = ? WHERE {id_column} = ?"
        cur.execute(q, (value, id_value))
        conn.commit()
