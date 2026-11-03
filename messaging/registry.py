from collections import defaultdict
from inspect import signature, Parameter
from typing import Callable, Any
from utils import MultiError
from enums import Platform
from .base import Messenger
from .types import Keyboard, Media, MessageRef, RemoveKeyboard

class MessengerRegistry:
    """Все подключённые мессенджеры, выбор по платформе получателя"""

    def __init__(self):
        self.messengers: dict[Platform, Messenger] = {}

    def add(self, messenger: Messenger):
        self.messengers[messenger.platform] = messenger
        return messenger

    def for_platform(self, platform: Platform) -> Messenger:
        messenger = self.messengers.get(platform)
        if messenger is None:
            raise ValueError(f"Мессенджер для платформы {platform} не подключён")
        return messenger

    @staticmethod
    def __check_user(user):
        if user.external_id is None:
            raise ValueError(f"Пользователь {user} не авторизован")

    def send_text(
        self,
        platform: Platform,
        peer_id: int,
        text: str,
        *,
        keyboard: Keyboard | RemoveKeyboard | None = None,
        reply_to: MessageRef | None = None,
    ) -> MessageRef:
        return self.for_platform(platform).send_text(
            peer_id, text, keyboard=keyboard, reply_to=reply_to
        )

    def notify(self, user, text: str, *, keyboard: Keyboard | RemoveKeyboard | None = None) -> MessageRef:
        self.__check_user(user)
        return self.for_platform(user.platform).send_text(
            user.external_id, text, keyboard=keyboard
        )
    
    def notify_many(
        self,
        users,
        text: str | Callable[[Platform], str] | Callable[[Any], str],
        *,
        keyboard: Keyboard | RemoveKeyboard | None | Callable[[Any], Keyboard | RemoveKeyboard | None] = None,
        only_registered: bool = True   
    ) -> None:
        unique_text_per_user = False
        if isinstance(text, Callable):
            argclass = list(signature(text).parameters.values())[0].annotation
            if isinstance(Parameter.empty(), argclass):
                raise ValueError("Тип аргумента функции text должен быть задан в сигнатуре функции")
            unique_text_per_user = not issubclass(argclass, Platform)
        unique_keyboard_per_user = isinstance(keyboard, Callable)
        error_list: list[Exception] = []
        if unique_text_per_user or unique_keyboard_per_user:
            if unique_text_per_user:
                user_text = text
            elif isinstance(text, Callable):
                user_text = lambda user: text(user.platform)
            else:
                user_text = lambda _: text
            if unique_keyboard_per_user:
                user_kb = keyboard
            else:
                user_kb = lambda _: keyboard
            for user in users:
                try:
                    self.__check_user(user)
                except ValueError as exc:
                    if not only_registered: error_list.append(exc)
                    continue
                try:
                    self.notify(user, user_text(user), keyboard=user_kb(user))
                except Exception as exc:
                    error_list.append(exc)
        else:
            platform_to_user_ids: defaultdict[Platform, list] = defaultdict(lambda: [])
            for user in users:
                try:
                    self.__check_user(user)
                    platform_to_user_ids[user.platform].append(user.external_id)
                except ValueError as exc:
                    if not only_registered: error_list.append(exc)
            for platform, user_ids in platform_to_user_ids.items():
                try:
                    self.for_platform(platform).send_texts(user_ids, text, keyboard=keyboard)
                except MultiError as new_multierror:
                    error_list.extend(new_multierror.error_list)
        multierror = MultiError(error_list, "при рассылке сообщений")
        multierror.clean_raise()

    def send_photo(
        self,
        platform: Platform,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
        show_caption_above_media: bool = False,
    ) -> MessageRef:
        return self.for_platform(platform).send_photo(
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
        self.__check_user(user)
        return self.send_photo(
            user.platform,
            user.external_id,
            media,
            caption=caption,
            keyboard=keyboard,
            show_caption_above_media=show_caption_above_media,
        )

    def send_document(
        self,
        platform: Platform,
        peer_id: int,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> MessageRef:
        return self.for_platform(platform).send_document(
            peer_id, media, caption=caption, keyboard=keyboard
        )

    def notify_document(
        self,
        user,
        media: Media,
        *,
        caption: str = "",
        keyboard: Keyboard | RemoveKeyboard | None = None,
    ) -> MessageRef:
        self.__check_user(user)
        return self.send_document(
            user.platform,
            user.external_id,
            media,
            caption=caption,
            keyboard=keyboard,
        )

    def copy_message(self, user, ref: MessageRef) -> MessageRef:
        self.__check_user(user)
        if ref.platform != user.platform:
            raise NotImplementedError("Пересылка сообщений между мессенджерами пока не поддерживается")
        return self.for_platform(user.platform).copy_message(user.external_id, ref)
