def extract_command(text: str | None) -> str | None:
    """Вернуть команду без слэша и без @botname"""
    if not text or not text.startswith("/"):
        return None
    command = text.split(maxsplit=1)[0][1:]
    return command.split("@", 1)[0]
