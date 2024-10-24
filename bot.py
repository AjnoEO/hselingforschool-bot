import os
from pathlib import Path
import re
from typing import Callable
import json
from db import create_update_db, StateDBStorage
from data import TOKEN, OWNER_ID, OWNER_HANDLE
import telebot
from telebot.types import Message, CallbackQuery, InputFile, ReplyKeyboardMarkup, ReplyKeyboardRemove, ReplyParameters
from telebot.formatting import escape_html
from telebot.custom_filters import SimpleCustomFilter, AdvancedCustomFilter, StateFilter
from telebot.states import State, StatesGroup
from telebot.states.sync.context import StateContext
from telebot.states.sync.middleware import StateMiddleware
from telebot.util import quick_markup, extract_command
from olymp import Olymp, OlympStatus
from users import User, OlympMember, Participant, Examiner
from problem import Problem, ProblemBlock, BlockType
from queue_entry import QueueEntry, QueueStatus
from utils import UserError, decline, get_arg, get_n_args, get_file, save_downloaded_file
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from io import BytesIO


PROMOTE_COMMANDS = False # Подсказывать ли команды участникам
NO_EXAMINER_COMPLAINTS = False # Давать ли участникам возможность пожаловаться на то, что принимающий не пришёл
MEMBER_PAGE_SIZE = 10 # Сколько членов олимпиады показывать в одном сообщении списка


create_update_db()


class MyExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exc: Exception):
        message = None
        reply_markup = None
        tb = exc.__traceback__
        while (tb := tb.tb_next):
            # print(tb.tb_frame)
            if 'message' in tb.tb_frame.f_locals:
                message = tb.tb_frame.f_locals['message']
                if isinstance(message, Message):
                    break
        if not message:
            return False
        contact_note = (message.chat.id != OWNER_ID)
        handled = False
        if isinstance(exc, UserError):
            error_message = "⚠️ Ошибка!\n" + str(exc)
            handled = True
            reply_markup = exc.reply_markup
            contact_note = contact_note and exc.contact_note
        else:
            traceback = exc.__traceback__
            while traceback.tb_next: traceback = traceback.tb_next
            filename = os.path.split(traceback.tb_frame.f_code.co_filename)[1]
            line_number = traceback.tb_lineno
            error_message = (f"⚠️ Во время выполнения операции произошла ошибка:\n"
                             f"<code>{exc.__class__.__name__} "
                             f"({filename}, строка {line_number}): {' '.join([escape_html(str(arg)) for arg in exc.args])}</code>")
        if contact_note:
            error_message += f"\nЕсли тебе кажется, что это баг, сообщи {OWNER_HANDLE}"
        bot.send_message(message.chat.id, error_message, reply_markup=reply_markup)
        return handled


bot = telebot.TeleBot(
    TOKEN,
    parse_mode="HTML",
    state_storage=StateDBStorage(),
    use_class_middlewares=True,
    disable_web_page_preview=True,
    exception_handler=MyExceptionHandler()
)

class ExaminerStates(StatesGroup):
    choosing_problems = State()

bot.add_custom_filter(StateFilter(bot))
bot.setup_middleware(StateMiddleware(bot))

class RolesFilter(AdvancedCustomFilter): # owner, examiner, participant
    key = 'roles'
    @staticmethod
    def check(message: Message, roles: list[str]):
        if 'owner' in roles and message.from_user.id == OWNER_ID:
            return True
        if 'not owner' in roles and message.from_user.id != OWNER_ID:
            return True
        if not current_olymp:
            return False
        if 'examiner' in roles and Examiner.from_tg_id(message.from_user.id, current_olymp.id, no_error=True):
            return True
        if 'participant' in roles and Participant.from_tg_id(message.from_user.id, current_olymp.id, no_error=True):
            return True
        return False

class OlympStatusFilter(AdvancedCustomFilter):
    key = 'olymp_statuses'
    @staticmethod
    def check(message: Message, statuses: list[OlympStatus]):
        if not current_olymp:
            return None in statuses
        return current_olymp.status in statuses

class DocCommandsFilter(AdvancedCustomFilter):
    key = 'doc_commands'
    @staticmethod
    def check(message: Message, commands: list[str]):
        command = extract_command(message.text or message.caption)
        return (command in commands)

class DiscussingExaminerFilter(SimpleCustomFilter):
    key = 'discussing_examiner'
    @staticmethod
    def check(message: Message):
        if not current_olymp: return False
        if current_olymp.status not in [OlympStatus.CONTEST, OlympStatus.QUEUE]: return False
        examiner: Examiner | None = Examiner.from_tg_id(message.from_user.id, current_olymp.id, no_error=True)
        if not examiner: return False
        return examiner.queue_entry is not None
        
bot.add_custom_filter(RolesFilter())
bot.add_custom_filter(OlympStatusFilter())
bot.add_custom_filter(DocCommandsFilter())
bot.add_custom_filter(DiscussingExaminerFilter())

current_olymp = Olymp.current()


JOIN_QUEUE_BUTTON = "Сдать задачу"
LEAVE_QUEUE_BUTTON = "Покинуть очередь"
MY_STATS_BUTTON = "Мои задачи"

participant_keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
participant_keyboard.add(JOIN_QUEUE_BUTTON, MY_STATS_BUTTON)

participant_keyboard_in_queue = ReplyKeyboardMarkup(resize_keyboard=True)
participant_keyboard_in_queue.add(LEAVE_QUEUE_BUTTON, MY_STATS_BUTTON)

participant_keyboard_olymp_finished = ReplyKeyboardMarkup(resize_keyboard=True)
participant_keyboard_olymp_finished.add(MY_STATS_BUTTON)

def participant_keyboard_choose_problem(participant: Participant):
    buttons = {f"{i+1}: {problem.name}": {'callback_data': f'join_queue_{i+1}'} 
               for i, problem in enumerate(participant.problems())}
    buttons["Отмена"] = {'callback_data': 'join_queue_cancel'}
    return quick_markup(buttons, row_width=3)


OLYMP_START_HELP = (f"Чтобы сдать задачу, используй кнопку «Сдать задачу» "
                    f"(она может быть скрыта справа от поля ввода сообщения "
                    f"под кнопкой в виде четырёх квадратиков или четырёхлистника)\n"
                    f"Если у тебя возникли вопросы, обращайся к {OWNER_HANDLE}")


@bot.message_handler(
    commands=['start', 'help', 'authenticate'],
    roles=['not owner'],
    olymp_statuses=[None, OlympStatus.TBA]
)
def lost(message: Message):
    response = ("Привет! Я бот для проведения <strong>Устной олимпиады по лингвистике (УОЛ)</strong>\n"
                "Пока что олимпиады нет. О регистрации на олимпиаду и дате её начала "
                "мы объявляем в каналах УОЛ и Лингвокружка:\n"
                "- ВКонтакте: vk.com/hseling.for.school\n"
                "- Телеграм: t.me/hselingforschool\n"
                "- Сайт: ling.hse.ru/junior\n"
                "Подписывайся и ожидай информации там")
    bot.send_message(message.chat.id, response)


@bot.message_handler(
    commands=['start', 'authenticate'], 
    olymp_statuses = [OlympStatus.REGISTRATION, OlympStatus.CONTEST]
)
def send_welcome(message: Message):
    olymp_id = current_olymp.id
    tg_id = message.from_user.id
    member: Participant | Examiner | None = \
        Participant.from_tg_id(tg_id, olymp_id, no_error=True) or Examiner.from_tg_id(tg_id, olymp_id, no_error=True)
    if member:
        send_authentication_confirmation(member, already_authenticated=True)
        return
    
    tg_handle = message.from_user.username or str(message.from_user.id)
    new_member: Participant | Examiner | None = \
        Participant.from_tg_handle(tg_handle, olymp_id, no_error=True) or Examiner.from_tg_handle(tg_handle, olymp_id, no_error=True)
    user_old_handle = User.from_tg_id(tg_id, no_error=True)
    if user_old_handle and new_member:
        if user_old_handle.tg_handle.isnumeric():
            participant_reply = f"Ты участвовал(-а) в прошлой олимпиаде без хэндла?"
        elif new_member.tg_handle.isnumeric():
            participant_reply = f"У тебя раньше в Телеграме был хэндл @{user_old_handle.tg_handle}?"
        else:
            participant_reply = f"У тебя поменялся хэндл в Телеграме с @{user_old_handle.tg_handle} на @{new_member.tg_handle}?"
        bot.send_message(
            message.chat.id, 
            participant_reply,
            reply_markup=quick_markup(
                {'Да': {'callback_data': 'handle_changed_yes'}, 'Нет': {'callback_data': 'handle_changed_no'}},
                row_width=2
            )
        )
        return
    
    if new_member:
        new_member.tg_id = tg_id
        send_authentication_confirmation(new_member)
        return
    
    bot.send_message(message.chat.id, f"Пользователь не найден. Если ты регистрировался(-лась) на олимпиаду, напиши {OWNER_HANDLE}")


