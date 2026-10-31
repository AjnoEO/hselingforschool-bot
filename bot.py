import os
from pathlib import Path
import re
import threading
from typing import Callable
import json
import traceback
from db import create_update_db
from data import (
    TELEGRAM_TOKEN, VK_TOKEN, VK_GROUP_ID, VK_ENABLED,
    OWNER_ID, OWNER_PLATFORM, OWNER_HANDLES, BUTTONS_IMG,
    PROMOTE_COMMANDS, NO_EXAMINER_COMPLAINTS, MEMBER_PAGE_SIZE, ASK_PEOPLE_HANDLES,
)
from enums import Platform
from olymp import Olymp, OlympStatus
from messaging import (
    Button, Context, ConversationState, Keyboard, Media, MessageRef, Event,
    MessengerRegistry, REMOVE_KEYBOARD, Router, StateStore, inline_keyboard,
)
from users import User, OlympMember, Participant, Examiner
from tag import Tag
from problem import Problem, ProblemBlock, BlockType
from queue_entry import QueueEntry, QueueStatus
from utils import (
    UserError, decline, escape_html, extract_command, get_arg, get_n_args,
    get_tags_args, save_downloaded_file,
)
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from io import BytesIO


router = Router()
state_store = StateStore()
notifier = MessengerRegistry()
current_olymp: Olymp | None = None


def build_context(event: Event):
    participant = None
    examiner = None
    if current_olymp:
        participant: Participant = Participant.from_external_id(
            event.platform, event.sender_id, current_olymp.id, no_error=True
        )
        examiner: Examiner = Examiner.from_external_id(
            event.platform, event.sender_id, current_olymp.id, no_error=True
        )
    is_owner = (event.platform == OWNER_PLATFORM and event.sender_id == OWNER_ID)
    roles = ["owner" if is_owner else "not owner"]
    if participant:
        roles.append("participant")
    if examiner:
        roles.append("examiner")
    discussing_examiner = bool(
        examiner
        and current_olymp
        and current_olymp.status in [OlympStatus.CONTEST, OlympStatus.QUEUE]
        and examiner.queue_entry is not None
    )
    state = ConversationState(state_store, event.platform, event.peer_id)
    return Context(
        notifier,
        event=event,
        state=state,
        user=User.from_external_id(event.platform, event.sender_id, no_error=True),
        participant=participant,
        examiner=examiner,
        roles=roles,
        olymp_status=current_olymp.status if current_olymp else None,
        discussing_examiner=discussing_examiner,
        is_owner=is_owner,
    )


dispatch_lock = threading.Lock()


def handle_event(event):
    ctx = build_context(event)
    handler = router.match(ctx)
    if handler is None:
        return
    try:
        with dispatch_lock:
            handler(ctx)
    except UserError as exc:
        error_message = "\u26a0\ufe0f Ошибка!\n" + str(exc)
        if not ctx.is_owner and exc.contact_note:
            error_message += f"\nЕсли тебе кажется, что это баг, сообщи {OWNER_HANDLES[ctx.platform]}"
        ctx.messenger.send_text(ctx.peer_id, error_message, keyboard=exc.reply_markup)
    except Exception as exc:
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        error_message = (
            f"\u26a0\ufe0f Во время выполнения операции произошла ошибка:\n"
            f"<code>{exc.__class__.__name__} "
            f"({os.path.basename(tb.filename)}, строка {tb.lineno}): "
            f"{' '.join([escape_html(str(arg)) for arg in exc.args])}</code>"
        )
        if not ctx.is_owner:
            error_message += f"\nЕсли тебе кажется, что это баг, сообщи {OWNER_HANDLES[ctx.platform]}"
        print(exc)
        ctx.messenger.send_text(ctx.peer_id, error_message)
        raise

# TODO: Унести всё это добро в другой файл?
def parse_identity_arg(arg: str) -> tuple[Platform, str]:
    arg = arg.strip()
    no_messenger_error = UserError(f"Требуется указать мессенджер ({', '.join([p.code for p in Platform])})")
    if not "@" in arg:
        raise no_messenger_error
    prefix, handle = arg.split("@", 1)
    if not prefix:
        raise no_messenger_error
    try:
        platform = Platform.from_code(prefix)
    except KeyError:
        raise UserError(f"Неизвестный мессенджер: {prefix}")
    handle = User.conform_handle(handle)
    if not handle:
        raise UserError("Необходимо указать хэндл пользователя")
    return platform, handle


def resolve_user(arg: str, no_error: bool = False):
    platform, handle = parse_identity_arg(arg)
    if handle.isnumeric():
        user = User.from_external_id(platform, int(handle), no_error=True)
        if user:
            return user
    return User.from_handle(platform, handle, no_error=no_error)


def resolve_member(arg: str, member_class: type[OlympMember], olymp_id: int, no_error: bool = False):
    platform, handle = parse_identity_arg(arg)
    if handle.isnumeric():
        member = member_class.from_external_id(platform, int(handle), olymp_id, no_error=True)
        if member:
            return member
    return member_class.from_handle(platform, handle, olymp_id, no_error=no_error)


def get_document(ctx: Context, no_file_error: str, expected_type: str | None = None) -> bytes:
    if ctx.document is None:
        raise UserError(no_file_error)
    if expected_type and not (ctx.document.file_name or "").endswith(expected_type):
        raise UserError(f"Файл должен иметь расширение `{expected_type}`")
    return ctx.download_document()


JOIN_QUEUE_BUTTON = "Сдать задачу"
LEAVE_QUEUE_BUTTON = "Покинуть очередь"
MY_STATS_BUTTON = "Мои задачи"

participant_keyboard = Keyboard()
participant_keyboard.row(Button(JOIN_QUEUE_BUTTON), Button(MY_STATS_BUTTON))

participant_keyboard_in_queue = Keyboard()
participant_keyboard_in_queue.row(Button(LEAVE_QUEUE_BUTTON), Button(MY_STATS_BUTTON))

participant_keyboard_olymp_finished = Keyboard()
participant_keyboard_olymp_finished.row(Button(MY_STATS_BUTTON))

def participant_keyboard_choose_problem(participant: Participant):
    buttons = {f"{i+1}: {problem.name}": f'join_queue_{i+1}'
               for i, problem in enumerate(participant.problems())}
    buttons["Отмена"] = 'join_queue_cancel'
    return inline_keyboard(buttons, row_width=3)


BUTTON_HELP = (f"Чтобы сдать задачу, используй кнопку «{JOIN_QUEUE_BUTTON}» "
               f"(она может быть скрыта справа от поля ввода сообщения "
               f"под кнопкой в виде четырёх квадратиков или четырёхлистника)")


@router.message(
    commands=['start', 'help', 'authenticate'],
    roles=['not owner'],
    olymp_statuses=[None, OlympStatus.TBA]
)
def lost(ctx: Context):
    response = ("Привет! Я бот для проведения <strong>Устной олимпиады по лингвистике (УОЛ)</strong>\n"
                "Пока что олимпиады нет. О регистрации на олимпиаду и дате её начала "
                "мы объявляем в каналах УОЛ и Лингвокружка:\n"
                "- ВКонтакте: vk.ru/hseling.for.school\n"
                "- Телеграм: t.me/hselingforschool\n"
                "- Сайт: ling.hse.ru/junior\n"
                "Подписывайся и ожидай информации там")
    ctx.reply(response)


@router.message(
    commands=['start', 'authenticate'], 
    olymp_statuses = [OlympStatus.REGISTRATION, OlympStatus.CONTEST]
)
def send_welcome(ctx: Context):
    olymp_id = current_olymp.id
    sender_id = ctx.sender_id
    member: OlympMember | None = (
        Participant.from_external_id(ctx.platform, sender_id, olymp_id, no_error=True)
        or Examiner.from_external_id(ctx.platform, sender_id, olymp_id, no_error=True)
    )
    if isinstance(member, Participant) and member.finished:
        raise UserError("Неизвестная команда")
    if member:
        send_authentication_confirmation(member, already_authenticated=True)
        return
    
    handle = ctx.sender_handle or str(ctx.sender_id)
    new_member: Participant | Examiner | None = (
        Participant.from_handle(ctx.platform, handle, olymp_id, no_error=True)
        or Examiner.from_handle(ctx.platform, handle, olymp_id, no_error=True)
    )
    if isinstance(new_member, Participant) and new_member.finished:
        raise UserError("Неизвестная команда")
    
    user_old_handle = User.from_external_id(ctx.platform, sender_id, no_error=True)
    if user_old_handle and new_member:
        if user_old_handle.handle.isnumeric():
            participant_reply = f"Ты участвовал(-а) в прошлой олимпиаде без хэндла?"
        elif new_member.handle.isnumeric():
            participant_reply = f"У тебя раньше был хэндл @{user_old_handle.handle}?"
        else:
            participant_reply = f"У тебя поменялся хэндл с @{user_old_handle.handle} на @{new_member.handle}?"
        ctx.reply(
            participant_reply,
            keyboard=inline_keyboard(
                {'Да': 'handle_changed_yes', 'Нет': 'handle_changed_no'},
                row_width=2
            )
        )
        return
    
    if new_member:
        new_member.external_id = sender_id
        send_authentication_confirmation(new_member)
        return
    
    ctx.reply(f"Пользователь не найден. Если ты регистрировался(-лась) на олимпиаду, напиши {OWNER_HANDLES[ctx.platform]}")


@router.callback(prefix='handle_changed_')
def handle_change_handler(ctx: Context):
    handle_changed = ctx.data.endswith('_yes')
    ctx.edit_keyboard(None)
    if not handle_changed:
        ctx.reply(f"Что-то пошло не так. Напиши {OWNER_HANDLES[ctx.platform]}")
        return
    old_user = User.from_external_id(ctx.platform, ctx.sender_id)
    new_user = User.from_handle(ctx.platform, ctx.sender_handle)
    old_user.conflate_with(new_user)
    user_id = old_user.user_id
    olymp_id = current_olymp.id
    member: Participant | Examiner | None = \
        Participant.from_user_id(user_id, olymp_id, no_error=True) or Examiner.from_user_id(user_id, olymp_id, no_error=True)
    if member:
        send_authentication_confirmation(member)
        ctx.answer()
        return


def send_authentication_confirmation(member: OlympMember, *, already_authenticated: bool = False):
    response = (("Ты уже авторизован(-а) как " if already_authenticated else "Ты успешно авторизовался(-лась) как ")
                + ("участник" if isinstance(member, Participant) else "принимающий")
                + "!\n" + member.display_data())
    if isinstance(member, Examiner):
        if current_olymp.status != OlympStatus.CONTEST:
            response += "\nЧтобы выбрать задачи для приёма, используй команду /choose_problems"
        elif member.is_busy:
            response += "\n❗️ Чтобы начать принимать задачи, используй команду /free"
        response += ("\nЧтобы просмотреть информацию о себе, используй команду /my_info"
                     "\nПолный список команд: /help")
    elif current_olymp.status == OlympStatus.REGISTRATION:
        response += ("\n\nДата и время начала олимпиады есть в <a href=\"vk.ru/hseling.for.school\">нашей группе ВКонтакте</a> "
                     "и в <a href=\"t.me/hselingforschool\">нашем Телеграм-канале</a>\n"
                     "Когда олимпиада начнётся, бот пришлёт тебе задания и ты сможешь записываться на сдачу задач через него")
    else:
        response += "\n❗️ Олимпиада уже началась!"
    notifier.notify(member, response)
    if current_olymp.status == OlympStatus.CONTEST and isinstance(member, Participant) and not member.finished:
        notifier.notify_photo(
            member,
            Media(BUTTONS_IMG, filename="Где_кнопки.png"),
            caption=BUTTON_HELP,
            keyboard=participant_keyboard,
            show_caption_above_media=True
        )
        send_all_problem_blocks(member)


