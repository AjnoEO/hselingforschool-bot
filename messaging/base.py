from enums import Platform
from .types import Document, Keyboard, Media, MessageRef, RemoveKeyboard


class Messenger:
    """Интерфейс мессенджера. Конкретная реализация занимается разметкой и медиа."""

    platform: Platform = Platform.TELEGRAM
    api_exceptions = ConnectionError

    @classmethod
    def _retry_method(cls, method, *args, max_retries: int = 5, **kwargs):
        while max_retries:
            try:
                return method(*args, **kwargs)
            except cls.api_exceptions as exc:
                max_retries -= 1
                print("!E >>", exc)
                if not max_retries:
                    raise exc

    def ref_for(self, message) -> MessageRef:
        raise NotImplementedError

    def get_contents(self, message: MessageRef) -> dict[str, str]:
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
        if self.platform != ref.platform and ref.text is None and ref.media is None:
            raise ValueError(
                f"Данные о содержании сообщения {ref} недоступны. "
                f"Пересылка из {ref.platform} в {self.platform} невозможна")
        raise NotImplementedError

    def copy_many(self, peer_ids: list[int], ref: MessageRef) -> None:
        if self.platform != ref.platform and ref.text is None and ref.media is None:
            raise ValueError(
                f"Данные о содержании сообщения {ref} недоступны. "
                f"Пересылка из {ref.platform} в {self.platform} невозможна")
        raise NotImplementedError

    def download_document(self, document: Document) -> bytes:
        raise NotImplementedError
