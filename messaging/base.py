from enums import Platform
from .types import Document, Keyboard, Media, MessageRef, RemoveKeyboard


class Messenger:
    """Интерфейс мессенджера. Конкретная реализация занимается разметкой и медиа."""

    platform: Platform = Platform.TELEGRAM

    def ref_for(self, message) -> MessageRef:
        raise NotImplementedError

    def send_text(
        self,
        peer_id: int,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        reply_to: MessageRef | None = None,
    ) -> MessageRef:
        raise NotImplementedError

    def send_photo(
        self,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False,
    ) -> MessageRef:
        raise NotImplementedError

    def send_document(
        self,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> MessageRef:
        raise NotImplementedError

    def edit_text(
        self,
        ref: MessageRef,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> None:
        raise NotImplementedError

    def edit_keyboard(self, ref: MessageRef, keyboard: Keyboard | RemoveKeyboard | None) -> None:
        raise NotImplementedError

    def delete(self, ref: MessageRef) -> None:
        raise NotImplementedError

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False) -> None:
        raise NotImplementedError

    def copy_message(self, peer_id: int, ref: MessageRef) -> MessageRef:
        raise NotImplementedError

    def download_document(self, document: Document) -> bytes:
        raise NotImplementedError