def send_all_problem_blocks(participant: Participant):
    for problem_block_number in range(1, participant.last_block_number+1):
        problem_block = participant.problem_block_from_number(problem_block_number)
        notifier.notify_document(
            participant,
            Media(problem_block.path or ProblemBlock.DEFAULT_PATH, filename=f"Блок_{problem_block_number}.pdf")
        )


@router.message(commands=['help'], roles=['owner', 'examiner', 'participant'])
def help(ctx: Context):
    commands = [[["help", "Показать список команд"]]]
    roles = []
    if ctx.is_owner:
        roles.append('owner')
    if current_olymp and Examiner.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id, no_error=True):
        roles.append('examiner')
    if current_olymp and Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id, no_error=True):
        roles.append('participant')
    several_roles = (len(roles) > 1)
    for role in roles:
        with open(os.path.join("help", f"{role}.json"), encoding="utf8") as f:
            data = json.load(f)
        if several_roles:
            commands.append(data["title"])
        commands += data["commands"]
    response = ""
    for block in commands:
        if isinstance(block, str):
            ctx.reply(response)
            response = f"<strong>{block}</strong>\n\n"
            continue
        for command_description in block:
            command, description = tuple(command_description)
            command = ("/" + command) if (" " not in command) else f"<code>/{escape_html(command)}</code>"
            response += command + " — " + description + "\n"
        response += "\n"
    ctx.reply(response)


