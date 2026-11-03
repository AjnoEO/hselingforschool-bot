from enums import Platform
from .types import Document, Keyboard, Media, MessageRef, RemoveKeyboard


class Messenger:
    """Интерфейс мессенджера. Конкретная реализация занимается разметкой и медиа."""

    platform: Platform = Platform.TELEGRAM

    def ref_for(self, message) -> MessageRef:
        raise NotImplementedError

    def send(
        self,
        peer_id: int,
        text: str = None,
        *,
        media: Media | None = None,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        reply_to: MessageRef | None = None,
        show_caption_above_media: bool = False
    ) -> MessageRef:
        raise NotImplementedError

    def send_many(
        self,
        peer_ids: list[int],
        text: str = None,
        *,
        media: Media | None = None,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False
    ) -> None:
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

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False,
                        *, user_id: int | None = None, peer_id: int | None = None) -> None:
        raise NotImplementedError

    def copy_message(self, peer_id: int, ref: MessageRef) -> MessageRef:
        raise NotImplementedError

    def download_document(self, document: Document) -> bytes:
        raise NotImplementedError
