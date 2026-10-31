from .context import Context

import re
from .text import extract_command
from typing import Any


class Router:
    """Регистрация и выбор обработчиков по нейтральному контексту"""

    def __init__(self):
        self.message_handlers: list[tuple[dict[str], Any]] = []
        self.callback_handlers: list[tuple[dict[str], Any]] = []

    def message(self, **filters):
        def decorator(handler):
            self.message_handlers.append((filters, handler))
            return handler
        return decorator

    def callback(self, **filters):
        def decorator(handler):
            self.callback_handlers.append((filters, handler))
            return handler
        return decorator

    def match(self, ctx: Context):
        handlers = self.callback_handlers if ctx.event.is_callback else self.message_handlers
        for filters, handler in handlers:
            if self.__matches(ctx, filters):
                return handler
        return None

    @staticmethod
    def __matches(ctx: Context, filters: dict[str]): # TODO: Don't hard-code the filters into Router and extra data into ctx
        if ctx.event.is_callback:
            if "prefix" in filters and not ctx.data.startswith(filters["prefix"]):
                return False
            if "func" in filters and not filters["func"](ctx):
                return False
        else:
            content_types = filters.get("content_types", ["text"])
            if ctx.content_type not in content_types:
                return False
            command = extract_command(ctx.text or ctx.caption)
            if "commands" in filters and command not in filters["commands"]: # TODO: Collapse into one filter?
                return False
            if "doc_commands" in filters and command not in filters["doc_commands"]:
                return False
            if "regexp" in filters and not re.search(filters["regexp"], ctx.text or ""):
                return False
        if "roles" in filters and not set(filters["roles"]) & set(ctx.roles):
            return False
        if "olymp_statuses" in filters and ctx.olymp_status not in filters["olymp_statuses"]:
            return False
        if "discussing_examiner" in filters and ctx.discussing_examiner != filters["discussing_examiner"]:
            return False
        if "state" in filters:
            state = filters["state"]
            if state != "*" and ctx.state.get() != state:
                return False
        return True
