import sqlite3
from functools import wraps
from db import DATABASE

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
        return result
    return wrapper

@provide_cursor
def value_exists(table: str, column_values: dict[str], *, cursor: sqlite3.Cursor) -> bool:
    conditions = []
    values = []
    for c, v in column_values.items():
        conditions.append(c + " = ?")
        values.append(v)
    cursor.execute(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {' AND '.join(conditions)})", tuple(values))
    result = cursor.fetchone()
    return bool(result[0])