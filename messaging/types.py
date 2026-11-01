from enums import Platform


class MessageRef:
    """Ссылка на сообщение, не зависящая от мессенджера"""

    def __init__(self, peer_id: int, message_id: int, platform: Platform = Platform.TELEGRAM):
        self.peer_id: int = peer_id
        self.message_id: int = message_id
        self.platform: Platform = platform


class Button:
    def __init__(self, text: str, *, callback_data: str | None = None):
        self.text: str = text
        self.callback_data: str | None = callback_data


class Keyboard:
    """Клавиатура: обычная (внизу экрана) или inline"""

    def __init__(self, *, inline: bool = False, resize: bool = True): # Сделать настройку для удаления после использования
        self.inline: bool = inline # TODO: А это вообще где-нибудь используется?
        self.resize: bool = resize
        self.rows: list[list[Button]] = []

    def row(self, *buttons: Button):
        self.rows.append(list(buttons)) # TODO: Конвертировать str -> Button?
        return self


class RemoveKeyboard:
    """Убрать обычную клавиатуру у пользователя"""


REMOVE_KEYBOARD = RemoveKeyboard()


class Media:
    def __init__(
        self,
        path: str | None = None,
        *,
        data: bytes | None = None,
        filename: str | None = None,
    ):
        self.path: str | None = path
        self.data: bytes | None = data # TODO: Точно надо?
        self.filename: str | None = filename


class Document:
    """Входящий файл, который можно скачать через мессенджер"""

    def __init__(self, file_id: str, file_name: str | None = None, mime_type: str | None = None):
        self.file_id: str = file_id
        self.file_name: str | None = file_name
        self.mime_type: str | None = mime_type


def inline_keyboard(buttons: dict[str, str], row_width: int = 3) -> Keyboard:
    """
    Собрать inline-клавиатуру из словаря «текст кнопки → callback_data»
    """
    keyboard = Keyboard(inline=True)
    row = []
    for text, callback_data in buttons.items():
        row.append(Button(text, callback_data=callback_data))
        if len(row) >= row_width:
            keyboard.row(*row)
            row = []
    if row:
        keyboard.row(*row)
    return keyboard


def reply_keyboard(*rows: list[str], resize: bool = True) -> Keyboard:
    """
    Собрать обычную клавиатуру из строк с текстами кнопок
    """
    keyboard = Keyboard(resize=resize)
    for row in rows:
        keyboard.row(*[Button(text) for text in row])
    return keyboard