@bot.callback_query_handler(lambda callback_query: callback_query.data.startswith('handle_changed_'))
def handle_change_handler(callback_query: CallbackQuery):
    handle_changed = callback_query.data.endswith('_yes')
    message = callback_query.message
    bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=message.id, reply_markup=None)
    if not handle_changed:
        bot.send_message(message.chat.id, f"Что-то пошло не так. Напиши {OWNER_HANDLE}")
        return
    old_user = User.from_tg_id(callback_query.from_user.id)
    new_user = User.from_tg_handle(callback_query.from_user.username)
    old_user.conflate_with(new_user)
    user_id = old_user.user_id
    olymp_id = current_olymp.id
    member: Participant | Examiner | None = \
        Participant.from_user_id(user_id, olymp_id, no_error=True) or Examiner.from_user_id(user_id, olymp_id, no_error=True)
    if member:
        send_authentication_confirmation(member)
        bot.answer_callback_query(callback_query.id)
        return


def send_authentication_confirmation(member: OlympMember, *, already_authenticated: bool = False):
    response = (("Ты уже авторизован(-а) как " if already_authenticated else "Ты успешно авторизовался(-лась) как ")
                + ("участник" if isinstance(member, Participant) else "принимающий")
                + "!\n" + member.display_data())
    participant_reply_markup = None
    if isinstance(member, Examiner):
        if not current_olymp.status == OlympStatus.CONTEST:
            response += "\nЧтобы выбрать задачи для приёма, используй команду /choose_problems"
        elif member.is_busy:
            response += "\n❗️ Чтобы начать принимать задачи, используй команду /free"
        response += ("\nЧтобы просмотреть информацию о себе, используй команду /my_info"
                     "\nПолный список команд: /help")
    elif current_olymp.status == OlympStatus.REGISTRATION:
        response += ("\n\nДата и время начала олимпиады есть в <a href=\"vk.com/hseling.for.school\">нашей группе ВКонтакте</a> "
                     "и в <a href=\"t.me/hselingforschool\">нашем Телеграм-канале</a>\n"
                     "Когда олимпиада начнётся, бот пришлёт тебе задания и ты сможешь записываться на сдачу задач через него")
    else:
        response += ("\n❗️ Олимпиада уже началась!\n" + OLYMP_START_HELP)
        participant_reply_markup = participant_keyboard
    bot.send_message(member.tg_id, response, reply_markup=participant_reply_markup)
    if current_olymp.status == OlympStatus.CONTEST and isinstance(member, Participant):
        send_all_problem_blocks(member)


def send_all_problem_blocks(participant: Participant):
    for problem_block_number in range(1, participant.last_block_number+1):
        problem_block = participant.problem_block_from_number(problem_block_number)
        bot.send_document(
            participant.tg_id,
            InputFile(problem_block.path, f"Блок_{problem_block_number}.pdf")
        )


@bot.message_handler(commands=['help'], roles=['owner', 'examiner', 'participant'])
def help(message: Message):
    commands = [[["help", "Показать список команд"]]]
    roles = []
    if message.from_user.id == OWNER_ID:
        roles.append('owner')
    if current_olymp and Examiner.from_tg_id(message.from_user.id, current_olymp.id, no_error=True):
        roles.append('examiner')
    if current_olymp and Participant.from_tg_id(message.from_user.id, current_olymp.id, no_error=True):
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
            bot.send_message(message.chat.id, response)
            response = f"<strong>{block}</strong>\n\n"
            continue
        for command_description in block:
            command, description = tuple(command_description)
            command = ("/" + command) if (" " not in command) else f"<code>/{escape_html(command)}</code>"
            response += command + " — " + description + "\n"
        response += "\n"
    bot.send_message(message.chat.id, response)


