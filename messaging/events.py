from enums import Platform
from .types import Document, MessageRef


class Event:
    """Общий класс для событий"""

    def __init__(
        self,
        platform: Platform,
        sender_id: int,
        sender_handle: str,  
    ):
        self.platform = platform
        self.sender_id = sender_id
        self.sender_handle = sender_handle


    @property
    def ref(self) -> MessageRef:
        raise NotImplementedError

    
    @property
    def is_callback(self) -> bool:
        raise NotImplementedError


class IncomingMessage(Event):
    """Входящее сообщение, не зависящее от мессенджера"""

    def __init__(
        self,
        platform: Platform,
        peer_id: int,
        sender_id: int,
        sender_handle: str,
        message_id: int,
        *,
        text: str | None = None,
        caption: str | None = None,
        content_type: str = "text",
        document: Document | None = None,
        reply_to: MessageRef | None = None,
    ):
        super().__init__(platform, sender_id, sender_handle)
        self.peer_id = peer_id
        self.message_id = message_id
        self.text = text
        self.caption = caption
        self.content_type = content_type
        self.document = document
        self.reply_to = reply_to

    @property
    def ref(self) -> MessageRef:
        return MessageRef(self.peer_id, self.message_id, self.platform)

    @property
    def is_callback(self) -> bool:
        return False


class IncomingCallback(Event):
    """Нажатие на inline-кнопку"""

    def __init__(
        self,
        platform: Platform,
        callback_id: str,
        sender_id: int,
        sender_handle: str,
        data: str,
        ref: MessageRef,
    ):
        super().__init__(platform, sender_id, sender_handle)
        self.callback_id = callback_id
        self.data = data
        self._ref = ref
        self.peer_id = ref.peer_id

    @property
    def ref(self) -> MessageRef:
        return self._ref

    @property
    def is_callback(self) -> bool:
        return True
