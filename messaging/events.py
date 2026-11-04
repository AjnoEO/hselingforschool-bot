from enums import Platform
from .types import Document, MessageRef


class Event:
    """Общий класс для событий"""

    def __init__(
        self,
        platform: Platform,
        peer_id: int,
        sender_id: int,
        sender_handle: str,  
    ):
        self.platform = platform
        self.peer_id = peer_id
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
        attachment: Document | None = None,
        document: Document | None = None,
        reply_to: MessageRef | None = None,
    ):
        super().__init__(platform, peer_id, sender_id, sender_handle)
        self.message_id = message_id
        self.text = text
        self.attachment = attachment
        self.document = document
        self.reply_to = reply_to

    @property
    def ref(self) -> MessageRef:
        return MessageRef(self.peer_id, self.message_id, self.platform, self.text, self.attachment)

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
        self._ref = ref
        super().__init__(platform, ref.peer_id, sender_id, sender_handle)
        self.callback_id = callback_id
        self.data = data

    @property
    def ref(self) -> MessageRef:
        return self._ref

    @property
    def is_callback(self) -> bool:
        return True
