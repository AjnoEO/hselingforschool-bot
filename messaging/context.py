from .events import Event, IncomingMessage, IncomingCallback
from .registry import MessengerRegistry
from .types import Keyboard, Media, MessageRef, RemoveKeyboard

class Context: # TODO: Точно надо? Точно не слишком телеграмоцентрично?
    """Событие пользователя, его роль и способ ответа"""

    def __init__(
        self,
        messengers: MessengerRegistry,
        *,
        event: Event | None = None,
        state=None,
        user=None, # TODO: Аккуратно сделать так чтобы можно было использовать наш класс User
        participant = None,
        examiner = None,
        roles: list[str] | None = None,
        olymp_status=None,
        discussing_examiner: bool = False,
        is_owner: bool = False,
    ):
        self.messengers = messengers
        self.messenger = messengers.for_platform(event.platform) if event is not None else None
        self.event = event
        self.state = state
        self.user = user
        self.participant = participant
        self.examiner = examiner

        self.roles: list[str] = roles or []
        self.olymp_status = olymp_status
        self.discussing_examiner: bool = discussing_examiner
        self.is_owner: bool = is_owner

        self.ref: MessageRef | None = event.ref if event is not None else None

        self.platform = event.platform if event is not None else None
        self.sender_id = event.sender_id if event is not None else None
        self.sender_handle = event.sender_handle if event is not None else None

        self.peer_id = event.peer_id if event is not None else None
        self.message_id = event.ref.message_id if event is not None else None
        self.text = event.text if isinstance(event, IncomingMessage) else None
        self.caption = event.caption if isinstance(event, IncomingMessage) else None
        self.content_type = event.content_type if isinstance(event, IncomingMessage) else None
        self.document = event.document if isinstance(event, IncomingMessage) else None
        self.reply_to = event.reply_to if isinstance(event, IncomingMessage) else None

        self.callback_id = event.callback_id if isinstance(event, IncomingCallback) else None
        self.data = event.data if isinstance(event, IncomingCallback) else None
        self.answered = False

    def __ref(self, ref: MessageRef | None = None) -> MessageRef:
        ref = ref or self.ref
        if ref is None:
            raise ValueError("У контекста нет сообщения")
        return ref

    def reply(self, text: str, *, keyboard: Keyboard | RemoveKeyboard | None = None) -> MessageRef:
        ref = self.__ref()
        return self.messenger.send_text(ref.peer_id, text, keyboard=keyboard)

    def send_text(
        self,
        peer_id: int,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        reply_to: MessageRef | None = None,
    ) -> MessageRef:
        return self.messenger.send_text(peer_id, text, keyboard=keyboard, reply_to=reply_to)

    def notify(self, user, text: str, *, keyboard: Keyboard | RemoveKeyboard | None = None) -> MessageRef:
        return self.messengers.notify(user, text, keyboard=keyboard)

    def send_photo(
        self,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False,
    ) -> MessageRef:
        return self.messenger.send_photo(
            peer_id,
            media,
            caption=caption,
            keyboard=keyboard,
            show_caption_above_media=show_caption_above_media,
        )

    def notify_photo(
        self,
        user,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False,
    ) -> MessageRef:
        return self.messengers.notify_photo(
            user,
            media,
            caption=caption,
            keyboard=keyboard,
            show_caption_above_media=show_caption_above_media,
        )

    def send_document(
        self,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> MessageRef:
        return self.messenger.send_document(peer_id, media, caption=caption, keyboard=keyboard)

    def notify_document(
        self,
        user,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> MessageRef:
        return self.messengers.notify_document(user, media, caption=caption, keyboard=keyboard)

    def download_document(self) -> bytes:
        if self.document is None:
            raise ValueError("В событии нет документа")
        return self.messenger.download_document(self.document)

    def edit_text(
        self,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        ref: MessageRef | None = None,
    ) -> None:
        self.messenger.edit_text(self.__ref(ref), text, keyboard=keyboard)

    def edit_keyboard(
        self,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        *,
        ref: MessageRef | None = None,
    ) -> None:
        self.messenger.edit_keyboard(self.__ref(ref), keyboard)

    def delete(self, ref: MessageRef | None = None) -> None:
        self.messenger.delete(self.__ref(ref))

    def answer(self, text: str = "", show_alert: bool = False) -> None:
        if self.callback_id is None:
            raise ValueError("Контекст не связан с callback-запросом")
        self.messenger.answer_callback(
            self.callback_id, text=text, show_alert=show_alert,
            user_id=self.sender_id, peer_id=self.peer_id,
        )
        self.answered = True

    def copy_to(self, user, ref: MessageRef) -> MessageRef:
        return self.messengers.copy_message(user, ref)