@bot.message_handler(
    regexp=rf"/my_stats|{MY_STATS_BUTTON}",
    roles=['participant'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def participant_stats(message: Message):
    participant: Participant = Participant.from_tg_id(message.from_user.id, current_olymp.id)
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
    bot.send_message(
        message.chat.id, response,
        reply_markup=ReplyKeyboardRemove() if current_olymp.status == OlympStatus.RESULTS else None
    )


@bot.message_handler(
    commands=["my_info"],
    roles=['participant'],
    olymp_statuses=[OlympStatus.REGISTRATION, OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def participant_info(message: Message):
    participant: Participant = Participant.from_tg_id(message.from_user.id, current_olymp.id)
    response = (f"Информация о тебе:\n"
                f"{participant.display_data(contact_note=False)}")
    if current_olymp.status != OlympStatus.REGISTRATION:
        response += "\nПросмотреть информацию о сданных задачах можно при помощи команды /my_stats"
    response += f"\nЕсли в данных есть ошибка, сообщи {OWNER_HANDLE}"
    bot.send_message(message.chat.id, response)


@bot.message_handler(
    commands=["my_info"],
    roles=['examiner'],
    olymp_statuses=[OlympStatus.REGISTRATION, OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def examiner_info(message: Message):
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    response = (f"Информация о тебе:\n"
                f"{examiner.display_data(verbose=True, olymp_status=current_olymp.status)}")
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['olymp_registration_start', 'olymp_reg_start'], roles=['owner'])
def olymp_reg_start(message: Message):
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
        if p.tg_id:
            p_message = (f"Мы запустили авторизацию в боте для онлайн-участников. Тебя авторизовали автоматически!\n"
                         f"{p.display_data()}")
            bot.send_message(p.tg_id, p_message)
    for e in examiners:
        if e.tg_id:
            e_message = (f"Мы запустили авторизацию в боте. Тебя автоматически авторизовали как принимающего!\n"
                         f"{e.display_data()}")
            bot.send_message(e.tg_id, e_message)
    bot.send_message(message.chat.id, f"Регистрация на олимпиаду <em>{current_olymp.name}</em> запущена")


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
    participant_message = ("Олимпиада началась! Можешь приступать к решению задач\n"
                           + OLYMP_START_HELP)
    for p in participants:
        if p.tg_id:
            problem_block = p.last_block
            bot.send_document(
                p.tg_id, 
                document=InputFile(problem_block.path, "Блок_1.pdf"),
                caption=participant_message,
                reply_markup=participant_keyboard
            )
    examiners = current_olymp.get_examiners()
    for e in examiners:
        if e.tg_id:
            bot.send_message(e.tg_id, "Олимпиада началась! Напиши /free и ожидай участников\nСписок команд: /help")
    bot.send_message(OWNER_ID, f"Олимпиада <em>{current_olymp.name}</em> начата")


@bot.message_handler(commands=['olymp_start'], roles=['owner'])
def olymp_start(message: Message):
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
    bot.send_message(message.chat.id, response, reply_markup=quick_markup({
        'Всё равно начать': {'callback_data': 'start_olymp_confirm'},
        'Отмена': {'callback_data': 'start_olymp_cancel'}
    }))


@bot.callback_query_handler(lambda callback_query: callback_query.data.startswith('start_olymp_'))
def olymp_start_confirmation_handler(callback_query: CallbackQuery):
    confirmed = callback_query.data.endswith('confirm')
    message = callback_query.message
    bot.delete_message(message.chat.id, message.id)
    if not confirmed:
        bot.send_message(message.chat.id, "Действие отменено")
        return
    start_olymp()


def finish_olymp():
    if not current_olymp or current_olymp.status not in [OlympStatus.CONTEST, OlympStatus.QUEUE]:
        raise UserError("Олимпиады нет или она ещё не начата/уже завершена")
    current_olymp.status = OlympStatus.RESULTS
    e_message = "Олимпиада завершилась! Очередь пуста, так что можешь идти отдыхать"
    for e in current_olymp.get_examiners():
        if e.tg_id:
            bot.send_message(e.tg_id, e_message)
    bot.send_message(OWNER_ID, "Олимпиада завершена. Очередь полностью обработана")


@bot.message_handler(commands=['olymp_finish'], roles=['owner'])
def olymp_finish(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.RESULTS:
        raise UserError("Олимпиада уже завершена")
    if current_olymp.status == OlympStatus.QUEUE:
        raise UserError("Олимпиада уже завершается, происходит работа с очередью")
    if current_olymp.status != OlympStatus.CONTEST:
        raise UserError("Олимпиада ещё не начата")
    
    participants = current_olymp.get_participants()
    examiners = current_olymp.get_examiners()
    p_not_in_queue_message = ("Олимпиада завершилась! Больше записываться в очередь нельзя. "
                              "Можешь отправляться на заслуженный отдых")
    p_in_queue_message = ("Олимпиада завершилась! Больше записываться в очередь нельзя, "
                          "но тех, кто уже записался, мы проверим, так что не уходи")
    if current_olymp.unhandled_queue_left():
        current_olymp.status = OlympStatus.QUEUE
        e_message = ("Олимпиада завершилась! Но мы ещё работаем с очередью, так что "
                     "не уходи раньше времени. Если ты завершил проверку и к тебе никто "
                     "не идёт — тогда можешь идти отдыхать")
        for p in participants:
            if p.tg_id:
                bot.send_message(p.tg_id, p_in_queue_message if p.queue_entry else p_not_in_queue_message)
        for e in examiners:
            if e.tg_id:
                bot.send_message(e.tg_id, e_message)
        bot.send_message(message.chat.id, "Олимпиада завершена. Идёт работа с очередью")
    else:
        for p in participants:
            if p.tg_id:
                bot.send_message(p.tg_id, p_not_in_queue_message, reply_markup=participant_keyboard_olymp_finished)
        finish_olymp()


@bot.message_handler(commands=['olymp_list'], roles=['owner'])
def olymp_list(message: Message):
    olymps = Olymp.list_all()
    amount = len(olymps)
    response = f"Имеется {amount} {decline(amount, 'олимпиад', ('а', 'ы', ''))}" + (":" if amount > 0 else "")
    for olymp in olymps:
        response += f"\n- <em>{olymp.name}</em> (<code>{olymp.status.name}</code>)"
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['olymp_select'], roles=['owner'])
def olymp_select(message: Message):
    global current_olymp
    if current_olymp and current_olymp.status != OlympStatus.RESULTS:
        raise UserError("Заверши текущую олимпиаду, чтобы выбрать другую")
    olymp_name = get_arg(message, "Необходимо указать название олимпиады")
    if current_olymp and current_olymp.name == olymp_name:
        raise UserError(f"Олимпиада <em>{olymp_name}</em> уже выбрана как текущая")
    current_olymp = Olymp.from_name(olymp_name)
    bot.send_message(message.chat.id, f"Олимпиада <em>{current_olymp.name}</em> выбрана как текущая")


@bot.message_handler(commands=['olymp_create'], roles=['owner'])
def olymp_create(message: Message):
    global current_olymp
    if current_olymp and current_olymp.status != OlympStatus.RESULTS:
        bot.send_message(
            message.chat.id,
            f"Уже имеется незавершённая олимпиада <em>{current_olymp.name}</em>. Заверши её, чтобы создать новую"
        )
        return
    name = get_arg(message, "Для олимпиады необходимо название")
    current_olymp = Olymp.create(name)
    bot.send_message(message.chat.id, f"Олимпиада <em>{current_olymp.name}</em> успешно создана")


def upload_members(message: Message, required_key: str, key_description: str, member_class: type[OlympMember],
                   term_stem: str, term_endings: tuple[str, str, str], term_endings_gen: tuple[str, str, str]):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status not in [OlympStatus.TBA, OlympStatus.REGISTRATION]:
        raise UserError("Олимпиада уже начата")
    required_columns = ["name", "surname", "tg_handle", required_key]
    file_data = get_file(message, bot, "Необходимо указать Excel-таблицу", ".xlsx")
    member_table = pd.read_excel(BytesIO(file_data))
    if not set(required_columns).issubset(set(member_table.columns)):
        raise UserError("Таблица должна содержать столбцы " + ', '.join([f'<code>{col}</code>' for col in required_columns]))
    old_members_amount = 0
    updated_users: list[tuple[User | OlympMember, OlympMember]] = []
    for _, m in member_table.iterrows():
        m["tg_handle"] = str(m["tg_handle"])
        old_user = None
        if user := User.from_tg_handle(m["tg_handle"], no_error=True):
            if user.name != m["name"] or user.surname != m["surname"]:
                old_user = user
        if member := member_class.from_tg_handle(m["tg_handle"], current_olymp.id, no_error=True):
            value = member._additional_values[required_key]
            if value != m[required_key]:
                old_user = member
            old_members_amount += 1
        member: OlympMember = member_class.create_as_new_user(**m, olymp_id=current_olymp.id, ok_if_user_exists=True, ok_if_exists=True)
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
                response += (f"\n- {old_user.name} {old_user.surname}, "
                             f"{key_description.format(old_user._additional_values[required_key])} "
                             f"→ {member.name} {member.surname}, "
                             f"{key_description.format(member._additional_values[required_key])} "
                             f"({member.display_tg_handle()})")
            else:
                response += f"\n- {old_user.name} {old_user.surname} → {member.name} {member.surname} ({member.display_tg_handle()})"
    response += (f"\nЧтобы просмотреть список {term_stem}{term_endings_gen[2]}, "
                 f"используй команду /list_{member_class.__name__.lower()}s")
    bot.send_message(message.chat.id, response)


@bot.message_handler(doc_commands=['upload_participants', 'upload_examiners'], roles=['owner'], content_types=['text', 'document'])
def upload_members_command(message: Message):
    command = extract_command(message.text or message.caption)
    if command == 'upload_participants':
        upload_members(
            message, "grade", "{0} класс",
            Participant, 'участник', ('', 'а', 'ов'), ('а', 'ов', 'ов')
        )
    elif command == 'upload_examiners':
        upload_members(
            message, "conference_link", "Ссылка на конференцию: {0}",
            Examiner, 'принимающ', ('ий', 'их', 'их'), ('его', 'их', 'их')
        )


def add_member(message: Message, min_args: int, max_args: int, no_arg_error: str,
               additional_values_func: Callable[[list[str]], dict[str]],
               member_class: type[OlympMember], term_pl_gen: str):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    tg_handle, name, surname, *other_args = get_n_args(
        message, min_args, max_args, no_arg_error
    )
    other_args = additional_values_func(other_args)
    member: OlympMember = member_class.create_as_new_user(
        tg_handle, name, surname, olymp_id=current_olymp.id,
        **other_args, ok_if_user_exists=True
    )
    bot.send_message(message.chat.id, f"{member.name} {member.surname} добавлен(-а) в список {term_pl_gen}")


@bot.message_handler(commands=['add_participant', 'add_examiner'], roles=['owner'])
def add_participant(message: Message):
    command = extract_command(message.text)
    if command == 'add_participant':
        add_member(
            message, 4, 5, "Необходимо указать Телеграм-хэндл, имя, фамилию, класс и, при необходимости, последний блок участника",
            lambda args: {'grade': int(args[0]), 'last_block_number': int(args[1]) if len(args) > 1 else None},
            Participant, "участников"
        )
    else:
        add_member(
            message, 5, 5, "Необходимо указать Телеграм-хэндл, имя, фамилию, ссылку на конференцию и задачи принимающего",
            lambda args: {'conference_link': args[0], 'problems': list(map(int, args[1].split())) if args[1][0] != '0' else None},
            Examiner, "принимающих"
        )


def edit_member(
    message: Message, member_class: type[OlympMember],
    other_args: dict[str, tuple[str, Callable[[OlympMember], str], Callable[[OlympMember, str], None]]]
):
    """
    Изменить данные члена олимпиады

    :param message: Сообщение (команда)
    :type message: `Message`

    :param member_class: Класс типа члена олимпиады
    :type member_class: Подкласс `OlympMember`

    :param other_args: Возможные аргументы кроме `tg_handle`, `name` и `surname`.
    Словарь, где ключ — ключ аргумента, а значение — кортеж русского названия и двух функций, `getter(member)` и `setter(member, value)`
    :type other_args: {`str`: (`str`, `getter(member) -> str`, `setter(member, value)`)}
    """
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    possible_keys = ['tg_handle', 'name', 'surname'] + list(other_args.keys())
    def tg_setter(member: OlympMember, value: str): member.tg_handle = value
    other_args['tg_handle'] = (
        'Телеграм-хэндл', 
        lambda member: member.display_tg_handle(hide_id=True),
        tg_setter
    )
    def name_setter(member: OlympMember, value: str): member.name = value
    other_args['name'] = ('Имя', lambda member: member.name, name_setter)
    def surname_setter(member: OlympMember, value: str): member.surname = value
    other_args['surname'] = ('Фамилия', lambda member: member.surname, surname_setter)
    command = extract_command(message.text)
    syntax_hint = ("Синтаксис команды: <code>"
                   + escape_html("/" + command + " <tg-хэндл> <" + "|".join(possible_keys) + "> <значение>")
                   + "</code>")
    tg_handle, key, value = get_n_args(message, 3, 3, syntax_hint)
    if key not in possible_keys:
        raise UserError(syntax_hint)
    if key == 'grade': value = int(value)
    member: OlympMember = member_class.from_tg_handle(tg_handle, current_olymp.id)
    old_value = other_args[key][1](member)
    other_args[key][2](member, value)
    change = f"{other_args[key][0]}: {old_value} → {value}"
    bot.send_message(message.chat.id, f"Данные участника {member.name} {member.surname} обновлены:\n" + change)
    if member.tg_id:
        bot.send_message(
            member.tg_id,
            f"Твои данные обновлены:\n" + change + "\nПросмотреть информацию о себе можно при помощи команды /my_info"
        )


@bot.message_handler(commands=['edit_participant', 'edit_examiner'], roles=['owner'])
def edit_member_command(message: Message):
    command = extract_command(message.text)
    if command == 'edit_participant':
        def grade_setter(participant: Participant, value: str): participant.grade = int(value)
        edit_member(
            message, "edit_participant", Participant,
            {'grade': ('Класс участия', lambda participant: participant.grade, grade_setter)}
        )
    else:
        def conference_link_setter(examiner: Examiner, value: str): examiner.conference_link = value
        edit_member(
            message, "edit_examiner", Examiner,
            {'conference_link': ('Ссылка на конференцию', lambda examiner: examiner.conference_link, conference_link_setter)}
        )


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
        bot.send_message(participant.tg_id, participant_response, reply_markup=participant_keyboard_in_queue)


@bot.message_handler(commands=['set_examiner_problems'], roles=['owner'])
def set_examiner_problems(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    tg_handle, problems = get_n_args(message, 2, 2, "Необходимо указать Телеграм-хэндл принимающего и задачи")
    problems = list(map(int, problems.split())) if problems[0] != '0' else None
    if problems:
        current_problems = set(map(lambda problem: problem.id, current_olymp.get_problems()))
        if not set(problems).issubset(current_problems):
            raise UserError("Можно назначать только задачи текущей олимпиады")
    examiner: Examiner = Examiner.from_tg_handle(tg_handle, current_olymp.id)
    examiner.set_problems(problems)
    examiner_response = "Твой список задач обновлён:\n"
    for problem_id in problems:
        examiner_response += f"- {Problem.from_id(problem_id)}\n"
    if examiner.tg_id:
        bot.send_message(examiner.tg_id, examiner_response)
    bot.send_message(message.chat.id, f"Список задач принимающего {examiner.name} {examiner.surname} обновлён")
    queue_entry = examiner.queue_entry
    if queue_entry and queue_entry.problem_id not in examiner.problems:
        participant: Participant = Participant.from_id(queue_entry.participant_id)
        problem: Problem = Problem.from_id(queue_entry.problem_id)
        examiner.withdraw_from_queue_entry()
        bot.send_message(
            examiner.tg_id,
            f"Ты больше не можешь принимать задачу {problem}. "
            f"Бот снял тебя с приёма задачи у участника {participant.name} {participant.surname}\n"
            f"❗️ Бот установил тебе статус \"занят(-а)\". Пожалуйста, используй команду /free, чтобы продолжить принимать задачи!",
            reply_markup=ReplyKeyboardRemove()
        )
        return_participant_to_queue(participant)
    elif not examiner.is_busy:
        queue_entry = examiner.look_for_queue_entry()
        if queue_entry:
            examiner.assign_to_queue_entry(queue_entry)
            announce_queue_entry(queue_entry)


def view_member(
    message: Message, member_class: type[OlympMember], member_name: str, member_name_gen: str
):
    """
    Просмотреть данные члена олимпиады

    :param message: Сообщение (команда)
    :type message: `Message`

    :param member_class: Класс типа члена олимпиады
    :type member_class: Подкласс `OlympMember`

    :param member_name: Русское название члена олимпиады
    :type member_name: `str`

    :param member_name: Русское название члена олимпиады в родительном падеже
    :type member_name: `str`
    """
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    tg_handle = get_arg(message, "Необходимо указать Телеграм-хэндл " + member_name_gen)
    member: OlympMember = member_class.from_tg_handle(tg_handle, current_olymp.id)
    response = (f"<strong>{member_name.capitalize()} <code>{member.id}</code>:</strong>\n"
                + member.display_data(verbose=True, olymp_status=current_olymp.status, technical_info=True, contact_note=False))
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['view_participant', 'view_examiner'], roles=['owner'])
def view_member_command(message: Message):
    command = extract_command(message.text)
    if command == 'view_participant':
        view_member(message, Participant, 'участник', 'участника')
    else:
        view_member(message, Examiner, 'принимающий', 'принимающего')


def list_members_page(message: Message, member_class: type[OlympMember], page: int, member_amount: int, title: str,
                      get_func: Callable[[int, int], list[OlympMember]]):
    """
    Отредактировать сообщение `message`, чтобы отобразить страницу `page` списка членов олимпиады

    :param message: Сообщение (от бота)
    :type message: `Message`

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
    callback_invalid = {'callback_data': f'page_list_{member_class_name}_invalid'}
    if member_amount == 0:
        buttons = {'← X': callback_invalid, 'X →': callback_invalid}
        bot.edit_message_text(title, message.chat.id, message.id, reply_markup=quick_markup(buttons))
        return
    max_page = (member_amount-1) // MEMBER_PAGE_SIZE + 1
    if page > max_page:
        page = max_page
    text = title + f"\nСтраница {page}/{max_page}"
    buttons = {}
    if page == 1:
        buttons['← X'] = callback_invalid
    else:
        buttons[f'← {page-1}'] = {'callback_data': f'page_list_{member_class_name}_{page-1}'}
    if page == max_page:
        buttons['X →'] = callback_invalid
    else:
        buttons[f'{page+1} →'] = {'callback_data': f'page_list_{member_class_name}_{page+1}'}
    member_list = get_func((page-1)*MEMBER_PAGE_SIZE, MEMBER_PAGE_SIZE)
    for member in member_list:
        text += (f"\n- {member.name} {member.surname} "
                 f"({member.display_tg_handle()})")
    text += f"\nЧтобы просмотреть подробную информацию о ком-то одном, используй команду <code>/view_{member_class_name}</code>"
    bot.edit_message_text(text, message.chat.id, message.id, reply_markup=quick_markup(buttons))


def list_participants_page(message: Message, page: int):
    amount = current_olymp.participants_amount()
    list_members_page(
        message, Participant, page, amount,
        f"В олимпиаде {decline(amount, 'участву', ('ет', 'ют', 'ет'))} {amount} {decline(amount, 'человек', ('', 'а', ''))}",
        lambda start, limit: current_olymp.get_participants(start, limit, sort=True)
    )


def list_examiners_page(message: Message, page: int):
    amount = current_olymp.examiners_amount()
    list_members_page(
        message, Examiner, page, amount, 
        f"В олимпиаде {amount} {decline(amount, 'принимающ', ('ий', 'их', 'их'))}",
        lambda start, limit: current_olymp.get_examiners(start, limit, sort=True)
    )


@bot.message_handler(commands=['list_participants', 'list_examiners'], roles=['owner'])
def list_members_command(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    reply = bot.send_message(message.chat.id, "Обработка запроса...", reply_markup=quick_markup({}))
    command = extract_command(message.text)
    if command == 'list_participants':
        list_participants_page(reply, 1)
    else:
        list_examiners_page(reply, 1)


@bot.callback_query_handler(lambda callback_query: callback_query.data.startswith('page_list_'))
def list_members_page_handler(callback_query: CallbackQuery):
    message = callback_query.message
    member_type, page = callback_query.data[len('page_list_'):].split('_')
    if page == 'invalid':
        bot.answer_callback_query(callback_query.id, "Страницы нет")
        return
    page = int(page)
    if member_type == 'participant':
        list_participants_page(message, page)
    else:
        list_examiners_page(message, page)


@bot.message_handler(commands=['olymp_info'], roles=['owner'])
def olymp_info(message: Message):
    if not current_olymp:
        response = f"Нет текущей олимпиады"
    else:
        p_amount = current_olymp.participants_amount()
        e_amount = current_olymp.examiners_amount()
        pr_amount = current_olymp.problems_amount()
        response = (f"Олимпиада <em>{current_olymp.name}</em>:\n"
                    f"<strong>ID:</strong> <code>{current_olymp.id}</code>\n"
                    f"<strong>Статус:</strong> <code>{current_olymp.status.name}</code>\n"
                    f"{p_amount} {decline(p_amount, 'участник', ('', 'а', 'ов'))}\n"
                    f"{e_amount} {decline(e_amount, 'принимающ', ('ий', 'их', 'их'))}\n"
                    f"{pr_amount} {decline(pr_amount, 'задач', ('а', 'и', ''))}")
    bot.send_message(message.chat.id, response)


@bot.message_handler(
    commands=['last_queue_entries'], 
    roles=['owner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def last_queue_entries(message: Message):
    syntax_hint = ("Синтаксис команды: <code>"
                   + escape_html("/last_queue_entries [participant=<tg-хэндл>] [examiner=<tg-хэндл>] [problem=<ID>] [limit=<ограничение>]")
                   + "</code>")
    args = get_n_args(message, 0, 4, syntax_hint)
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
            case 'participant': value = Participant.from_tg_handle(value, current_olymp.id)
            case 'examiner': value = Examiner.from_tg_handle(value, current_olymp.id)
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
        examiner: Examiner | None = chosen_examiner or (Examiner.from_id(queue_entry.examiner_id) if queue_entry.examiner_id else None)
        problem: Problem = chosen_problem or Problem.from_id(queue_entry.problem_id)
        response += (f"ЗАПИСЬ <code>{queue_entry.id}</code>\n"
                     f"- <strong>Участник:</strong> {participant.name} {participant.surname} ({participant.grade} класс)\n")
        if examiner: 
            response += f"- <strong>Принимающий:</strong> {examiner.name} {examiner.surname}\n"
        else: response += "- <strong>Принимающий</strong> не назначен\n"
        response += (f"- <strong>Задача:</strong> <code>{problem.id}</code> {problem}\n"
                     f"- <strong>Статус:</strong> {queue_entry.status}\n")
    bot.send_message(message.chat.id, response)
    
        

@bot.message_handler(
    commands=['update_queue_entry_status'],
    roles=['owner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def update_queue_entry_status(message: Message):
    id, status_text = get_n_args(message, 2, 2, "Необходимо указать ID записи и новый статус")
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
    if message.reply_to_message:
        message_to_send = message.reply_to_message
        participant: Participant = Participant.from_id(queue_entry.participant_id)
        bot.copy_message(participant.tg_id, message_to_send.chat.id, message_to_send.id)
    announce_queue_entry(queue_entry)


@bot.message_handler(
    commands=['update_queue_entry_problem'],
    roles=['owner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE]
)
def update_queue_entry_problem(message: Message):
    id, problem_id = get_n_args(message, 2, 2, "Необходимо указать ID записи и ID новой задачи")
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
            f"У участника {participant.name} {participant.surname} нет задачи <code>{problem_id}</code> {problem}"
        )
    if participant.attempts_left(problem) <= 0 or participant.solved(problem):
        raise UserError(
            f"Участник {participant.name} {participant.surname} больше не может "
            f"сдавать задачу <code>{problem_id}</code> {problem}"
        )
    queue_entry.problem_id = problem_id
    if message.reply_to_message:
        message_to_send = message.reply_to_message
        participant: Participant = Participant.from_id(queue_entry.participant_id)
        bot.copy_message(participant.tg_id, message_to_send.chat.id, message_to_send.id)
    if queue_entry.status == QueueStatus.DISCUSSING:
        examiner: Examiner = Examiner.from_id(queue_entry.examiner_id)
        if problem_id in examiner.problems:
            bot.send_message(
                examiner.tg_id,
                f"Участнику {participant.name} {participant.surname} сменили задачу на {problem}. Обсуждайте её!"
            )
            problem_number = participant.get_problem_number(problem)
            bot.send_message(
                participant.tg_id,
                f"Задачу {problem_number}: {problem} у тебя примет тот же принимающий, "
                f"{examiner.name} {examiner.surname}, по ссылке {examiner.conference_link}"
            )
            return
        queue_entry.status = QueueStatus.WAITING
        queue_entry.examiner_id = None
        examiner.is_busy = True
        bot.send_message(
            examiner.tg_id,
            f"Участнику {participant.name} {participant.surname} сменили задачу на задачу, которую ты не принимаешь\n"
            f"❗️ Бот установил тебе статус \"занят(-а)\". Пожалуйста, используй команду /free, чтобы продолжить принимать задачи!",
            reply_markup=ReplyKeyboardRemove()
        )
    new_examiner_id = queue_entry.look_for_examiner()
    if new_examiner_id:
        new_examiner: Examiner = Examiner.from_id(new_examiner_id)
        new_examiner.assign_to_queue_entry(queue_entry)
    announce_queue_entry(queue_entry)


@bot.message_handler(commands=['problem_create'], roles=['owner'])
def problem_create(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if '\n' not in message.text:
        name = get_arg(message, "Для задачи необходимо название")
        problem = Problem.create(current_olymp.id, name)
        bot.send_message(message.chat.id, f"Задача {problem} добавлена! ID: <code>{problem.id}</code>")
        return
    names = message.text.split(sep='\n')[1:]
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
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['problem_rename'], roles=['owner'])
def problem_rename(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    id, name = get_n_args(message, 2, 2, "Необходимо указать ID задачи и новое название")
    problem = Problem.from_id(int(id))
    if problem.olymp_id != current_olymp.id:
        raise UserError("Задача не относится к текущей олимпиаде")
    problem.name = name
    bot.send_message(message.chat.id, f"Задача <code>{problem.id}</code> переименована: {problem}")


@bot.message_handler(commands=['problem_list'], roles=['owner', 'examiner'])
def problem_list(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problems = current_olymp.get_problems(sort=True)
    if len(problems) == 0:
        bot.send_message(message.chat.id, f"В олимпиаде <em>{current_olymp.name}</em> ещё нет задач")
        return
    response = f"Задачи олимпиады <em>{current_olymp.name}</em>:"
    for p in problems:
        response += f"\n- <code>{p.id}</code> {p}"
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['problem_info'], roles=['owner', 'examiner'])
def problem_info(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problem = Problem.from_id(int(get_arg(message, "Необходимо указать ID задачи")))
    response = ""
    if problem.olymp_id != current_olymp.id:
        if message.from_user.id != OWNER_ID:
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
    bot.send_message(message.chat.id, response)


@bot.message_handler(doc_commands=['problem_block_create'], roles=['owner'], content_types=['text', 'document'])
def problem_block_create(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    file = get_file(message, bot, "Необходим файл с условиями задач", ".pdf")
    args = get_n_args(message, 3, 4, "Необходимо указать задачи для блока")
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
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['problem_block_list'], roles=['owner', 'examiner'])
def problem_block_list(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problem_blocks = current_olymp.get_problem_blocks()
    if len(problem_blocks) == 0:
        bot.send_message(message.chat.id, f"В олимпиаде <em>{current_olymp.name}</em> ещё нет задачных блоков")
        return
    response = f"Задачные блоки олимпиады <em>{current_olymp.name}</em>:"
    for pb in problem_blocks:
        response += f"\n- <code>{pb.id}</code>"
        if pb.block_type:
            response += f" <em>{pb.block_type}</em>"
        if not pb.path:
            response += " (Нет файла!)"
    bot.send_message(message.chat.id, response)


def get_problem_block_from_arg(message_or_arg: Message | str):
    if isinstance(message_or_arg, Message):
        arg = get_arg(message_or_arg, "Необходимо указать ID или тип блока")
    else:
        arg = message_or_arg
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


@bot.message_handler(commands=['problem_block_info'], roles=['owner', 'examiner'])
def problem_block_info(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problem_block = get_problem_block_from_arg(message)
    response = ""
    if problem_block.olymp_id != current_olymp.id:
        if message.from_user.id != OWNER_ID:
            raise UserError("Блок задач не найден")
        response += "❗️ Блок задач не относится к текущей олимпиаде\n"
    response += (f"Информация о блоке задач {problem_block}:\n"
                 f"ID: <code>{problem_block.id}</code>\n"
                 f"Задачи:")
    for problem in problem_block.problems:
        response += f"\n- <code>{problem.id}</code> {problem}"
    if problem_block.path:
        bot.send_document(
            message.chat.id,
            InputFile(problem_block.path, f"Блок_{problem_block.id}.pdf"),
            caption=response
        )
    else:
        response += "\nФайл не назначен"
        bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['problem_block_update_type'], roles=['owner'])
def problem_block_update_type(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status not in [OlympStatus.TBA, OlympStatus.REGISTRATION]:
        raise UserError("Менять тип блока задач можно только до начала олимпиады")
    problem_block_arg, new_type_arg = get_n_args(message, 2, 2, "Необходимо указать ID или тип блока и новый тип или 0")
    problem_block = get_problem_block_from_arg(problem_block_arg)
    if not re.match(r"^((JUNIOR|SENIOR)_[123]|0)$", new_type_arg):
        raise UserError("Тип блока должен быть указан в форме <code>(JUNIOR|SENIOR)_(1|2|3)</code> "
                        "(или <code>0</code>, чтобы убрать тип блока)")
    new_type = BlockType[new_type_arg] if new_type_arg != '0' else None
    problem_block.block_type = new_type
    bot.send_message(
        message.chat.id,
        f"Тип блока <code>{problem_block.id}</code> " + (f"изменён на {new_type}" if new_type else "обнулён")
    )


@bot.message_handler(commands=['problem_block_update_file'], roles=['owner'])
def problem_block_update_file(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    file = get_file(message, bot, "Необходим файл с условиями задач", ".pdf")
    problem_block = get_problem_block_from_arg(message)
    path = save_downloaded_file(file)
    problem_block.delete_file(no_error=True)
    problem_block.path = path
    bot.send_message(message.chat.id, f"Файл блока {problem_block} обновлён")


@bot.message_handler(commands=['problem_block_delete_file'], roles=['owner'])
def problem_block_delete_file(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.CONTEST:
        raise UserError("Нельзя удалять файлы блоков во время олимпиады")
    arg = get_arg(message, "Необходимо указать ID или тип блока, или <code>all</code>, чтобы удалить все файлы")
    if arg != "all":
        problem_block = get_problem_block_from_arg(arg)
        problem_block.delete_file()
        bot.send_message(message.chat.id, f"Файл блока {problem_block} удалён")
        return
    problem_blocks = current_olymp.get_problem_blocks()
    deleted_files = 0
    for pb in problem_blocks:
        if pb.path:
            deleted_files += 1
            pb.delete_file()
    pl = (deleted_files > 1)
    bot.send_message(
        message.chat.id,
        f"Файл{'ы' if pl else ''} {deleted_files} {decline(deleted_files, 'блок', ('а', 'ов', 'ов'))} удал{'ены' if pl else 'ён'}"
    )


@bot.message_handler(commands=['problem_block_delete'], roles=['owner'])
def problem_block_delete(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problem_block = get_problem_block_from_arg(message)
    problem_block.id
    response = f"Блок задач {problem_block} будет удалён безвозвратно. Ты уверен?"
    bot.send_message(
        message.chat.id, 
        response, 
        reply_markup=quick_markup({
            'Да': {'callback_data': f'delete_block_{problem_block.id}'},
            'Нет': {'callback_data': 'delete_block_cancel'}
        })
    )


@bot.callback_query_handler(lambda callback_query: callback_query.data.startswith('delete_block_'))
def delete_block_handler(callback_query: CallbackQuery):
    message = callback_query.message
    bot.delete_message(message.chat.id, message.id)
    if callback_query.data.endswith('_cancel'):
        bot.send_message(message.chat.id, "Действие отменено", reply_markup=participant_keyboard)
        return
    problem_block_id = int(callback_query.data[len('delete_block_'):])
    problem_block = ProblemBlock.from_id(problem_block_id)
    problem_block.delete()
    bot.send_message(callback_query.from_user.id, f"Блок {problem_block} удалён")


@bot.message_handler(commands=['choose_problems'], roles=['examiner'], olymp_statuses=[OlympStatus.REGISTRATION])
def examiner_problems(message: Message, state: StateContext):
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    response = "Выбери задачу, чтобы добавить её в свой список задач или убрать её из него\n" + examiner.display_problem_data()
    all_problems = current_olymp.get_problems(sort=True)
    reply_buttons = ReplyKeyboardMarkup(resize_keyboard=True)
    problem_row = []
    for problem in all_problems:
        problem_row.append(problem.name)
        if len(problem_row) == 3:
            reply_buttons.row(*problem_row)
            problem_row = []
    if len(problem_row) > 0:
        reply_buttons.row(*problem_row)
    reply_buttons.row("[Закончить выбор]")
    state.set(ExaminerStates.choosing_problems)
    bot.send_message(message.chat.id, response, reply_markup=reply_buttons)


@bot.message_handler(state=ExaminerStates.choosing_problems, roles=['examiner'])
def examiner_chooses_problem(message: Message, state: StateContext):
    if message.text == "[Закончить выбор]":
        state.delete()
        bot.send_message(message.chat.id, "Выбор сохранён", reply_markup=ReplyKeyboardRemove())
        return
    problem = Problem.from_name(message.text, current_olymp.id)
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    if problem.id in examiner.problems:
        examiner.remove_problem(problem)
        response = f"Задача {problem} удалена из твоего списка задач"
    else:
        examiner.add_problem(problem)
        response = f"Задача {problem} добавлена в твой список задач"
    response += "\n" + examiner.display_problem_data()
    bot.send_message(message.chat.id, response)


@bot.message_handler(
    commands=['free', 'busy'], roles=['examiner'], 
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE], discussing_examiner=False
)
def examiner_busyness_status(message: Message):
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    command = extract_command(message.text)
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
    bot.send_message(
        message.chat.id,
        response
    )
    if not examiner.is_busy:
        queue_entry = examiner.look_for_queue_entry()
        if queue_entry:
            examiner.assign_to_queue_entry(queue_entry)
            announce_queue_entry(queue_entry)


def announce_queue_entry(queue_entry: QueueEntry):
    participant: Participant = Participant.from_id(queue_entry.participant_id)
    problem: Problem = Problem.from_id(queue_entry.problem_id)
    problem_number = participant.get_problem_number(problem)
    
    # Нет принимающего
    if not queue_entry.examiner_id:
        if queue_entry.status == QueueStatus.WAITING:
            response = (f"Ты теперь в очереди на задачу {problem_number}: {problem}. "
                        f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится")
            if PROMOTE_COMMANDS:
                response += f"\nЧтобы покинуть очередь, используй команду /leave_queue"
            bot.send_message(participant.tg_id, response, reply_markup=participant_keyboard_in_queue)
            return
        elif queue_entry.status == QueueStatus.CANCELED:
            if current_olymp.status == OlympStatus.CONTEST:
                response = "Ты больше не в очереди"
                if PROMOTE_COMMANDS:
                    response += (". Чтобы записаться на сдачу задачи снова, используй команду <code>"
                                 + escape_html("/queue <номер задачи>")
                                 + "</code>")
                keyboard = participant_keyboard
            else:
                response = "Ты больше не в очереди. Олимпиада завершена, можешь отправляться на заслуженный отдых"
                keyboard = participant_keyboard_olymp_finished
            bot.send_message(participant.tg_id, response, reply_markup=keyboard)
            if current_olymp.status == OlympStatus.QUEUE and not current_olymp.unhandled_queue_left():
                finish_olymp()
            return
    
    examiner: Examiner = Examiner.from_id(queue_entry.examiner_id)

    # Приём завершён
    if queue_entry.status not in QueueStatus.active():
        new_problem_block = None
        if queue_entry.status == QueueStatus.FAIL:
            participant_response = f"Задача {problem_number}: {problem} не принята"
            if current_olymp.status == OlympStatus.CONTEST:
                attempts = participant.attempts_left(problem)
                participant_response += (f". У тебя {decline(attempts, 'остал', ('ась', 'ось', 'ось'))} "
                                        f"{attempts} {decline(attempts, 'попыт', ('ка', 'ки', 'ок'))}, "
                                        f"чтобы её сдать")
            examiner_response = (f"Задача {problem} отмечена как несданная участником {participant.name} {participant.surname}")
        elif queue_entry.status == QueueStatus.CANCELED:
            participant_response = (f"Сдача задачи {problem_number}: {problem} отменена. Ты больше не в очереди")
            if current_olymp.status == OlympStatus.CONTEST:
                participant_response += ". Попытка не потрачена"
            examiner_response = (f"Сдача задачи {problem} участником {participant.name} {participant.surname} отменена")
        elif queue_entry.status == QueueStatus.SUCCESS:
            participant_response = f"Задача {problem_number}: {problem} принята! Поздравляем"
            if current_olymp.status == OlympStatus.CONTEST and participant.should_get_new_problem(problem):
                new_problem_block = participant.give_next_problem_block()
                participant_response += (f"\nЗа решение этой задачи тебе полагается {participant.last_block_number} блок задач. "
                                         f"Теперь можешь сдавать и их!")
            examiner_response = (f"Задача {problem} отмечена "
                                 f"как успешно сданная участником {participant.name} {participant.surname}")
        if current_olymp.status == OlympStatus.CONTEST:
            if PROMOTE_COMMANDS:
                participant_response += ("\nЧтобы записаться на сдачу задачи, используй команду <code>"
                                         + escape_html("/queue <номер задачи>")
                                         + "</code>")
            keyboard = participant_keyboard
        else:
            participant_response += "\nОлимпиада завершена, можешь отправляться на заслуженный отдых"
            keyboard = participant_keyboard_olymp_finished
        unhandled_queue_left = current_olymp.unhandled_queue_left()
        if current_olymp.status == OlympStatus.CONTEST or unhandled_queue_left:
            examiner_response += "\n❗️ Чтобы продолжить принимать задачи, используй команду /free"
        if new_problem_block:
            bot.send_document(
                participant.tg_id, 
                document=InputFile(new_problem_block.path, f"Блок_{participant.last_block_number}.pdf"),
                caption=participant_response,
                reply_markup=keyboard
            )
        else:
            bot.send_message(participant.tg_id, participant_response, reply_markup=keyboard)
        bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove() if not examiner.queue_entry else None)
        if current_olymp.status == OlympStatus.QUEUE and not unhandled_queue_left:
            finish_olymp()
        return
    
    # Принимающий только что назначен
    participant_response = (f"Задачу {problem_number}: {problem} "
                            f"у тебя примет {examiner.name} {examiner.surname}.\n"
                            f"Ссылка: {examiner.conference_link}")
    bot.send_message(
        participant.tg_id, 
        participant_response, 
        reply_markup=(quick_markup({'Принимающий не пришёл': {'callback_data': 'examiner_didnt_come'}})
                      if NO_EXAMINER_COMPLAINTS else ReplyKeyboardRemove())
    )
    examiner_response = (f"К тебе идёт сдавать задачу {problem} "
                         f"участник {participant.name} {participant.surname} ({participant.grade} класс). "
                         f"Ты можешь принять или отклонить решение, а также отменить сдачу (например, если участник "
                         f"не пришёл или если ты не хочешь учитывать эту сдачу как потраченную попытку)")
    if current_olymp.status == OlympStatus.QUEUE:
        examiner_response += (f"\n\nЕсли участник случайно нажал не на ту задачу, пожалуйста, напиши {OWNER_HANDLE}! "
                              "И <strong>не нажимай ни на какую из кнопок!</strong>")
    examiner_keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    examiner_keyboard.add("Принято", "Не принято", "Отмена")
    bot.send_message(examiner.tg_id, examiner_response, reply_markup=examiner_keyboard)


@bot.message_handler(commands=['withdraw_examiner'], roles=['owner'], olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE])
def withdraw_examiner(message: Message):
    examiner_tg_handle = get_arg(message, no_arg_error="Необходимо указать хэндл принимающего")
    examiner: Examiner = Examiner.from_tg_handle(examiner_tg_handle, current_olymp.id)
    participant: Participant = Participant.from_id(examiner.queue_entry.participant_id)
    examiner.withdraw_from_queue_entry()
    examiner_response = (f"{participant.name} {participant.surname} пожаловался(-лась), что тебя не было на приёме задачи. "
                         f"Пожалуйста, используй команду /busy, если тебе надо отойти!\n"
                         f"❗️ Бот установил тебе статус \"занят(-а)\". "
                         f"Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
    bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
    return_participant_to_queue(participant)


@bot.callback_query_handler(
    lambda callback_query: callback_query.data.startswith('examiner_didnt_come'), 
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE]
)
def examiner_didnt_come_handler(callback_query: CallbackQuery):
    if not NO_EXAMINER_COMPLAINTS:
        bot.answer_callback_query(callback_query.id)
        return
    data = callback_query.data
    message = callback_query.message
    if data == 'examiner_didnt_come_cancel':
        bot.delete_message(message.chat.id, message.id)
        bot.answer_callback_query(callback_query.id, "Действие отменено")
        return
    participant: Participant = Participant.from_tg_id(callback_query.from_user.id, current_olymp.id)
    if not participant.queue_entry:
        raise UserError("Пожаловаться, что принимающий не пришёл, можно только при сдаче задачи")
    if participant.queue_entry.status != QueueStatus.DISCUSSING:
        raise UserError("Пожаловаться, что принимающий не пришёл, можно только при сдаче задачи")
    if data == 'examiner_didnt_come':
        response = "Ты точно хочешь пожаловаться, что принимающего нет, и вернуться в очередь?"
        buttons = quick_markup({
            'Да, принимающего нет': {'callback_data': 'examiner_didnt_come_confirmed'},
            'Нет, я нажал(-а) случайно': {'callback_data': 'examiner_didnt_come_cancel'}
        })
        bot.send_message(message.chat.id, response, reply_markup=buttons)
        bot.answer_callback_query(callback_query.id)
        return
    bot.edit_message_reply_markup(message.chat.id, message.id, reply_markup=None)
    examiner: Examiner = Examiner.from_id(participant.queue_entry.examiner_id)
    examiner.withdraw_from_queue_entry()
    examiner_response = (f"{participant.name} {participant.surname} отметил(-а), что тебя не было на приёме задачи. "
                         f"Пожалуйста, используй команду /busy, если тебе надо отойти!\n"
                         f"❗️ Бот установил тебе статус \"занят(-а)\". "
                         f"Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
    bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
    queue_entry = participant.queue_entry
    owner_message = (f"{participant.name} {participant.surname} ({participant.grade} класс) пожаловался(-лась), "
                     f"что принимающего {examiner.name} {examiner.surname} не было на приёме задачи "
                     f"(запись в очереди: <code>{queue_entry.id}</code>)")
    bot.send_message(OWNER_ID, owner_message)
    bot.answer_callback_query(callback_query.id, "Мы сообщили организаторам о проблеме")
    return_participant_to_queue(participant)


@bot.message_handler(regexp=rf'(/queue( \d+)?|{JOIN_QUEUE_BUTTON})', roles=['participant'], olymp_statuses=[OlympStatus.CONTEST])
def queue(message: Message):
    temp_reply = bot.send_message(message.chat.id, "Обработка запроса…", reply_markup=ReplyKeyboardRemove())
    participant: Participant = Participant.from_tg_id(message.from_user.id, current_olymp.id)
    if participant.queue_entry:
        problem = Problem.from_id(participant.queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        error_message = f"Ты уже в очереди на задачу {problem_number}: {problem}"
        if PROMOTE_COMMANDS:
            error_message += ". Чтобы покинуть очередь, используй команду /leave_queue"
        bot.delete_message(temp_reply.chat.id, temp_reply.id)
        raise UserError(error_message, reply_markup=participant_keyboard_in_queue)
    if match := re.match(r"/queue (\d+)", message.text):
        match: re.Match
        problem_number = int(match.group(1))
        bot.delete_message(temp_reply.chat.id, temp_reply.id)
        join_queue(participant, problem_number)
        return
    bot.delete_message(temp_reply.chat.id, temp_reply.id)
    bot.send_message(message.chat.id, "Выбери задачу для сдачи", reply_markup=participant_keyboard_choose_problem(participant))


@bot.callback_query_handler(
    lambda callback_query: callback_query.data.startswith('join_queue_'),
    olymp_statuses=[OlympStatus.CONTEST])
def join_queue_handler(callback_query: CallbackQuery):
    message = callback_query.message
    bot.delete_message(message.chat.id, message.id)
    if callback_query.data.endswith('_cancel'):
        bot.send_message(message.chat.id, "Действие отменено", reply_markup=participant_keyboard)
        return
    participant: Participant = Participant.from_tg_id(callback_query.from_user.id, current_olymp.id)
    queue_entry = participant.queue_entry
    if queue_entry:
        problem = Problem.from_id(queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        error_message = f"Ты уже в очереди на задачу {problem_number}: {problem}"
        if PROMOTE_COMMANDS:
            error_message += ". Чтобы покинуть очередь, используй команду /leave_queue"
        raise UserError(error_message, reply_markup=participant_keyboard_in_queue)
    problem_number = int(callback_query.data[len('join_queue_'):])
    join_queue(participant, problem_number)


def join_queue(participant: Participant, problem_number: int):
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


@bot.message_handler(
    regexp=rf'(/leave_queue|{LEAVE_QUEUE_BUTTON})', 
    roles=['participant'], 
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE]
)
def leave_queue(message: Message):
    participant: Participant = Participant.from_tg_id(message.from_user.id, current_olymp.id)
    queue_entry = participant.queue_entry
    if not queue_entry:
        error_message = "Ты уже не в очереди"
        if PROMOTE_COMMANDS:
            error_message += (". Чтобы записаться на сдачу задачи, используй команду <code>"
                              + escape_html("/queue <номер задачи>")
                              + "</code>")
        raise UserError(error_message, reply_markup=participant_keyboard)
    if queue_entry.status != QueueStatus.WAITING:
        raise UserError("Нельзя покинуть очередь во время сдачи задач")
    if current_olymp.status == OlympStatus.QUEUE:
        response = "Олимпиада завершена — если ты покинешь очередь, то больше не сможешь сдавать задачи"
    else:
        response = ("Осторожно! Если ты покинешь очередь, то потеряешь своё место в ней. "
                    "При повторной записи на задачу ты окажешься в конце")
    bot.send_message(
        message.chat.id,
        response,
        reply_markup=quick_markup({
            'Покинуть очередь': {'callback_data': 'leave_queue_confirm'},
            'Отмена': {'callback_data': 'leave_queue_cancel'}
        })
    )


@bot.callback_query_handler(
    lambda callback_query: callback_query.data.startswith('leave_queue_'), 
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE]
)
def leave_queue_handler(callback_query: CallbackQuery):
    message = callback_query.message
    bot.delete_message(message.chat.id, message.id)
    if callback_query.data.endswith('_cancel'):
        bot.send_message(message.chat.id, "Действие отменено", reply_markup=participant_keyboard_in_queue)
        return
    participant: Participant = Participant.from_tg_id(callback_query.from_user.id, current_olymp.id)
    queue_entry = participant.queue_entry
    if not queue_entry:
        error_message = "Ты уже не в очереди"
        if PROMOTE_COMMANDS:
            error_message += (". Чтобы записаться в очередь, используй команду <code>"
                              + escape_html("/queue <номер задачи>")
                              + "</code>")
        raise UserError(error_message, reply_markup=participant_keyboard)
    if queue_entry.status != QueueStatus.WAITING:
        raise UserError("Нельзя покинуть очередь во время сдачи задач")
    queue_entry.status = QueueStatus.CANCELED
    announce_queue_entry(queue_entry)


@bot.message_handler(commands=['give_out_second_block', 'give_out_third_block'], roles=['owner'], olymp_statuses=[OlympStatus.CONTEST])
def give_out_problem_block(message: Message):
    command = extract_command(message.text)
    new_problem_block_number = 2 if command == 'give_out_second_block' else 3
    participants = current_olymp.get_participants()
    issues = 0
    receivers = 0
    for p in participants:
        last_block_number = p.last_block_number
        if last_block_number < new_problem_block_number - 1:
            issues += 1
        if last_block_number < new_problem_block_number:
            receivers += 1
            new_problem_block = p.give_next_problem_block()
            if p.tg_id:
                participant_reply = (f"Тебе выдан {'второй' if last_block_number + 1 == 2 else 'завершающий третий'} блок задач. "
                                     f"Теперь можешь решать и сдавать их тоже!")
                if PROMOTE_COMMANDS:
                    participant_reply += (f"\nЧтобы записаться на сдачу задачи, используй команду <code>"
                                          + escape_html("/queue <номер задачи>")
                                          + "</code>")
                bot.send_document(
                    p.tg_id,
                    InputFile(new_problem_block.path, f"Блок_{last_block_number + 1}.pdf"),
                    caption=participant_reply
                )
    owner_reply = (f"{'Второй' if new_problem_block_number == 2 else 'Третий'} блок задач выдан "
                   f"{receivers} {decline(receivers, 'участник', ('у', 'ам', 'ам'))}")
    if issues:
        owner_reply += f"\n⚠️ У {issues} {decline(issues, 'участник', ('а', 'ов', 'ов'))} не было предыдущего блока!"
    bot.send_message(message.chat.id, owner_reply)


@bot.message_handler(commands=['give_second_block', 'give_third_block'], roles=['owner'], olymp_statuses=[OlympStatus.CONTEST])
def give_problem_block(message: Message):
    command = extract_command(message.text)
    new_problem_block_number = 2 if command == 'give_second_block' else 3
    arg = get_arg(message, "Необходимо указать хэндл участника")
    participant: Participant = Participant.from_tg_handle(arg, current_olymp.id)
    if participant.last_block_number != new_problem_block_number - 1:
        raise UserError(f"Последний блок участника {participant.name} {participant.surname} — блок {participant.last_block_number}")
    problem_block = participant.give_next_problem_block()
    participant_reply = (f"Тебе выдан {'второй' if participant.last_block_number == 2 else 'завершающий третий'} блок задач. "
                         f"Теперь можешь решать и сдавать их тоже!")
    if PROMOTE_COMMANDS:
        participant_reply += ("\nЧтобы записаться на сдачу задачи, используй команду <code>"
                              + escape_html("/queue <номер задачи>")
                              + "</code>")
    bot.send_document(
        participant.tg_id,
        InputFile(problem_block.path, f"Блок_{participant.last_block_number}.pdf"),
        caption=participant_reply
    )
    bot.send_message(
        message.chat.id,
        f"{'Второй' if new_problem_block_number == 2 else 'Третий'} блок задач выдан участнику {participant.name} {participant.surname}"
    )


@bot.message_handler(commands=['send_to_participant', 'send_to_examiner'], roles=['owner'])
def send_command(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    message_to_send = message.reply_to_message
    if not message_to_send:
        raise UserError("Эту команду нужно использовать в ответ на сообщение")
    tg_handle = get_arg(message, "Необходимо указать Телеграм-хэндл")
    command = extract_command(message.text)
    member_class = Participant if command == 'send_to_participant' else Examiner
    member_class_name = "Участник" if command == 'send_to_participant' else "Принимающий"
    member: Participant | Examiner = member_class.from_tg_handle(tg_handle, current_olymp.id)
    if not member.tg_id:
        raise UserError(f"{member.name} {member.surname} не авторизован(-а). Невозможно переслать сообщение…")
    bot.copy_message(member.tg_id, message_to_send.chat.id, message_to_send.id)
    bot.send_message(
        message.chat.id,
        f"{member_class_name} {member.name} {member.surname} получил оповещение!",
        reply_to_message_id=message_to_send.id
    )


@bot.message_handler(commands=['announce_to_participants', 'announce_to_examiners', 'announce_to_everyone'], roles=['owner'])
def announce_command(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    announcement = message.reply_to_message
    if not announcement:
        raise UserError("Эту команду нужно использовать в ответ на сообщение")
    command = extract_command(message.text)
    send_to_participants = (command != 'announce_to_examiners')
    send_to_examiners = (command != 'announce_to_participants')
    owner_response = ""
    p_amount = 0
    e_amount = 0
    if send_to_participants:
        for p in current_olymp.get_participants():
            if p.tg_id:
                bot.copy_message(p.tg_id, announcement.chat.id, announcement.id)
                p_amount += 1
        owner_response += f"{p_amount} {decline(p_amount, 'участник', ('', 'а', 'ов'))}"
    if send_to_participants and send_to_examiners:
        owner_response += " и "
    if send_to_examiners:
        for e in current_olymp.get_examiners():
            if e.tg_id:
                bot.copy_message(e.tg_id, announcement.chat.id, announcement.id)
                e_amount += 1
        owner_response += f"{e_amount} {decline(e_amount, 'принимающ', ('ий', 'их', 'их'))}"
    owner_response += " получил" + ("и" if p_amount + e_amount > 1 else "") + " оповещение!"
    bot.send_message(message.chat.id, owner_response, reply_to_message_id=announcement.id)


@bot.message_handler(
    commands=['results'],
    roles=['owner', 'examiner'],
    olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE, OlympStatus.RESULTS]
)
def results_command(message: Message):
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
        if participant.is_junior: junior_table = pd.concat([junior_table, pd.DataFrame([row], columns=COLUMNS.keys())], ignore_index=True)
        else:                     senior_table = pd.concat([senior_table, pd.DataFrame([row], columns=COLUMNS.keys())], ignore_index=True)
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
    bot.send_document(
        message.chat.id, 
        InputFile(excel_path, file_name=f"{current_olymp.name.replace(' ', '_')}_результаты.xlsx"),
        caption="Результаты олимпиады"
    )


@bot.message_handler(discussing_examiner=True)
def examiner_buttons_callback(message: Message):
    result_status = QueueStatus.from_text(message.text, no_error = True)
    if not result_status or result_status in QueueStatus.active():
        bot.send_message(message.chat.id, "Выбери результат сдачи на клавиатуре")
        return
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    queue_entry: QueueEntry = examiner.queue_entry
    queue_entry.status = result_status
    announce_queue_entry(queue_entry)


@bot.message_handler(regexp=r"/.+")
def other_commands(message: Message):
    raise UserError("Неизвестная команда")

@bot.message_handler(roles=['not owner'])
def other_messages(message: Message):
    raise UserError(f"Неизвестная команда\n"
                    f"Если у тебя есть организационные вопросы касательно олимпиады, "
                    f"пиши нам в <a href=\"vk.com/hseling.for.school\">группе ВКонтакте</a> "
                    f"и в <a href=\"t.me/hselingforschool\">Телеграм-канале</a>\n"
                    f"Если у тебя есть вопросы по работе бота, пиши {OWNER_HANDLE}",
                    contact_note=False)


print("Запускаю бота...")

owner_startup_message = "Бот запущен!"
if not current_olymp:
    owner_startup_message += (
        "\nТекущая олимпиада не выбрана. Чтобы установить текущую олимпиаду, используй команду <code>"
        + escape_html("/olymp_select <название>")
        + "</code>")
bot.send_message(OWNER_ID, owner_startup_message)

bot.infinity_polling()