@router.message(
    regexp=rf"/my_stats|{MY_STATS_BUTTON}",
    roles=['participant'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def participant_stats(ctx: Context):
    participant: Participant = Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    response = f"Информация о сдачах задач:\n"
    sum, problem_info = participant.results()
    for i, (problem, success, number) in enumerate(problem_info):
        response += f"- <strong>{i+1}: {problem}</strong> — "
        if success:
            response += f"решена ({number} {decline(number, 'балл', ('', 'а', 'ов'))})\n"
        else:
            response += (f"не решена, {decline(number, 'остал', ('ась', 'ось', 'ось'))} "
                         f"{number} {decline(number, 'попыт', ('ка', 'ки', 'ок'))} из 3\n")
    response += f"Набрано баллов: <strong>{sum}</strong>"
    ctx.reply(
        response,
        keyboard=REMOVE_KEYBOARD if participant.finished or current_olymp.status == OlympStatus.RESULTS else None
    )


@router.message(
    commands=["my_info"],
    roles=['participant'],
    olymp_statuses=[OlympStatus.REGISTRATION, OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def participant_info(ctx: Context):
    participant: Participant = Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    response = (f"Информация о тебе:\n"
                f"{participant.display_data(contact_note=False)}")
    if current_olymp.status != OlympStatus.REGISTRATION:
        response += "\nПросмотреть информацию о сданных задачах можно при помощи команды /my_stats"
    response += f"\nЕсли в данных есть ошибка, сообщи {OWNER_HANDLES[ctx.platform]}"
    ctx.reply(response)


@router.message(
    commands=["my_info"],
    roles=['examiner'],
    olymp_statuses=[OlympStatus.REGISTRATION, OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def examiner_info(ctx: Context):
    examiner: Examiner = Examiner.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    response = (f"Информация о тебе:\n"
                f"{examiner.display_data(verbose=True, olymp_status=current_olymp.status)}")
    ctx.reply(response)


@router.message(commands=['olymp_registration_start', 'olymp_reg_start'], roles=['owner'])
def olymp_reg_start(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.REGISTRATION:
        raise UserError("Регистрация уже идёт")
    if current_olymp.status != OlympStatus.TBA:
        raise UserError("Олимпиада уже идёт или завершилась")
    current_olymp.status = OlympStatus.REGISTRATION
    participants = current_olymp.get_participants()
    examiners = current_olymp.get_examiners()
    for p in participants:
        if p.external_id:
            p_message = (f"Мы запустили авторизацию в боте для онлайн-участников. Тебя авторизовали автоматически!\n"
                         f"{p.display_data()}")
            notifier.notify(p, p_message)
    for e in examiners:
        if e.external_id:
            e_message = (f"Мы запустили авторизацию в боте. Тебя автоматически авторизовали как принимающего!\n"
                         f"{e.display_data()}")
            notifier.notify(e, e_message)
    ctx.reply(f"Регистрация на олимпиаду <em>{current_olymp.name}</em> запущена")


def start_olymp():
    global current_olymp
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.TBA:
        raise UserError("Сначала необходимо запустить регистрацию")
    if current_olymp.status != OlympStatus.REGISTRATION:
        raise UserError("Олимпиада уже идёт или завершилась")
    current_olymp.status = OlympStatus.CONTEST
    participants = current_olymp.get_participants()
    def participant_start_message(platform):
        return (f"Олимпиада началась! Можешь приступать к решению задач\n"
                f"Если у тебя возникли вопросы, обращайся к "
                f"{' или '.join(ASK_PEOPLE_HANDLES.get(platform, []))} (организационные вопросы) "
                f"или к {OWNER_HANDLES[platform]} (функционирование бота)")
    err_amount = 0
    receivers = 0
    send_error = None
    for p in participants:
        if p.external_id:
            try:
                notifier.notify_photo(
                    p,
                    Media(BUTTONS_IMG, filename="Где_кнопки.png"),
                    caption=BUTTON_HELP,
                    keyboard=participant_keyboard,
                    show_caption_above_media=True
                )
                problem_block = p.last_block
                notifier.notify_document(
                    p,
                    Media(problem_block.path or ProblemBlock.DEFAULT_PATH, filename="Блок_1.pdf"),
                    caption=participant_start_message(p.platform)
                )
                receivers += 1
            except Exception as exc:
                err_amount += 1
                send_error = exc
    examiners = current_olymp.get_examiners()
    for e in examiners:
        if e.external_id:
            state_store.delete(e.platform, e.external_id)
            try:
                notifier.notify(
                    e,
                    "Олимпиада началась! Напиши /free и ожидай участников\nСписок команд: /help",
                    keyboard=REMOVE_KEYBOARD,
                )
                receivers += 1
            except Exception as exc:
                err_amount += 1
                send_error = exc
    owner_message = f"Олимпиада <em>{current_olymp.name}</em> начата"
    if err_amount:
        owner_message += (
            f"\n⚠️ Не удалось доставить сообщение {err_amount} {decline(err_amount, 'пользовател', ('ю', 'ям', 'ям'))}"
        )
    notifier.send_text(OWNER_PLATFORM, OWNER_ID, owner_message)
    if err_amount and (err_amount > receivers / 2):
        raise send_error


@router.message(commands=['olymp_start'], roles=['owner'])
def olymp_start(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.TBA:
        raise UserError("Сначала необходимо запустить регистрацию")
    if current_olymp.status != OlympStatus.REGISTRATION:
        raise UserError("Олимпиада уже идёт или завершилась")
    problem_blocks = current_olymp.get_problem_blocks()
    junior_blocks = 0
    senior_blocks = 0
    fileless_blocks = 0
    for pb in problem_blocks:
        if not pb.block_type:
            continue
        if not pb.path:
            fileless_blocks += 1
        if pb.block_type.is_junior:
            junior_blocks += 1
        if pb.block_type.is_senior:
            senior_blocks += 1
    jb_warning = (junior_blocks < 3)
    sb_warning = (senior_blocks < 3)
    files_warning = (fileless_blocks > 0)
    if not (jb_warning or sb_warning or files_warning):
        start_olymp()
        return
    response = "Осторожно! Ты собираешься начать олимпиаду, в которой"
    if jb_warning and sb_warning: 
        response += " только"
    if jb_warning:
        response += f" {junior_blocks} {decline(junior_blocks, 'блок', ('', 'а', 'ов'))} задач для младших"
    if jb_warning and sb_warning:
        response += " и"
    if sb_warning:
        response += f" {senior_blocks} {decline(senior_blocks, 'блок', ('', 'а', 'ов'))} задач для старших"
    if jb_warning or sb_warning:
        response += " классов"
    if (sb_warning or jb_warning) and files_warning: 
        response += ", а ещё"
    if files_warning: 
        response += f" {fileless_blocks} {decline(fileless_blocks, 'блок', ('у', 'ам', 'ам'))} не назначен файл"
    ctx.reply(response, keyboard=inline_keyboard({
        'Всё равно начать': 'start_olymp_confirm',
        'Отмена': 'start_olymp_cancel'
    }))


@router.callback(prefix='start_olymp_')
def olymp_start_confirmation_handler(ctx: Context):
    confirmed = ctx.data.endswith('confirm')
    ctx.delete()
    if not confirmed:
        ctx.reply("Действие отменено")
        return
    start_olymp()


def finish_olymp():
    if not current_olymp or current_olymp.status not in [OlympStatus.CONTEST, OlympStatus.QUEUE]:
        raise UserError("Олимпиады нет или она ещё не начата/уже завершена")
    participants_left = current_olymp.participants_amount(finished=False)
    if participants_left == 0:
        current_olymp.status = OlympStatus.RESULTS
        e_message = "Олимпиада завершилась! Очередь пуста, так что можешь идти отдыхать"
        owner_message = "Олимпиада завершена. Очередь полностью обработана"
    else:
        e_message = (
            f"Олимпиада частично завершилась, {decline(participants_left, 'продолжа', ('ет', 'ют', 'ют'))} "
            f"участие {participants_left} {decline(participants_left, 'участник', ('', 'а', 'ов'))}. "
            f"Если ты знаешь, что тебе не надо {'его' if participants_left == 1 else 'их'} проверять, "
            f"то <strong>поставь статус /busy</strong> и можешь идти отдыхать"
        )
        owner_message = (
            f"Олимпиада частично завершена, {decline(participants_left, 'продолжа', ('ет', 'ют', 'ют'))} "
            f"участие {participants_left} {decline(participants_left, 'участник', ('', 'а', 'ов'))}"
        )
    receivers = 0
    err_amount = 0
    for e in current_olymp.get_examiners():
        if e.external_id:
            try:
                notifier.notify(e, e_message)
                receivers += 1
            except Exception as send_error:
                err_amount += 1
    notifier.send_text(OWNER_PLATFORM, OWNER_ID, owner_message)
    if err_amount and (err_amount > receivers / 2):
        raise send_error


@router.message(commands=['olymp_finish'], roles=['owner'])
def olymp_finish(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.RESULTS:
        raise UserError("Олимпиада уже завершена")
    if current_olymp.status == OlympStatus.QUEUE:
        raise UserError("Олимпиада уже завершается, происходит работа с очередью")
    if current_olymp.status != OlympStatus.CONTEST:
        raise UserError("Олимпиада ещё не начата")
    
    include_tags, exclude_tags = get_tags_args(ctx.text)
    tags_defined = not(include_tags is None and exclude_tags is None)
    participants = current_olymp.get_participants(finished=False, include_tags=include_tags, exclude_tags=exclude_tags)
    if not participants:
        if tags_defined:
            raise UserError("По указанным фильтрам нет участников, для которых олимпиада ещё не завершена")
        else:
            return finish_olymp()
    examiners = current_olymp.get_examiners()
    p_not_in_queue_message = ("Олимпиада завершилась! Больше записываться в очередь нельзя. "
                              "Можешь отправляться на заслуженный отдых")
    p_in_queue_message = ("Олимпиада завершилась! Больше записываться в очередь нельзя, "
                          "но тех, кто уже записался, мы проверим, так что не уходи")
    err_amount = 0
    receivers = 0
    for p in participants:
        p.finished = True
        if p.external_id:
            try:
                is_in_queue = p.queue_entry is not None
                notifier.notify(
                    p,
                    p_in_queue_message if is_in_queue else p_not_in_queue_message,
                    keyboard = None if is_in_queue else participant_keyboard_olymp_finished
                )
                receivers += 1
            except Exception as send_error:
                err_amount += 1
    if current_olymp.unhandled_queue_left(finished=True):
        participants_left = current_olymp.participants_amount(finished=False)
        if participants_left == 0:
            current_olymp.status = OlympStatus.QUEUE
            e_message = "Олимпиада завершилась! "
            owner_message = "Олимпиада завершена. Идёт работа с очередью"
        else:
            e_message = "Олимпиада частично завершилась! "
            owner_message = "Олимпиада завершена для части участников. Идёт работа с очередью"
        e_message += ("Но мы ещё работаем с очередью, так что не уходи раньше времени. "
                      "Если ты завершил(-а) проверку, и даже в статусе /free к тебе никто "
                      "не идёт — тогда можешь идти отдыхать")
        for e in examiners:
            if e.external_id:
                try:
                    notifier.notify(e, e_message)
                    receivers += 1
                except Exception as send_error:
                    err_amount += 1
        ctx.reply(owner_message)
        if err_amount and (err_amount > receivers / 2):
            raise send_error
    else:
        finish_olymp()


@router.message(commands=['olymp_list'], roles=['owner'])
def olymp_list(ctx: Context):
    olymps = Olymp.list_all()
    amount = len(olymps)
    response = f"Имеется {amount} {decline(amount, 'олимпиад', ('а', 'ы', ''))}" + (":" if amount > 0 else "")
    for olymp in olymps:
        response += f"\n- <em>{olymp.name}</em> (<code>{olymp.status.name}</code>)"
    ctx.reply(response)


@router.message(commands=['olymp_select'], roles=['owner'])
def olymp_select(ctx: Context):
    global current_olymp
    if current_olymp and current_olymp.status != OlympStatus.RESULTS:
        raise UserError("Заверши текущую олимпиаду, чтобы выбрать другую")
    olymp_name = get_arg(ctx.text, "Необходимо указать название олимпиады")
    if current_olymp and current_olymp.name == olymp_name:
        raise UserError(f"Олимпиада <em>{olymp_name}</em> уже выбрана как текущая")
    current_olymp = Olymp.from_name(olymp_name)
    ctx.reply(f"Олимпиада <em>{current_olymp.name}</em> выбрана как текущая")


@router.message(commands=['olymp_create'], roles=['owner'])
def olymp_create(ctx: Context):
    global current_olymp
    if current_olymp and current_olymp.status != OlympStatus.RESULTS:
        ctx.reply(
            f"Уже имеется незавершённая олимпиада <em>{current_olymp.name}</em>. Заверши её, чтобы создать новую"
        )
        return
    name = get_arg(ctx.text, "Для олимпиады необходимо название")
    current_olymp = Olymp.create(name)
    ctx.reply(f"Олимпиада <em>{current_olymp.name}</em> успешно создана")


def upload_members(ctx: Context, required_key: str, key_description: str, member_class: type[OlympMember],
                   term_stem: str, term_endings: tuple[str, str, str], term_endings_gen: tuple[str, str, str]):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status not in [OlympStatus.TBA, OlympStatus.REGISTRATION]:
        raise UserError("Олимпиада уже начата")
    required_columns = ["name", "surname", "platform", "handle", required_key]
    file_data = get_document(ctx, "Необходимо указать Excel-таблицу", ".xlsx")
    member_table = pd.read_excel(BytesIO(file_data))
    if not set(required_columns).issubset(set(member_table.columns)):
        raise UserError("Таблица должна содержать столбцы " + ', '.join([f'<code>{col}</code>' for col in required_columns]))
    old_members_amount = 0
    updated_users: list[tuple[User | OlympMember, OlympMember]] = []
    for _, m in member_table.iterrows():
        platform = Platform.from_code(m["platform"])
        handle = m["handle"]
        old_user = None
        if user := User.from_handle(platform, handle, no_error=True):
            if user.name != m["name"] or user.surname != m["surname"]:
                old_user = user
        if member := member_class.from_handle(platform, handle, current_olymp.id, no_error=True):
            value = member._additional_values[required_key]
            if value != m[required_key]:
                old_user = member
            old_members_amount += 1
        member: OlympMember = member_class.create_as_new_user(
            handle, m['name'], m['surname'], m[required_key],
            olymp_id=current_olymp.id, platform=platform,
            ok_if_user_exists=True, ok_if_exists=True
        )
        if old_user:
            updated_users.append((old_user, member))
    amount = member_table.shape[0] - old_members_amount
    response = (f"{amount} {decline(amount, term_stem, term_endings)} успешно "
                f"{decline(amount, 'добавлен', ('', 'ы', 'ы'))} в олимпиаду <em>{current_olymp.name}</em>")
    if old_members_amount > 0:
        response += (f"\n{old_members_amount} {decline(old_members_amount, term_stem, term_endings)} "
                     f"уже {decline(old_members_amount, 'был', ('', 'и', 'и'))} зарегистрированы в олимпиаде")
    updated_amount = len(updated_users)
    if updated_amount > 0:
        response += f"\nУ {updated_amount} {decline(updated_amount, term_stem, term_endings_gen)} обновилась информация:"
        for old_user, member in updated_users:
            if isinstance(old_user, member_class):
                response += (f"\n- {old_user.full_name}, "
                             f"{key_description.format(old_user._additional_values[required_key])} "
                             f"→ {member.full_name}, "
                             f"{key_description.format(member._additional_values[required_key])} "
                             f"({member.display_handle()})")
            else:
                response += f"\n- {old_user.full_name} → {member.full_name} ({member.display_handle()})"
    response += (f"\nЧтобы просмотреть список {term_stem}{term_endings_gen[2]}, "
                 f"используй команду /list_{member_class.__name__.lower()}s")
    ctx.reply(response)


@router.message(doc_commands=['upload_participants', 'upload_examiners'], roles=['owner'], content_types=['text', 'document'])
def upload_members_command(ctx: Context):
    command = extract_command(ctx.text or ctx.caption)
    if command == 'upload_participants':
        upload_members(
            ctx, "grade", "{0} класс",
            Participant, 'участник', ('', 'а', 'ов'), ('а', 'ов', 'ов')
        )
    elif command == 'upload_examiners':
        upload_members(
            ctx, "conference_link", "Ссылка на конференцию: {0}",
            Examiner, 'принимающ', ('ий', 'их', 'их'), ('его', 'их', 'их')
        )


def add_member(ctx: Context, min_args: int, max_args: int, no_arg_error: str,
               additional_values_func: Callable[[list[str]], dict[str]],
               member_class: type[OlympMember], term_pl_gen: str, ok_if_exists: bool = False):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    identity, name, surname, *other_args = get_n_args(
        ctx.text, min_args, max_args, no_arg_error
    )
    platform, handle = parse_identity_arg(identity)
    other_args = additional_values_func(other_args)
    member: OlympMember = member_class.create_as_new_user(
        handle, name, surname, olymp_id=current_olymp.id, platform=platform,
        **other_args, ok_if_user_exists=True, ok_if_exists=ok_if_exists
    )
    ctx.reply(f"{member} добавлен(-а) в список {term_pl_gen}")


@router.message(commands=['add_participant', 'add_examiner'], roles=['owner'])
def add_member_command(ctx: Context):
    command = extract_command(ctx.text)
    if command == 'add_participant':
        add_member(
            ctx, 4, 5, "Необходимо указать хэндл, имя, фамилию, класс и, при необходимости, последний блок участника",
            lambda args: {'grade': int(args[0]), 'last_block_number': int(args[1]) if len(args) > 1 else None},
            Participant, "участников"
        )
    else:
        add_member(
            ctx, 5, 5, "Необходимо указать хэндл, имя, фамилию, ссылку на конференцию и задачи принимающего",
            lambda args: {'conference_link': args[0], 'problems': list(map(int, args[1].split())) if args[1][0] != '0' else None},
            Examiner, "принимающих"
        )


@router.message(commands=['add_test_members'], roles=['owner'])
def add_test_members(ctx: Context):
    ctx.text += f" {ctx.platform.code}@{ctx.sender_handle} OWNER OWNER"
    add_member(
        ctx, 3, 3, "",
        lambda _: {'grade': 8, 'last_block_number': None},
        Participant, "участников", ok_if_exists=True
    )
    add_member(
        ctx, 3, 3, "",
        lambda _: {'conference_link': "example.com", 'problems': [pr.id for pr in current_olymp.get_problems()]},
        Examiner, "принимающих", ok_if_exists=True
    )


def edit_member(
    ctx: Context, member_class: type[OlympMember],
    other_args: dict[str, tuple[str, Callable[[OlympMember], str], Callable[[OlympMember, str], None]]]
):
    """
    Изменить данные члена олимпиады

    :param ctx: Контекст события
    :type ctx: `Context`

    :param member_class: Класс типа члена олимпиады
    :type member_class: Подкласс `OlympMember`

    :param other_args: Возможные аргументы кроме `handle`, `name` и `surname`.
    Словарь, где ключ — ключ аргумента, а значение — кортеж русского названия и двух функций,
    `getter(member)` и `setter(member, value)`
    :type other_args: {`str`: (`str`, `getter(member) -> str`, `setter(member, value)`)}
    """
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    possible_keys = ['handle', 'name', 'surname'] + list(other_args.keys())
    def handle_setter(member: OlympMember, value: str): member.handle = value
    other_args['handle'] = (
        'Хэндл',
        lambda member: member.display_handle(hide_id=True),
        handle_setter
    )
    def name_setter(member: OlympMember, value: str): member.name = value
    other_args['name'] = ('Имя', lambda member: member.name, name_setter)
    def surname_setter(member: OlympMember, value: str): member.surname = value
    other_args['surname'] = ('Фамилия', lambda member: member.surname, surname_setter)
    command = extract_command(ctx.text)
    syntax_hint = ("Синтаксис команды: <code>"
                   + escape_html("/" + command + " <messenger@handle> <" + "|".join(possible_keys) + "> <значение>")
                   + "</code>")
    identity, key, value = get_n_args(ctx.text, 3, 3, syntax_hint)
    if key not in possible_keys:
        raise UserError(syntax_hint)
    if key == 'grade': value = int(value)
    member: OlympMember = resolve_member(identity, member_class, current_olymp.id)
    old_value = other_args[key][1](member)
    other_args[key][2](member, value)
    change = f"{other_args[key][0]}: {old_value} → {value}"
    ctx.reply(f"Данные участника {member} обновлены:\n" + change)
    if member.external_id:
        notifier.notify(
            member,
            f"Твои данные обновлены:\n" + change + "\nПросмотреть информацию о себе можно при помощи команды /my_info"
        )


@router.message(commands=['edit_participant', 'edit_examiner'], roles=['owner'])
def edit_member_command(ctx: Context):
    command = extract_command(ctx.text)
    if command == 'edit_participant':
        def grade_setter(participant: Participant, value: str): participant.grade = int(value)
        edit_member(
            ctx, Participant,
            {'grade': ('Класс участия', lambda participant: participant.grade, grade_setter)}
        )
    else:
        def conference_link_setter(examiner: Examiner, value: str): examiner.conference_link = value
        edit_member(
            ctx, Examiner,
            {'conference_link': ('Ссылка на конференцию', lambda examiner: examiner.conference_link, conference_link_setter)}
        )


@router.message(commands=['create_tag'], roles=['owner'])
def create_tag(ctx: Context):
    name, description = get_n_args(ctx.text, 2, 2, "Необходимо указать код и описание")
    tag = Tag.create(name, description)
    ctx.reply(f"Тэг <code>{escape_html(tag.name)}</code> <em>{tag}</em> создан (<code>{tag.id}</code>)")


@router.message(commands=['edit_tag'], roles=['owner'])
def edit_tag(ctx: Context):
    possible_keys = ['name', 'description']
    syntax_hint = ("Синтаксис команды: <code>"
                   + escape_html("/edit_tag <хэндл> <" + "|".join(possible_keys) + "> <значение>")
                   + "</code>")
    tag_name, key, value = get_n_args(ctx.text, 3, 3, syntax_hint)
    if key not in possible_keys:
        raise UserError(syntax_hint)
    if key == "name" and len(value.split()) > 1:
        raise UserError("Код не должен содержать пробелов")
    tag: Tag = Tag.from_name(tag_name)
    if key == "name":
        tag.name = value
        response = f"Код тэга <code>{escape_html(tag_name)}</code> обновлён на <code>{escape_html(tag.name)}</code>"
    elif key == "description":
        tag.description = value
        response = f"Описание тэга <code>{escape_html(tag.name)}</code> обновлено: <em>{tag}</em>"
    ctx.reply(response)


@router.message(commands=['list_tags'], roles=['owner'])
def list_tags(ctx: Context):
    tags = Tag.get_all()
    if len(tags) == 0:
        ctx.reply("Тэгов не задано")
        return
    response = f"{decline(len(tags), 'Задан', ('', 'о', 'о'))} {len(tags)} {decline(len(tags), 'тэг', ('', 'а', 'ов'))}:"
    for tag in tags:
        response += f"\n- <code>{escape_html(tag.name)}</code> <em>{tag}</em>"
    ctx.reply(response)


@router.message(commands=['set_tags'], roles=['owner'])
def set_tags(ctx: Context):
    identity, tags = get_n_args(ctx.text, 2, 2, "Необходимо указать хэндл пользователя и тэги")
    tags = tags.split()
    current_tags = Tag.get_all()
    current_tag_ids = set(map(lambda tag: tag.name, current_tags))
    if not set(tags).issubset(current_tag_ids):
        not_found = [tag for tag in tags if tag not in current_tag_ids]
        err = (
            f"{len(not_found)} {decline(len(not_found), 'тэг', ('', 'а', 'ов'))} "
            f"не {decline(len(not_found), 'найден', ('', 'о', 'о'))}: "
        )
        err += ", ".join(not_found)
        raise UserError(err)
    tags = list(filter(lambda tag: tag.name in tags, current_tags))
    user: User = resolve_user(identity)
    user.set_tags(tags)
    user_response = "Твои тэги обновлены:\n"
    for tag in tags:
        user_response += f"- {tag}\n"
    if user.external_id:
        notifier.notify(user, user_response)
    ctx.reply(f"Список тэгов пользователя {user} обновлён")


@router.message(commands=['clear_tags'], roles=['owner'])
def clear_tags(ctx: Context):
    identity = get_arg(ctx.text, "Необходимо указать хэндл пользователя")
    user: User = resolve_user(identity)
    user.set_tags(None)
    if user.external_id:
        notifier.notify(user, "Твои тэги удалены")
    ctx.reply(f"Тэги пользователя {user} очищены")


def return_participant_to_queue(participant: Participant):
    queue_entry = participant.queue_entry
    new_examiner_id = queue_entry.look_for_examiner()
    if new_examiner_id:
        new_examiner: Examiner = Examiner.from_id(new_examiner_id)
        new_examiner.assign_to_queue_entry(queue_entry)
        announce_queue_entry(queue_entry)
    else:
        problem = Problem.from_id(queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        participant_response = (f"Вернули тебя в начало очереди на задачу {problem_number}: {problem}. "
                                f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится")
        if PROMOTE_COMMANDS:
            participant_response += f"Чтобы покинуть очередь, используй команду /leave_queue"
        notifier.notify(participant, participant_response, keyboard=participant_keyboard_in_queue)


@router.message(commands=['set_examiner_problems'], roles=['owner'])
def set_examiner_problems(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    identity, problems = get_n_args(ctx.text, 2, 2, "Необходимо указать хэндл принимающего и задачи")
    problems = list(map(int, problems.split())) if problems[0] != '0' else None
    if problems:
        current_problems = set(map(lambda problem: problem.id, current_olymp.get_problems()))
        if not set(problems).issubset(current_problems):
            raise UserError("Можно назначать только задачи текущей олимпиады")
    examiner: Examiner = resolve_member(identity, Examiner, current_olymp.id)
    examiner.set_problems(problems)
    examiner_response = "Твой список задач обновлён:\n"
    for problem_id in problems:
        examiner_response += f"- {Problem.from_id(problem_id)}\n"
    if examiner.external_id:
        notifier.notify(examiner, examiner_response)
    ctx.reply(f"Список задач принимающего {examiner} обновлён")
    queue_entry = examiner.queue_entry
    if queue_entry and queue_entry.problem_id not in examiner.problems:
        participant: Participant = Participant.from_id(queue_entry.participant_id)
        problem: Problem = Problem.from_id(queue_entry.problem_id)
        examiner.withdraw_from_queue_entry()
        notifier.notify(
            examiner,
            f"Ты больше не можешь принимать задачу {problem}. "
            f"Бот снял тебя с приёма задачи у участника {participant.full_name}\n"
            f"❗️ Бот установил тебе статус \"занят(-а)\". Пожалуйста, используй команду /free, чтобы продолжить принимать задачи!",
            keyboard=REMOVE_KEYBOARD
        )
        return_participant_to_queue(participant)
    elif not examiner.is_busy:
        queue_entry = examiner.look_for_queue_entry()
        if queue_entry:
            examiner.assign_to_queue_entry(queue_entry)
            announce_queue_entry(queue_entry)


def view_member(
    ctx: Context, member_class: type[OlympMember], member_name: str, member_name_gen: str
):
    """
    Просмотреть данные члена олимпиады

    :param ctx: Контекст события
    :type ctx: `Context`

    :param member_class: Класс типа члена олимпиады
    :type member_class: Подкласс `OlympMember`

    :param member_name: Русское название члена олимпиады
    :type member_name: `str`

    :param member_name: Русское название члена олимпиады в родительном падеже
    :type member_name: `str`
    """
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    identity = get_arg(ctx.text, "Необходимо указать хэндл " + member_name_gen)
    member: OlympMember = resolve_member(identity, member_class, current_olymp.id)
    response = (f"<strong>{member_name.capitalize()} <code>{member.id}</code>:</strong>\n"
                + member.display_data(verbose=True, olymp_status=current_olymp.status, technical_info=True, contact_note=False))
    ctx.reply(response)


@router.message(commands=['view_participant', 'view_examiner'], roles=['owner'])
def view_member_command(ctx: Context):
    command = extract_command(ctx.text)
    if command == 'view_participant':
        view_member(ctx, Participant, 'участник', 'участника')
    else:
        view_member(ctx, Examiner, 'принимающий', 'принимающего')


def list_members_page(ref: MessageRef, member_class: type[OlympMember], page: int, member_amount: int, title: str,
                      get_func: Callable[[int, int], list[OlympMember]]):
    """
    Отредактировать сообщение `ref`, чтобы отобразить страницу `page` списка членов олимпиады

    :param ref: Сообщение (от бота)
    :type ref: `MessageRef`

    :param member_class: Класс типа члена олимпиады
    :type member_class: Подкласс `OlympMember`

    :param page: Номер страницы
    :type page: `int`

    :param member_amount: Количество членов олимпиады
    :type member_amount: `int``

    :param title: Заголовок сообщения о списке
    :type title: `str`

    :param get_func: Функция получения списка из номера первого члена олимпиады и длины списка
    :type get_func: get_list(start, limit) -> `list[OlympMember]`
    """
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    member_class_name = member_class.__name__.lower()
    callback_invalid = f'page_list_{member_class_name}_invalid'
    if member_amount == 0:
        buttons = {'← X': callback_invalid, 'X →': callback_invalid}
        notifier.edit_text(title, keyboard=inline_keyboard(buttons), ref=ref)
        return
    max_page = (member_amount-1) // MEMBER_PAGE_SIZE + 1
    if page > max_page:
        page = max_page
    text = title + f"\nСтраница {page}/{max_page}"
    buttons = {}
    if page == 1:
        buttons['← X'] = callback_invalid
    else:
        buttons[f'← {page-1}'] = f'page_list_{member_class_name}_{page-1}'
    if page == max_page:
        buttons['X →'] = callback_invalid
    else:
        buttons[f'{page+1} →'] = f'page_list_{member_class_name}_{page+1}'
    member_list = get_func((page-1)*MEMBER_PAGE_SIZE, MEMBER_PAGE_SIZE)
    for member in member_list:
        text += f"\n- {member}"
    text += f"\nЧтобы просмотреть подробную информацию о ком-то одном, используй команду <code>/view_{member_class_name}</code>"
    notifier.edit_text(text, keyboard=inline_keyboard(buttons), ref=ref)


def list_participants_page(ref: MessageRef, page: int):
    amount = current_olymp.participants_amount()
    list_members_page(
        ref, Participant, page, amount,
        f"В олимпиаде {decline(amount, 'участву', ('ет', 'ют', 'ет'))} {amount} {decline(amount, 'человек', ('', 'а', ''))}",
        lambda start, limit: current_olymp.get_participants(start, limit, sort=True)
    )


def list_examiners_page(ref: MessageRef, page: int):
    amount = current_olymp.examiners_amount()
    list_members_page(
        ref, Examiner, page, amount,
        f"В олимпиаде {amount} {decline(amount, 'принимающ', ('ий', 'их', 'их'))}",
        lambda start, limit: current_olymp.get_examiners(start, limit, sort=True)
    )


@router.message(commands=['list_participants', 'list_examiners'], roles=['owner'])
def list_members_command(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    reply = ctx.reply("Обработка запроса...", keyboard=inline_keyboard({}))
    command = extract_command(ctx.text)
    if command == 'list_participants':
        list_participants_page(reply, 1)
    else:
        list_examiners_page(reply, 1)


@router.callback(prefix='page_list_')
def list_members_page_handler(ctx: Context):
    member_type, page = ctx.data[len('page_list_'):].split('_')
    if page == 'invalid':
        ctx.answer("Страницы нет")
        return
    page = int(page)
    if member_type == 'participant':
        list_participants_page(ctx.ref, page)
    else:
        list_examiners_page(ctx.ref, page)


@router.message(commands=['olymp_info'], roles=['owner'])
def olymp_info(ctx: Context):
    if not current_olymp:
        response = f"Нет текущей олимпиады"
    else:
        p_amount = current_olymp.participants_amount()
        p_finished = current_olymp.participants_amount(finished=True)
        e_amount = current_olymp.examiners_amount()
        pr_amount = current_olymp.problems_amount()
        response = (f"Олимпиада <em>{current_olymp.name}</em>:\n"
                    f"<strong>ID:</strong> <code>{current_olymp.id}</code>\n"
                    f"<strong>Статус:</strong> <code>{current_olymp.status.name}</code>\n"
                    f"{p_amount} {decline(p_amount, 'участник', ('', 'а', 'ов'))}")
        if p_finished:
            response += f" ({p_finished} {decline(p_finished, 'завершивш', ('ий', 'их', 'их'))})"
        response += (f"\n{e_amount} {decline(e_amount, 'принимающ', ('ий', 'их', 'их'))}\n"
                     f"{pr_amount} {decline(pr_amount, 'задач', ('а', 'и', ''))}")
    ctx.reply(response)


@router.message(
    commands=['last_queue_entries'], 
    roles=['owner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def last_queue_entries(ctx: Context):
    syntax_hint = ("Синтаксис команды: <code>"
                   + escape_html(
                       "/last_queue_entries [participant=<messenger@handle>] [examiner=<messenger@handle>]"
                       "[problem=<ID>] [limit=<ограничение>]"
                       )
                   + "</code>")
    args = get_n_args(ctx.text, 0, 4, syntax_hint)
    settings = {}
    for arg in args:
        match: re.Match = re.match(r"(participant|examiner|problem|limit)=(.+)", arg)
        if not match:
            raise UserError(syntax_hint)
        key, value = match.groups()
        if key in settings:
            raise UserError(syntax_hint)
        match key:
            case 'limit': value = int(value)
            case 'problem': value = Problem.from_id(value)
            case 'participant': value = resolve_member(value, Participant, current_olymp.id)
            case 'examiner': value = resolve_member(value, Examiner, current_olymp.id)
        settings[key] = value
    limit = settings.get('limit', 10)
    chosen_participant: Participant | None = settings.get('participant')
    chosen_examiner: Examiner | None = settings.get('examiner')
    chosen_problem: Problem | None = settings.get('problem')
    queue_entries = current_olymp.last_queue_entries(
        limit,
        participant=chosen_participant,
        examiner=chosen_examiner,
        problem=chosen_problem
    )
    response = f"Записи в очереди:\n"
    for queue_entry in queue_entries:
        participant: Participant = chosen_participant or Participant.from_id(queue_entry.participant_id)
        examiner: Examiner | None = (
            chosen_examiner
            or (Examiner.from_id(queue_entry.examiner_id) if queue_entry.examiner_id else None)
        )
        problem: Problem = chosen_problem or Problem.from_id(queue_entry.problem_id)
        response += (f"ЗАПИСЬ <code>{queue_entry.id}</code>\n"
                     f"- <strong>Участник:</strong> {participant} ({participant.grade} класс)\n")
        if examiner: 
            response += f"- <strong>Принимающий:</strong> {examiner}\n"
        else: response += "- <strong>Принимающий</strong> не назначен\n"
        response += (f"- <strong>Задача:</strong> <code>{problem.id}</code> {problem}\n"
                     f"- <strong>Статус:</strong> {queue_entry.status}\n")
    ctx.reply(response)
    
        

@router.message(
    commands=['update_queue_entry_status'],
    roles=['owner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def update_queue_entry_status(ctx: Context):
    id, status_text = get_n_args(ctx.text, 2, 2, "Необходимо указать ID записи и новый статус")
    if not id.isnumeric(): raise UserError("Необходимо указать ID записи и новый статус")
    id = int(id)
    status = QueueStatus.from_text(status_text)
    queue_entry = QueueEntry.from_id(id)
    if queue_entry.olymp_id != current_olymp.id:
        raise UserError("Запись не относится к текущей олимпиаде")
    if queue_entry.status == QueueStatus.WAITING and status != QueueStatus.CANCELED:
        raise UserError(f"Нельзя менять статус ожидания на что-либо кроме отмены")
    if status == QueueStatus.WAITING:
        raise UserError(f"Нельзя устанавливать статус ожидания")
    if status == queue_entry.status:
        raise UserError(f"Статус уже {status_text.capitalize()}")
    queue_entry.status = status
    if ctx.reply_to:
        message_to_send = ctx.reply_to
        participant: Participant = Participant.from_id(queue_entry.participant_id)
        ctx.copy_to(participant, message_to_send)
    announce_queue_entry(queue_entry)
    ctx.reply("Запись обновлена")


@router.message(
    commands=['update_queue_entry_problem'],
    roles=['owner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE]
)
def update_queue_entry_problem(ctx: Context):
    id, problem_id = get_n_args(ctx.text, 2, 2, "Необходимо указать ID записи и ID новой задачи")
    if not id.isnumeric() or not problem_id.isnumeric(): raise UserError("Необходимо указать ID записи и ID новой задачи")
    id, problem_id = int(id), int(problem_id)
    queue_entry = QueueEntry.from_id(id)
    problem = Problem.from_id(problem_id)
    if problem.olymp_id != current_olymp.id:
        raise UserError(f"Задача <code>{problem_id}</code> не относится к текущей олимпиаде")
    if queue_entry.problem_id == problem_id:
        raise UserError(f"Записи <code>{id}</code> уже соответствует задача <code>{problem_id}</code> {problem}")
    if queue_entry.status not in QueueStatus.active():
        raise UserError(f"Задачу завершённого обсуждения нельзя изменить")
    participant: Participant = Participant.from_id(queue_entry.participant_id)
    if not participant.has_problem(problem):
        raise UserError(
            f"У участника {participant} нет задачи <code>{problem_id}</code> {problem}"
        )
    if participant.attempts_left(problem) <= 0 or participant.solved(problem):
        raise UserError(
            f"Участник {participant} больше не может "
            f"сдавать задачу <code>{problem_id}</code> {problem}"
        )
    queue_entry.problem_id = problem_id
    if ctx.reply_to:
        message_to_send = ctx.reply_to
        participant: Participant = Participant.from_id(queue_entry.participant_id)
        ctx.copy_to(participant, message_to_send)
    if queue_entry.status == QueueStatus.DISCUSSING:
        examiner: Examiner = Examiner.from_id(queue_entry.examiner_id)
        if problem_id in examiner.problems:
            notifier.notify(
                examiner,
                f"Участнику {participant.full_name} сменили задачу на {problem}. Обсуждайте её!"
            )
            problem_number = participant.get_problem_number(problem)
            notifier.notify(
                participant,
                f"Задачу {problem_number}: {problem} у тебя примет тот же принимающий, "
                f"{examiner.full_name}, по ссылке {examiner.conference_link}"
            )
            return
        queue_entry.status = QueueStatus.WAITING
        queue_entry.examiner_id = None
        examiner.is_busy = True
        notifier.notify(
            examiner,
            f"Участнику {participant.full_name} сменили задачу на задачу, которую ты не принимаешь\n"
            f"❗️ Бот установил тебе статус \"занят(-а)\". Пожалуйста, используй команду /free, чтобы продолжить принимать задачи!",
            keyboard=REMOVE_KEYBOARD
        )
    new_examiner_id = queue_entry.look_for_examiner()
    if new_examiner_id:
        new_examiner: Examiner = Examiner.from_id(new_examiner_id)
        new_examiner.assign_to_queue_entry(queue_entry)
    announce_queue_entry(queue_entry)
    ctx.reply("Запись обновлена")


@router.message(commands=['problem_create'], roles=['owner'])
def problem_create(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if '\n' not in ctx.text:
        name = get_arg(ctx.text, "Для задачи необходимо название")
        problem = Problem.create(current_olymp.id, name)
        ctx.reply(f"Задача {problem} добавлена! ID: <code>{problem.id}</code>")
        return
    names = ctx.text.split(sep='\n')[1:]
    problems: list[Problem] = []
    for name in names:
        if not Problem.from_name(name, current_olymp.id, no_error=True):
            problems.append(Problem.create(current_olymp.id, name))
    amount = len(problems)
    response = f"{amount} {decline(amount, 'задач', ('а', 'и', ''))} {decline(amount, 'добавлен', ('а', 'о', 'о'))}!"
    for p in problems:
        response += f"\n- <code>{p.id}</code> {p}"
    old_amount = len(names) - amount
    if old_amount > 0:
        response += f"\n{old_amount} {decline(old_amount, 'задач', ('а', 'и', ''))} уже есть в олимпиаде"
    ctx.reply(response)


@router.message(commands=['problem_rename'], roles=['owner'])
def problem_rename(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    id, name = get_n_args(ctx.text, 2, 2, "Необходимо указать ID задачи и новое название")
    problem = Problem.from_id(int(id))
    if problem.olymp_id != current_olymp.id:
        raise UserError("Задача не относится к текущей олимпиаде")
    problem.name = name
    ctx.reply(f"Задача <code>{problem.id}</code> переименована: {problem}")


@router.message(commands=['problem_delete'], roles=['owner'])
def problem_delete(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    id = get_arg(ctx.text, "Необходимо указать ID задачи")
    problem = Problem.from_id(int(id))
    if problem.olymp_id != current_olymp.id:
        raise UserError("Задача не относится к текущей олимпиаде")
    problem.delete()
    ctx.reply(f"Задача <code>{problem.id}</code> {problem} удалена")


@router.message(commands=['problem_list'], roles=['owner', 'examiner'])
def problem_list(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problems = current_olymp.get_problems(sort=True)
    if len(problems) == 0:
        ctx.reply(f"В олимпиаде <em>{current_olymp.name}</em> ещё нет задач")
        return
    response = f"Задачи олимпиады <em>{current_olymp.name}</em>:"
    for p in problems:
        response += f"\n- <code>{p.id}</code> {p}"
    ctx.reply(response)


@router.message(commands=['problem_info'], roles=['owner', 'examiner'])
def problem_info(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problem = Problem.from_id(int(get_arg(ctx.text, "Необходимо указать ID задачи")))
    response = ""
    if problem.olymp_id != current_olymp.id:
        if not ctx.is_owner:
            raise UserError("Задача не найдена")
        response += "❗️ Задача не относится к текущей олимпиаде\n"
    response += (f"Информация о задаче {problem}:\n"
                f"ID: <code>{problem.id}</code>\n")
    blocks = problem.get_blocks()
    if len(blocks) == 0:
        response += "Задача не входит ни в какие блоки"
    else:
        response += "Блоки задач:"
        for block in blocks:
            response += f"\n- {block}"
    ctx.reply(response)


@router.message(doc_commands=['problem_block_create'], roles=['owner'], content_types=['text', 'document'])
def problem_block_create(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    file = get_document(ctx, "Необходим файл с условиями задач", ".pdf")
    args = get_n_args(ctx.text, 3, 4, "Необходимо указать задачи для блока")
    problems = list(map(int, args[:3]))
    block_type = None
    if len(args) > 3:
        if not re.match(r"^(JUNIOR|SENIOR)_[123]$", args[3]):
            raise UserError("Тип блока должен быть указан в форме <code>(JUNIOR|SENIOR)_(1|2|3)</code>")
        block_type = BlockType[args[3]]
    path = save_downloaded_file(file)
    problem_block = ProblemBlock.create(current_olymp.id, problems, block_type=block_type, path=path)
    response = (f"Блок {problem_block} создан!\n"
                f"Задачи:")
    for problem in problem_block.problems:
        response += f"\n- <code>{problem.id}</code> {problem}"
    ctx.reply(response)


@router.message(commands=['test_problem_blocks_create'], roles=['owner'])
def test_problem_blocks_create(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    path = os.path.join("predefined_files", "default_problem_block.pdf")
    response_lines = ["Создал блоки:"]
    for name in ["JUNIOR", "SENIOR"]:
        problems: list[list[Problem]] = [[]]
        for no in range(1, 10):
            if len(problems[-1]) >= 3: problems.append([])
            problems[-1].append(Problem.create(current_olymp.id, f"{name}, {no}", no_error=True))
        for degree in range(1, 4):  
            block_type = BlockType[f"{name}_{degree}"]
            try:
                problem_block = ProblemBlock.create(current_olymp.id, problems[degree-1], block_type=block_type, path=path)
                response_lines.append(f"- <code>{problem_block.id}</code>")
            except UserError:
                pass
    ctx.reply("\n".join(response_lines))


@router.message(commands=['problem_block_list'], roles=['owner', 'examiner'])
def problem_block_list(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problem_blocks = current_olymp.get_problem_blocks()
    if len(problem_blocks) == 0:
        ctx.reply(f"В олимпиаде <em>{current_olymp.name}</em> ещё нет задачных блоков")
        return
    response = f"Задачные блоки олимпиады <em>{current_olymp.name}</em>:"
    for pb in problem_blocks:
        response += f"\n- <code>{pb.id}</code>"
        if pb.block_type:
            response += f" <em>{pb.block_type}</em>"
        if not pb.path:
            response += " (Нет файла!)"
    ctx.reply(response)


def get_problem_block_from_arg(arg: str):
    if re.match(r"^(JUNIOR|SENIOR)_[123]$", arg):
        block_type = BlockType[arg]
        problem_block = ProblemBlock.from_block_type(current_olymp.id, block_type)
    elif arg.isnumeric():
        id = int(arg)
        problem_block = ProblemBlock.from_id(id)
    else:
        raise UserError("Необходимо указать ID или тип блока")
    if problem_block.olymp_id != current_olymp.id:
        raise UserError("Блок задач не относится к текущей олимпиаде")
    return problem_block


@router.message(commands=['problem_block_info'], roles=['owner', 'examiner'])
def problem_block_info(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problem_block = get_problem_block_from_arg(get_arg(ctx.text, "Необходимо указать ID или тип блока"))
    response = ""
    if problem_block.olymp_id != current_olymp.id:
        if not ctx.is_owner:
            raise UserError("Блок задач не найден")
        response += "❗️ Блок задач не относится к текущей олимпиаде\n"
    response += (f"Информация о блоке задач {problem_block}:\n"
                 f"ID: <code>{problem_block.id}</code>\n"
                 f"Задачи:")
    for problem in problem_block.problems:
        response += f"\n- <code>{problem.id}</code> {problem}"
    if problem_block.path:
        ctx.send_document(
            ctx.peer_id,
            Media(problem_block.path, filename=f"Блок_{problem_block.id}.pdf"),
            caption=response
        )
    else:
        response += "\nФайл не назначен"
        ctx.reply(response)


@router.message(commands=['problem_block_update_type'], roles=['owner'])
def problem_block_update_type(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status not in [OlympStatus.TBA, OlympStatus.REGISTRATION]:
        raise UserError("Менять тип блока задач можно только до начала олимпиады")
    problem_block_arg, new_type_arg = get_n_args(ctx.text, 2, 2, "Необходимо указать ID или тип блока и новый тип или 0")
    problem_block = get_problem_block_from_arg(problem_block_arg)
    if not re.match(r"^((JUNIOR|SENIOR)_[123]|0)$", new_type_arg):
        raise UserError("Тип блока должен быть указан в форме <code>(JUNIOR|SENIOR)_(1|2|3)</code> "
                        "(или <code>0</code>, чтобы убрать тип блока)")
    new_type = BlockType[new_type_arg] if new_type_arg != '0' else None
    problem_block.block_type = new_type
    ctx.reply(
        f"Тип блока <code>{problem_block.id}</code> " + (f"изменён на {new_type}" if new_type else "обнулён")
    )


@router.message(commands=['problem_block_update_file'], roles=['owner'])
def problem_block_update_file(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    file = get_document(ctx, "Необходим файл с условиями задач", ".pdf")
    problem_block = get_problem_block_from_arg(get_arg(ctx.text, "Необходимо указать ID или тип блока"))
    path = save_downloaded_file(file)
    problem_block.delete_file(no_error=True)
    problem_block.path = path
    ctx.reply(f"Файл блока {problem_block} обновлён")


@router.message(commands=['problem_block_delete_file'], roles=['owner'])
def problem_block_delete_file(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status in [OlympStatus.CONTEST]:
        raise UserError("Нельзя удалять файлы блоков после начала олимпиады")
    arg = get_arg(ctx.text, "Необходимо указать ID или тип блока, или <code>all</code>, чтобы удалить все файлы")
    if arg != "all":
        problem_block = get_problem_block_from_arg(arg)
        problem_block.delete_file()
        ctx.reply(f"Файл блока {problem_block} удалён")
        return
    problem_blocks = current_olymp.get_problem_blocks()
    deleted_files = 0
    for pb in problem_blocks:
        if pb.path:
            deleted_files += 1
            pb.delete_file()
    pl = (deleted_files > 1)
    ctx.reply(
        f"Файл{'ы' if pl else ''} {deleted_files} {decline(deleted_files, 'блок', ('а', 'ов', 'ов'))} удал{'ены' if pl else 'ён'}"
    )


@router.message(commands=['problem_block_delete'], roles=['owner'])
def problem_block_delete(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status in [OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]:
        raise UserError("Нельзя удалять блоки задач после начала олимпиады")
    problem_block = get_problem_block_from_arg(get_arg(ctx.text, "Необходимо указать ID или тип блока"))
    problem_block.id
    response = f"Блок задач {problem_block} будет удалён безвозвратно. Ты уверен?"
    ctx.reply(
        response,
        keyboard=inline_keyboard({
            'Да': f'delete_block_{problem_block.id}',
            'Нет': 'delete_block_cancel'
        })
    )


@router.callback(prefix='delete_block_')
def delete_block_handler(ctx: Context):
    ctx.delete()
    if ctx.data.endswith('_cancel'):
        ctx.reply("Действие отменено")
        return
    problem_block_id = int(ctx.data[len('delete_block_'):])
    problem_block = ProblemBlock.from_id(problem_block_id)
    problem_block.delete()
    ctx.send_text(ctx.sender_id, f"Блок {problem_block} удалён")


@router.message(commands=['choose_problems'], roles=['examiner'], olymp_statuses=[OlympStatus.REGISTRATION])
def examiner_problems(ctx: Context):
    examiner: Examiner = Examiner.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    response = "Выбери задачу, чтобы добавить её в свой список задач или убрать её из него\n" + examiner.display_problem_data()
    all_problems = current_olymp.get_problems(sort=True)
    reply_buttons = Keyboard()
    problem_row = []
    for problem in all_problems:
        problem_row.append(Button(problem.name))
        if len(problem_row) == 3:
            reply_buttons.row(*problem_row)
            problem_row = []
    if len(problem_row) > 0:
        reply_buttons.row(*problem_row)
    reply_buttons.row(Button("[Закончить выбор]"))
    ctx.state.set("choosing_problems")
    ctx.reply(response, keyboard=reply_buttons)


@router.message(state="choosing_problems", roles=['examiner'])
def examiner_chooses_problem(ctx: Context):
    if ctx.text == "[Закончить выбор]":
        ctx.state.delete()
        ctx.reply("Выбор сохранён", keyboard=REMOVE_KEYBOARD)
        return
    problem = Problem.from_name(ctx.text, current_olymp.id)
    examiner: Examiner = Examiner.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    if problem.id in examiner.problems:
        examiner.remove_problem(problem)
        response = f"Задача {problem} удалена из твоего списка задач"
    else:
        examiner.add_problem(problem)
        response = f"Задача {problem} добавлена в твой список задач"
    response += "\n" + examiner.display_problem_data()
    ctx.reply(response)


@router.message(
    commands=['free', 'busy'], roles=['examiner'], 
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE], discussing_examiner=False
)
def examiner_busyness_status(ctx: Context):
    examiner: Examiner = Examiner.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    command = extract_command(ctx.text)
    if command == 'free' and not examiner.is_busy:
        raise UserError("Ты уже свободен(-на). Если хочешь отметить, что ты занят(-а), используй команду /busy")
    if command == 'busy' and examiner.is_busy:
        raise UserError("Ты уже занят(-а). Если хочешь отметить, что ты свободен(-на), используй команду /free")
    if command == 'free' and examiner.queue_entry:
        raise UserError("Нельзя отметиться свободным(-ой) во время приёма задачи")
    examiner.is_busy = not examiner.is_busy
    if examiner.is_busy:
        response = "Теперь к тебе не будут приходить на сдачу. Если хочешь отметить, что ты свободен(-на), используй команду /free"
    else:
        response = "Теперь к тебе могут прийти сдавать задачи. Если хочешь отметить, что ты занят(-а), используй команду /busy"
    ctx.reply(response)
    if not examiner.is_busy:
        queue_entry = examiner.look_for_queue_entry()
        if queue_entry:
            examiner.assign_to_queue_entry(queue_entry)
            announce_queue_entry(queue_entry)


def announce_queue_entry(queue_entry: QueueEntry):
    participant: Participant = Participant.from_id(queue_entry.participant_id)
    problem: Problem = Problem.from_id(queue_entry.problem_id)
    problem_number = participant.get_problem_number(problem)
    p_continues = (current_olymp.status == OlympStatus.CONTEST and not participant.finished)
    
    # Нет принимающего
    if not queue_entry.examiner_id:
        if queue_entry.status == QueueStatus.WAITING:
            response = (f"Ты теперь в очереди на задачу {problem_number}: {problem}. "
                        f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится")
            if PROMOTE_COMMANDS:
                response += f"\nЧтобы покинуть очередь, используй команду /leave_queue"
            notifier.notify(participant, response, keyboard=participant_keyboard_in_queue)
            return
        elif queue_entry.status == QueueStatus.CANCELED:
            if p_continues:
                response = "Ты больше не в очереди"
                if PROMOTE_COMMANDS:
                    response += (". Чтобы записаться на сдачу задачи снова, используй команду <code>"
                                 + escape_html("/queue <номер задачи>")
                                 + "</code>")
                keyboard = participant_keyboard
            else:
                response = "Ты больше не в очереди. Олимпиада завершена, можешь отправляться на заслуженный отдых"
                keyboard = participant_keyboard_olymp_finished
            notifier.notify(participant, response, keyboard=keyboard)
            if participant.finished and not current_olymp.unhandled_queue_left(finished=True):
                finish_olymp()
            return
    
    examiner: Examiner = Examiner.from_id(queue_entry.examiner_id)

    # Приём завершён
    if queue_entry.status not in QueueStatus.active():
        new_problem_block = None
        if queue_entry.status == QueueStatus.FAIL:
            participant_response = f"Задача {problem_number}: {problem} не принята"
            if p_continues:
                attempts = participant.attempts_left(problem)
                participant_response += (f". У тебя {decline(attempts, 'остал', ('ась', 'ось', 'ось'))} "
                                        f"{attempts} {decline(attempts, 'попыт', ('ка', 'ки', 'ок'))}, "
                                        f"чтобы её сдать")
            examiner_response = (f"Задача {problem} отмечена как несданная участником {participant.full_name}")
        elif queue_entry.status == QueueStatus.CANCELED:
            participant_response = (f"Сдача задачи {problem_number}: {problem} отменена. Ты больше не в очереди")
            if p_continues:
                participant_response += ". Попытка не потрачена"
            examiner_response = (f"Сдача задачи {problem} участником {participant.full_name} отменена")
        elif queue_entry.status == QueueStatus.SUCCESS:
            participant_response = f"Задача {problem_number}: {problem} принята! Поздравляем"
            if p_continues and participant.should_get_new_problem(problem):
                new_problem_block = participant.give_next_problem_block()
                participant_response += (f"\nЗа решение этой задачи тебе полагается {participant.last_block_number} блок задач. "
                                         f"Теперь можешь сдавать и их!")
            examiner_response = (f"Задача {problem} отмечена как успешно сданная участником {participant.full_name}")
        if p_continues:
            can_continue = (participant.last_block_number != 3)
            if not can_continue:
                for _, success, attempts in participant.results()[1]:
                    if not success and (attempts > 0):
                        can_continue = True
                        break
            if not can_continue:
                participant_response += ("\n✅ У тебя не осталось задач, которые можно сдавать! Так что "
                                         "для тебя олимпиада завершена, можешь отправляться на заслуженный отдых")
                participant.finished = True
                notifier.notify(participant, participant_response, keyboard=participant_keyboard_olymp_finished)
                notifier.send_text(OWNER_PLATFORM, OWNER_ID, f"Участник {participant} завершил олимпиаду!")
                if current_olymp.participants_amount(finished=False) == 0:
                    finish_olymp()
                return
            if PROMOTE_COMMANDS:
                participant_response += ("\nЧтобы записаться на сдачу задачи, используй команду <code>"
                                        + escape_html("/queue <номер задачи>")
                                        + "</code>")
            keyboard = participant_keyboard
        else:
            participant_response += "\nОлимпиада завершена, можешь отправляться на заслуженный отдых"
            keyboard = participant_keyboard_olymp_finished
        unhandled_queue_left = current_olymp.unhandled_queue_left(finished=True)
        if current_olymp.status == OlympStatus.CONTEST or unhandled_queue_left:
            examiner_response += "\n❗️ Чтобы продолжить принимать задачи, используй команду /free"
        if new_problem_block:
            notifier.notify_document(
                participant,
                Media(
                    new_problem_block.path or ProblemBlock.DEFAULT_PATH,
                    filename=f"Блок_{participant.last_block_number}.pdf"
                ),
                caption=participant_response,
                keyboard=keyboard
            )
        else:
            notifier.notify(participant, participant_response, keyboard=keyboard)
        notifier.notify(
            examiner, examiner_response,
            keyboard=REMOVE_KEYBOARD if not examiner.queue_entry else None
        )
        if participant.finished and not unhandled_queue_left:
            finish_olymp()
        return
    
    # Принимающий только что назначен
    participant_response = (f"Задачу {problem_number}: {problem} "
                            f"у тебя примет {examiner.full_name}.\n"
                            f"Ссылка: {examiner.conference_link}")
    notifier.notify(
        participant,
        participant_response,
        keyboard=(inline_keyboard({'Принимающий не пришёл': 'examiner_didnt_come'})
                  if NO_EXAMINER_COMPLAINTS else REMOVE_KEYBOARD)
    )
    examiner_response = (f"К тебе идёт сдавать задачу {problem} "
                         f"участник {participant.full_name} ({participant.grade} класс). ")
    examiner_response += participant.display_tags(sep="; ", end=". ")
    examiner_response += (f"Ты можешь принять или отклонить решение, а также отменить сдачу (например, если участник "
                          f"не пришёл или если ты не хочешь учитывать эту сдачу как потраченную попытку)")
    if current_olymp.status == OlympStatus.QUEUE or participant.finished:
        examiner_response += (f"\n\nЕсли участник случайно нажал не на ту задачу, пожалуйста, напиши "
                              f"{OWNER_HANDLES[examiner.platform]}! И <strong>не нажимай ни на какую из кнопок!</strong>")
    examiner_keyboard = Keyboard()
    examiner_keyboard.row(Button("Принято"), Button("Не принято"), Button("Отмена"))
    notifier.notify(examiner, examiner_response, keyboard=examiner_keyboard)


@router.message(commands=['withdraw_examiner'], roles=['owner'], olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE])
def withdraw_examiner(ctx: Context):
    identity = get_arg(ctx.text, no_arg_error="Необходимо указать хэндл принимающего")
    examiner: Examiner = resolve_member(identity, Examiner, current_olymp.id)
    participant: Participant = Participant.from_id(examiner.queue_entry.participant_id)
    examiner.withdraw_from_queue_entry()
    examiner_response = (f"{participant.full_name} пожаловался(-лась), что тебя не было на приёме задачи. "
                         f"Пожалуйста, используй команду /busy, если тебе надо отойти!\n"
                         f"❗️ Бот установил тебе статус \"занят(-а)\". "
                         f"Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
    notifier.notify(examiner, examiner_response, keyboard=REMOVE_KEYBOARD)
    return_participant_to_queue(participant)
    ctx.reply(f"Принимающий {examiner} снят с приёма задачи")


@router.callback(prefix='examiner_didnt_come', olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE])
def examiner_didnt_come_handler(ctx: Context):
    if not NO_EXAMINER_COMPLAINTS:
        ctx.answer()
        return
    data = ctx.data
    if data == 'examiner_didnt_come_cancel':
        ctx.delete()
        ctx.answer("Действие отменено")
        return
    participant: Participant = Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    if not participant.queue_entry:
        raise UserError("Пожаловаться, что принимающий не пришёл, можно только при сдаче задачи")
    if participant.queue_entry.status != QueueStatus.DISCUSSING:
        raise UserError("Пожаловаться, что принимающий не пришёл, можно только при сдаче задачи")
    if data == 'examiner_didnt_come':
        response = "Ты точно хочешь пожаловаться, что принимающего нет, и вернуться в очередь?"
        buttons = inline_keyboard({
            'Да, принимающего нет': 'examiner_didnt_come_confirmed',
            'Нет, я нажал(-а) случайно': 'examiner_didnt_come_cancel'
        })
        ctx.reply(response, keyboard=buttons)
        ctx.answer()
        return
    ctx.edit_keyboard(None)
    examiner: Examiner = Examiner.from_id(participant.queue_entry.examiner_id)
    examiner.withdraw_from_queue_entry()
    examiner_response = (f"{participant.full_name} отметил(-а), что тебя не было на приёме задачи. "
                         f"Пожалуйста, используй команду /busy, если тебе надо отойти!\n"
                         f"❗️ Бот установил тебе статус \"занят(-а)\". "
                         f"Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
    notifier.notify(examiner, examiner_response, keyboard=REMOVE_KEYBOARD)
    queue_entry = participant.queue_entry
    owner_message = (f"{participant} пожаловался(-лась), что принимающего {examiner} не было на приёме задачи "
                     f"(запись в очереди: <code>{queue_entry.id}</code>)")
    notifier.send_text(OWNER_PLATFORM, OWNER_ID, owner_message)
    ctx.answer("Мы сообщили организаторам о проблеме")
    return_participant_to_queue(participant)


@router.message(
    regexp=rf'(/queue( \d+)?|{JOIN_QUEUE_BUTTON})',
    roles=['participant'], olymp_statuses=[OlympStatus.CONTEST])
def queue(ctx: Context):
    participant: Participant = Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    if participant.finished:
        if extract_command(ctx.text):
            raise UserError("Неизвестная команда", reply_markup=participant_keyboard_olymp_finished)
        return
    temp_reply = ctx.reply("Обработка запроса…", keyboard=REMOVE_KEYBOARD)
    if participant.queue_entry:
        problem = Problem.from_id(participant.queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        error_message = f"Ты уже в очереди на задачу {problem_number}: {problem}"
        if PROMOTE_COMMANDS:
            error_message += ". Чтобы покинуть очередь, используй команду /leave_queue"
        ctx.delete(temp_reply)
        raise UserError(error_message, reply_markup=participant_keyboard_in_queue)
    if match := re.match(r"/queue (\d+)", ctx.text):
        match: re.Match
        problem_number = int(match.group(1))
        ctx.delete(temp_reply)
        join_queue(participant, problem_number)
        return
    ctx.delete(temp_reply)
    ctx.reply(
        "Выбери задачу для сдачи",
        keyboard=participant_keyboard_choose_problem(participant)
    )


@router.callback(prefix='join_queue_', olymp_statuses=[OlympStatus.CONTEST])
def join_queue_handler(ctx: Context):
    ctx.delete()
    participant: Participant = Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    if ctx.data.endswith('_cancel'):
        ctx.reply(
            "Действие отменено",
            keyboard=participant_keyboard_olymp_finished if participant.finished else participant_keyboard
        )
        return
    queue_entry = participant.queue_entry
    if queue_entry:
        problem = Problem.from_id(queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        error_message = f"Ты уже в очереди на задачу {problem_number}: {problem}"
        if PROMOTE_COMMANDS:
            error_message += ". Чтобы покинуть очередь, используй команду /leave_queue"
        raise UserError(error_message, reply_markup=participant_keyboard_in_queue)
    problem_number = int(ctx.data[len('join_queue_'):])
    join_queue(participant, problem_number)


def join_queue(participant: Participant, problem_number: int):
    if participant.finished or current_olymp.status in [OlympStatus.QUEUE, OlympStatus.REGISTRATION]:
        raise UserError(
            "Олимпиада завершена, записываться на сдачу больше нельзя",
            reply_markup=participant_keyboard_olymp_finished
        )
    problem = participant.problem_from_number(problem_number)
    if participant.attempts_left(problem) <= 0:
        raise UserError(f"У тебя не осталось попыток, чтобы сдать задачу {problem_number}: {problem}… "
                        f"Стоит заняться другими задачами",
                        reply_markup=participant_keyboard)
    if participant.solved(problem):
        raise UserError(f"Задача {problem_number}: {problem} уже сдана! Займись другими задачами",
                        reply_markup=participant_keyboard)
    queue_entry = participant.join_queue(problem)
    announce_queue_entry(queue_entry)


@router.message(
    regexp=rf'(/leave_queue|{LEAVE_QUEUE_BUTTON})', 
    roles=['participant'], 
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE]
)
def leave_queue(ctx: Context):
    participant: Participant = Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    queue_entry = participant.queue_entry
    if not queue_entry:
        if participant.finished:
            if extract_command(ctx.text):
                raise UserError("Неизвестная команда", reply_markup=participant_keyboard_olymp_finished)
            return
        error_message = "Ты уже не в очереди"
        if PROMOTE_COMMANDS:
            error_message += (". Чтобы записаться на сдачу задачи, используй команду <code>"
                              + escape_html("/queue <номер задачи>")
                              + "</code>")
        raise UserError(error_message, reply_markup=participant_keyboard)
    if queue_entry.status != QueueStatus.WAITING:
        raise UserError("Нельзя покинуть очередь во время сдачи задач")
    if participant.finished or current_olymp.status == OlympStatus.QUEUE:
        response = "Олимпиада завершена — если ты покинешь очередь, то больше не сможешь сдавать задачи"
    else:
        response = ("Осторожно! Если ты покинешь очередь, то потеряешь своё место в ней. "
                    "При повторной записи на задачу ты окажешься в конце")
    ctx.reply(
        response,
        keyboard=inline_keyboard({
            'Покинуть очередь': 'leave_queue_confirm',
            'Отмена': 'leave_queue_cancel'
        })
    )


@router.callback(prefix='leave_queue_', olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE])
def leave_queue_handler(ctx: Context):
    ctx.delete()
    participant: Participant = Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    if ctx.data.endswith('_cancel'):
        ctx.reply(
            "Действие отменено",
            keyboard=participant_keyboard_olymp_finished if participant.finished else participant_keyboard_in_queue
        )
        return
    queue_entry = participant.queue_entry
    if not queue_entry:
        error_message = "Ты уже не в очереди"
        if PROMOTE_COMMANDS and not participant.finished:
            error_message += (". Чтобы записаться в очередь, используй команду <code>"
                              + escape_html("/queue <номер задачи>")
                              + "</code>")
        raise UserError(error_message, 
                        reply_markup=participant_keyboard_olymp_finished if participant.finished else participant_keyboard)
    if queue_entry.status != QueueStatus.WAITING:
        raise UserError("Нельзя покинуть очередь во время сдачи задач")
    queue_entry.status = QueueStatus.CANCELED
    announce_queue_entry(queue_entry)


@router.message(
    commands=['give_out_second_block', 'give_out_third_block'], roles=['owner'],
    olymp_statuses=[OlympStatus.CONTEST])
def give_out_problem_block(ctx: Context):
    command = extract_command(ctx.text)
    new_problem_block_number = 2 if command == 'give_out_second_block' else 3
    include_tags, exclude_tags = get_tags_args(ctx.text)
    participants = current_olymp.get_participants(finished=False, include_tags=include_tags, exclude_tags=exclude_tags)
    issues_no_prev = 0
    issues_contact = 0
    receivers = 0
    for p in participants:
        last_block_number = p.last_block_number
        if last_block_number < new_problem_block_number - 1:
            issues_no_prev += 1
        if last_block_number < new_problem_block_number:
            receivers += 1
            new_problem_block = p.give_next_problem_block()
            if p.external_id:
                participant_reply = (
                    f"Тебе выдан {'второй' if last_block_number + 1 == 2 else 'завершающий третий'} блок задач. "
                    f"Теперь можешь решать и сдавать их тоже!"
                )
                if PROMOTE_COMMANDS:
                    participant_reply += (f"\nЧтобы записаться на сдачу задачи, используй команду <code>"
                                          + escape_html("/queue <номер задачи>")
                                          + "</code>")
                try:
                    notifier.notify_document(
                        p,
                        Media(
                            new_problem_block.path or ProblemBlock.DEFAULT_PATH,
                            filename=f"Блок_{last_block_number + 1}.pdf"
                        ),
                        caption=participant_reply
                    )
                except Exception as send_error:
                    issues_contact += 1
    owner_reply = (f"{'Второй' if new_problem_block_number == 2 else 'Третий'} блок задач выдан "
                   f"{receivers} {decline(receivers, 'участник', ('у', 'ам', 'ам'))}")
    if issues_no_prev:
        owner_reply += (
            f"\n⚠️ У {issues_no_prev} {decline(issues_no_prev, 'участник', ('а', 'ов', 'ов'))} "
            f"не было предыдущего блока!"
        )
    if issues_contact:
        owner_reply += (
            f"\n⚠️ Не удалось отправить новый блок "
            f"{issues_contact} {decline(issues_contact, 'участник', ('у', 'ам', 'ам'))}!"
        )
    ctx.reply(owner_reply)
    if issues_contact and (issues_contact > receivers / 2):
        raise send_error


@router.message(commands=['give_second_block', 'give_third_block'], roles=['owner'], olymp_statuses=[OlympStatus.CONTEST])
def give_problem_block(ctx: Context):
    command = extract_command(ctx.text)
    new_problem_block_number = 2 if command == 'give_second_block' else 3
    identity = get_arg(ctx.text, "Необходимо указать messenger@handle участника")
    participant: Participant = resolve_member(identity, Participant, current_olymp.id)
    if participant.finished:
        raise UserError(f"Участник {participant.full_name} завершил участие, ему нельзя выдавать новые блоки задач")
    if participant.last_block_number != new_problem_block_number - 1:
        raise UserError(f"Последний блок участника {participant} — блок {participant.last_block_number}")
    problem_block = participant.give_next_problem_block()
    participant_reply = (f"Тебе выдан {'второй' if participant.last_block_number == 2 else 'завершающий третий'} блок задач. "
                         f"Теперь можешь решать и сдавать их тоже!")
    if PROMOTE_COMMANDS:
        participant_reply += ("\nЧтобы записаться на сдачу задачи, используй команду <code>"
                              + escape_html("/queue <номер задачи>")
                              + "</code>")
    notifier.notify_document(
        participant,
        Media(
            problem_block.path or ProblemBlock.DEFAULT_PATH,
            filename=f"Блок_{participant.last_block_number}.pdf"
        ),
        caption=participant_reply
    )
    ctx.reply(
        f"{'Второй' if new_problem_block_number == 2 else 'Третий'} блок задач выдан участнику {participant}"
    )


@router.message(commands=['send_to_participant', 'send_to_examiner'], roles=['owner'])
def send_command(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    message_to_send = ctx.reply_to
    if not message_to_send:
        raise UserError("Эту команду нужно использовать в ответ на сообщение")
    identity = get_arg(ctx.text, "Необходимо указать хэндл пользователя")
    command = extract_command(ctx.text)
    member_class = Participant if command == 'send_to_participant' else Examiner
    member_class_name = "Участник" if command == 'send_to_participant' else "Принимающий"
    member: Participant | Examiner = resolve_member(identity, member_class, current_olymp.id)
    if not member.external_id:
        raise UserError(f"{member} не авторизован(-а). Невозможно переслать сообщение…")
    message_to_send_ref = message_to_send
    ctx.copy_to(member, message_to_send_ref)
    ctx.send_text(
        ctx.peer_id,
        f"{member_class_name} {member} получил оповещение!",
        reply_to=message_to_send_ref
    )


@router.message(commands=['announce_to_participants', 'announce_to_examiners', 'announce_to_everyone'], roles=['owner'])
def announce_command(ctx: Context):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    announcement = ctx.reply_to
    if not announcement:
        raise UserError("Эту команду нужно использовать в ответ на сообщение")
    command = extract_command(ctx.text)
    include_tags, exclude_tags = get_tags_args(ctx.text)
    send_to_participants = (command != 'announce_to_examiners')
    send_to_examiners = (command != 'announce_to_participants')
    owner_response = ""
    p_amount = 0
    e_amount = 0
    err_amount = 0
    if send_to_participants:
        for p in current_olymp.get_participants(include_tags=include_tags, exclude_tags=exclude_tags):
            if p.external_id:
                try:
                    ctx.copy_to(p, announcement)
                    p_amount += 1
                except Exception as send_error:
                    err_amount += 1
        owner_response += f"{p_amount} {decline(p_amount, 'участник', ('', 'а', 'ов'))}"
    if send_to_participants and send_to_examiners:
        owner_response += " и "
    if send_to_examiners:
        for e in current_olymp.get_examiners(include_tags=include_tags, exclude_tags=exclude_tags):
            if e.external_id:
                try:
                    ctx.copy_to(e, announcement)
                    e_amount += 1
                except Exception as send_error:
                    err_amount += 1
        owner_response += f"{e_amount} {decline(e_amount, 'принимающ', ('ий', 'их', 'их'))}"
    owner_response += " получил" + ("и" if p_amount + e_amount > 1 else "") + " оповещение!"
    if err_amount:
        owner_response += (
            f"\n⚠️ Не удалось доставить сообщение {err_amount} {decline(err_amount, 'пользовател', ('ю', 'ям', 'ям'))}"
        )
    ctx.send_text(ctx.peer_id, owner_response, reply_to=announcement)
    if err_amount and (err_amount > (p_amount + e_amount) / 2):
        raise send_error


@router.message(
    commands=['results'],
    roles=['owner', 'examiner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def results_command(ctx: Context):
    COLUMNS = {
        'ID': 7.0,
        'ФИО': 25.0,
        'Класс': 10.0
    }
    for n in range(1, 10): COLUMNS[str(n)] = 10.3
    COLUMNS['Сумма'] = 10.3
    junior_table = pd.DataFrame(columns=COLUMNS.keys())
    senior_table = pd.DataFrame(columns=COLUMNS.keys())
    for participant in current_olymp.get_participants():
        sum, problem_results = participant.results()
        row = [None, f'{participant.surname} {participant.name}', participant.grade]
        for _, successful, number in problem_results:
            row.append(number if successful else 0)
        row.append(sum)
        if participant.is_junior: junior_table = pd.concat([junior_table, pd.DataFrame([row], columns=COLUMNS.keys())],
                                                           ignore_index=True)
        else:                     senior_table = pd.concat([senior_table, pd.DataFrame([row], columns=COLUMNS.keys())],
                                                           ignore_index=True)
    dir = "created_files"
    Path(dir).mkdir(exist_ok=True)
    excel_path = os.path.join("created_files", f"results_{current_olymp.id}.xlsx")
    JUNIOR_SHEET_NAME = "Результаты 8—9"
    SENIOR_SHEET_NAME = "Результаты 10—11"
    with pd.ExcelWriter(excel_path) as writer:
        junior_table.to_excel(writer, JUNIOR_SHEET_NAME, index=False, freeze_panes=(1, 3))
        senior_table.to_excel(writer, SENIOR_SHEET_NAME, index=False, freeze_panes=(1, 3))
        book = writer.book
        for sheetname in book.sheetnames:
            sheet = book[sheetname]
            for i, (column_name, column_size) in enumerate(COLUMNS.items()):
                col_letter = chr(ord('A')+i)
                sheet.column_dimensions[col_letter].width = column_size
                if i >= 2:
                    for cell in sheet[col_letter]:
                        cell.alignment = Alignment(horizontal='center')
                if column_name in ['ID', 'Сумма']:
                    for cell in sheet[col_letter]:
                        cell.font = Font(bold=True)
    ctx.send_document(
        ctx.peer_id,
        Media(excel_path, filename=f"{current_olymp.name.replace(' ', '_')}_результаты.xlsx"),
        caption="Результаты олимпиады"
    )


@router.message(discussing_examiner=True)
def examiner_buttons_callback(ctx: Context):
    result_status = QueueStatus.from_text(ctx.text, no_error = True)
    if not result_status or result_status in QueueStatus.active():
        ctx.reply("Выбери результат сдачи на клавиатуре")
        return
    examiner: Examiner = Examiner.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id)
    queue_entry: QueueEntry = examiner.queue_entry
    queue_entry.status = result_status
    announce_queue_entry(queue_entry)


@router.message(regexp=r"/.+")
def other_commands(ctx: Context):
    raise UserError("Неизвестная команда")

@router.message(roles=['not owner'])
def other_messages(ctx: Context):
    if not current_olymp:
        refer_to_people = False
    elif current_olymp.status == OlympStatus.REGISTRATION:
        refer_to_people = True
    elif (current_olymp.status in [OlympStatus.CONTEST, OlympStatus.QUEUE]
          and (p := Participant.from_external_id(ctx.platform, ctx.sender_id, current_olymp.id, no_error=True))
          and (p.queue_entry or not p.finished)):
        refer_to_people = True
    else:
        refer_to_people = False
    
    error_message = ("Неизвестная команда\n"
                     "Если у тебя есть организационные вопросы об олимпиаде, ")
    if refer_to_people: error_message += f"пиши {' или '.join(ASK_PEOPLE_HANDLES.get(ctx.platform, []))}\n"
    else:               error_message += (f"пиши нам в <a href=\"vk.ru/hseling.for.school\">группе ВКонтакте</a> "
                                          f"или в <a href=\"t.me/hselingforschool\">Телеграм-канале</a>\n")
    error_message += f"Если у тебя есть вопросы по работе бота, пиши {OWNER_HANDLES[ctx.platform]}"

    raise UserError(error_message, contact_note=False)


def main():
    global current_olymp
    create_update_db()
    current_olymp = Olymp.current()

    from messaging.telegram_transport import TelegramTransport

    transports = []
    if TELEGRAM_TOKEN:
        telegram = TelegramTransport(TELEGRAM_TOKEN, handle_event)
        notifier.add(telegram.messenger)
        transports.append(telegram)
    if VK_ENABLED:
        from messaging.vk_transport import VkTransport
        vk = VkTransport(VK_TOKEN, VK_GROUP_ID, handle_event)
        notifier.add(vk.messenger)
        transports.append(vk)

    print("Запускаю бота...")

    owner_startup_message = "Бот запущен!"
    if not current_olymp:
        owner_startup_message += (
            "\nТекущая олимпиада не выбрана. Чтобы установить текущую олимпиаду, используй команду <code>"
            + escape_html("/olymp_select <название>")
            + "</code>")
    try:
        notifier.send_text(OWNER_PLATFORM, OWNER_ID, owner_startup_message)
    except Exception as e:
        print("! Не удалось оповестить владельца. Проверь owner_id в файле config.ini")

    threads = [threading.Thread(target=transport.start, daemon=True) for transport in transports]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
