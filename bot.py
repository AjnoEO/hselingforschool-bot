import os
from pathlib import Path
import re
import json
from db import create_update_db
from data import TOKEN, OWNER_ID, OWNER_HANDLE
import telebot
from telebot.types import Message, CallbackQuery, InputFile, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telebot.formatting import escape_html
from telebot.custom_filters import SimpleCustomFilter, AdvancedCustomFilter
from telebot.util import quick_markup, extract_command
from olymp import Olymp, OlympStatus
from users import User, OlympMember, Participant, Examiner
from problem import Problem, ProblemBlock, BlockType
from queue_entry import QueueEntry, QueueStatus
from utils import UserError, decline, get_arg, get_n_args, get_file
import pandas as pd
from io import BytesIO


PROMOTE_COMMANDS = False # Подсказывать ли команды участникам
NO_EXAMINER_COMPLAINTS = False # Давать ли участникам возможность пожаловаться на то, что принимающий не пришёл


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
        handled = False
        if isinstance(exc, UserError):
            error_message = "⚠️ Ошибка!\n" + str(exc)
            handled = True
            reply_markup = exc.reply_markup
        else:
            traceback = exc.__traceback__
            while traceback.tb_next: traceback = traceback.tb_next
            filename = os.path.split(traceback.tb_frame.f_code.co_filename)[1]
            line_number = traceback.tb_lineno
            error_message = (f"⚠️ Во время выполнения операции произошла ошибка:\n"
                             f"<code>{exc.__class__.__name__} "
                             f"({filename}, строка {line_number}): {exc}</code>")
        error_message += f"\nЕсли тебе кажется, что это баг, сообщи {OWNER_HANDLE}"
        bot.send_message(message.chat.id, error_message, reply_markup=reply_markup)
        return handled


bot = telebot.TeleBot(TOKEN, parse_mode="HTML", disable_web_page_preview=True, exception_handler=MyExceptionHandler())

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
    def check(_: Message, statuses: list[OlympStatus]):
        if not current_olymp:
            return None in statuses
        return current_olymp.status in statuses
    
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
    if isinstance(member, Examiner):
        if not current_olymp.status == OlympStatus.CONTEST:
            response += "\nЧтобы выбрать задачи для приёма, используй команду /choose_problems"
        elif member.is_busy:
            response += "\nЧтобы начать принимать задачи, используй команду /free"
        response += "\nЧтобы просмотреть информацию о себе, используй команду /my_info"
    elif current_olymp.status == OlympStatus.REGISTRATION:
        response += ("\nДата и время начала олимпиады есть в <a href=\"vk.com/hseling.for.school\">нашей группе ВКонтакте</a> "
                     "и в <a href=\"t.me/hselingforschool\">нашем Телеграм-канале</a>\n"
                     "Когда олимпиада начнётся, бот пришлёт тебе задания и ты сможешь записываться на сдачу задач через него")
    bot.send_message(member.tg_id, response)
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
    role_titles = len(roles) > 1
    for role in roles:
        with open(os.path.join("help", f"{role}.json"), encoding="utf8") as f:
            data = json.load(f)
        if role_titles:
            commands.append(data["title"])
        commands += data["commands"]
    response = ""
    for block in commands:
        if isinstance(block, str):
            response += f"<strong>{block}</strong>\n\n"
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
    sum = 0
    for i, problem in enumerate(participant.problems()):
        response += f"- <strong>{i+1}: <em>{escape_html(problem.name)}</em></strong> — "
        attempts = participant.attempts_left(problem)
        if participant.solved(problem): 
            response += f"решена ({attempts} {decline(attempts, 'балл', ('', 'а', 'ов'))})\n"
            sum += attempts
        else:
            response += (f"не решена, {decline(attempts, 'остал', ('ась', 'ось', 'ось'))} "
                         f"{attempts} {decline(attempts, 'попыт', ('ка', 'ки', 'ок'))} из 3\n")
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
                f"{examiner.display_data(contact_note=False)}\n")
    problems = [Problem.from_id(problem_id) for problem_id in examiner.problems]
    if len(problems) == 0:
        response += f"Задач нет\n"
    else:
        response += f"<strong>Задачи:</strong>\n"
        for problem_id in examiner.problems:
            problem = Problem.from_id(problem_id)
            response += f"- <em>{escape_html(problem.name)}</em>\n"
    if current_olymp.status != OlympStatus.REGISTRATION:
        response += (
            f"<strong>Статус:</strong> {'занят(-а)' if examiner.is_busy else 'свободен(-на)'}\n"
            f"<strong>Обсуждений с участниками:</strong> {examiner.busyness_level}"
        )
    response += f"\nЕсли в данных есть ошибка, сообщи {OWNER_HANDLE}"
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


@bot.message_handler(commands=['olymp_start'], roles=['owner'])
def olymp_start(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.TBA:
        raise UserError("Сначала необходимо запустить регистрацию")
    if current_olymp.status != OlympStatus.REGISTRATION:
        raise UserError("Олимпиада уже идёт или завершилась")
    current_olymp.status = OlympStatus.CONTEST
    participants = current_olymp.get_participants()
    participant_message = (f"Олимпиада началась! Можешь приступать к решению задач\n"
                           f"Если у тебя возникли вопросы, обращайся к {OWNER_HANDLE}")
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
            bot.send_message(e.tg_id, "Олимпиада началась! Напиши /free и ожидай участников")
    bot.send_message(message.chat.id, f"Олимпиада <em>{current_olymp.name}</em> начата")


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


def upload_members(message: Message, required_columns: list[str], member_class: type[OlympMember],
                   term_stem: str, term_endings: tuple[str], term_endings_gen: tuple[str]):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status != OlympStatus.TBA:
        raise UserError("Регистрация на олимпиаду или олимпиада уже начата")
    file_data = get_file(message, bot, "Необходимо указать Excel-таблицу")
    member_table = pd.read_excel(BytesIO(file_data))
    if not set(member_table.columns).issubset(set(required_columns)):
        raise UserError("Таблица должна содержать столбцы " + ', '.join([f'`{col}`' for col in required_columns]))
    updated_users: list[tuple[User, member_class]] = []
    for _, m in member_table.iterrows():
        old_user = None
        if user := User.from_tg_handle(m["tg_handle"], no_error=True):
            if user.name != m["name"] or user.surname != m["surname"]:
                old_user = user
        member: member_class = member_class.create_as_new_user(**m, olymp_id=current_olymp.id, ok_if_user_exists=True)
        if old_user:
            updated_users.append((old_user, member))
    amount = member_table.shape[0]
    response = (f"{amount} {decline(amount, term_stem, term_endings)} успешно "
                f"{decline(amount, 'добавлен', ('', 'ы', 'ы'))} в олимпиаду <em>{current_olymp.name}</em>")
    updated_amount = len(updated_users)
    if updated_amount > 0:
        response += f"\nУ {updated_amount} {decline(updated_amount, term_stem, term_endings_gen)} обновилась информация:"
        for old_user, member in updated_users:
            response += f"\n- {old_user.name} {old_user.surname} → {member.name} {member.surname} (@{member.tg_handle})"
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['upload_participants'], roles=['owner'], content_types=['text', 'document'])
def upload_participants(message: Message):
    upload_members(
        message, ["name", "surname", "tg_handle", "grade"], 
        Participant, 'участник', ('', 'а', 'ов'), ('а', 'ов', 'ов')
    )


@bot.message_handler(commands=['upload_examiners'], roles=['owner'], content_types=['text', 'document'])
def upload_examiners(message: Message):
    upload_members(
        message, ["name", "surname", "tg_handle", "conference_link"],
        Examiner, 'принимающ', ('ий', 'их', 'их'), ('его', 'их', 'их')
    )


@bot.message_handler(commands=['add_participant'], roles=['owner'])
def add_participant(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    tg_handle, name, surname, *numbers = get_n_args(
        message, 4, 5, "Необходимо указать Телеграм-хэндл, имя, фамилию, класс и, при необходимости, последний блок участника"
    )
    grade = int(numbers[0])
    last_block_number = int(numbers[1]) if len(numbers) > 1 else None
    participant: Participant = Participant.create_as_new_user(
        tg_handle, name, surname, grade, current_olymp.id,
        last_block_number=last_block_number, ok_if_user_exists=True
    )
    bot.send_message(message.chat.id, f"{participant.name} {participant.surname} добавлен(-а) в список участников")


@bot.message_handler(commands=['edit_participant'], roles=['owner'])
def edit_participant(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    syntax_hint = ("Синтаксис команды: <code>"
                   + escape_html("/edit_participant <tg-хэндл> <tg_handle|name|surname|grade> <значение>")
                   + "</code>")
    tg_handle, key, value = get_n_args(message, 3, 3, syntax_hint)
    if key not in ['tg_handle', 'name', 'surname', 'grade']:
        raise UserError(syntax_hint)
    if key == 'grade': value = int(value)
    participant: Participant = Participant.from_tg_handle(tg_handle, current_olymp.id)
    match key:
        case 'tg_handle':
            change = f"Телеграм-хэндл: @{participant.tg_handle} → {value}"
            participant.tg_handle = value
        case 'name':
            change = f"Имя: {participant.name} → {value}"
            participant.name = value
        case 'surname':
            change = f"Фамилия: {participant.surname} → {value}"
            participant.surname = value
        case 'grade':
            change = f"Класс участия: {participant.grade} → {value}"
            participant.grade = value
    bot.send_message(message.chat.id, f"Данные участника {participant.name} {participant.surname} обновлены:\n" + change)
    if participant.tg_id:
        bot.send_message(
            participant.tg_id,
            f"Твои данные обновлены:\n" + change + "\nПросмотреть информацию о себе можно при помощи команды /my_info"
        )


@bot.message_handler(commands=['add_examiner'], roles=['owner'])
def add_examiner(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    tg_handle, name, surname, conference_link, problems = get_n_args(
        message, 5, 5, "Необходимо указать Телеграм-хэндл, имя, фамилию, ссылку на конференцию и задачи принимающего"
    )
    problems = list(map(int, problems.split())) if problems[0] != '0' else None
    examiner: Examiner = Examiner.create_as_new_user(
        tg_handle, name, surname, conference_link, current_olymp.id,
        problems = problems, ok_if_user_exists = True
    )
    bot.send_message(message.chat.id, f"{examiner.name} {examiner.surname} добавлен(-а) в список принимающих")


@bot.message_handler(commands=['edit_participant'], roles=['owner'])
def edit_participant(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    syntax_hint = ("Синтаксис команды: <code>"
                   + escape_html("/edit_examiner <tg-хэндл> <tg_handle|name|surname|conference_link> <значение>")
                   + "</code>")
    tg_handle, key, value = get_n_args(message, 3, 3, syntax_hint)
    if key not in ['tg_handle', 'name', 'surname', 'conference_link']:
        raise UserError(syntax_hint)
    examiner: Examiner = Examiner.from_tg_handle(tg_handle, current_olymp.id)
    match key:
        case 'tg_handle':
            change = f"Телеграм-хэндл: @{examiner.tg_handle} → {value}"
            examiner.tg_handle = value
        case 'name':
            change = f"Имя: {examiner.name} → {value}"
            examiner.name = value
        case 'surname':
            change = f"Фамилия: {examiner.surname} → {value}"
            examiner.surname = value
        case 'conference_link':
            change = f"Ссылка на конференцию: {examiner.conference_link} → {value}"
            examiner.conference_link = value
    bot.send_message(message.chat.id, f"Данные принимающего {examiner.name} {examiner.surname} обновлены:\n" + change)
    if examiner.tg_id:
        bot.send_message(
            examiner.tg_id, 
            f"Твои данные обновлены:\n" + change + "\nПросмотреть информацию о себе можно при помощи команды /my_info"
        )


@bot.message_handler(commands=['set_examiner_problems'], roles=['owner'])
def set_examiner_problems(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    tg_handle, problems = get_n_args(message, 2, 2, "Необходимо указать Телеграм-хэндл принимающего и задачи")
    problems = list(map(int, problems.split())) if problems[0] != '0' else None
    examiner: Examiner = Examiner.from_tg_handle(tg_handle, current_olymp.id)
    examiner.set_problems(problems)
    examiner_response = "Твой список задач обновлён:\n"
    for problem_id in problems:
        examiner_response += f"- <em>{escape_html(Problem.from_id(problem_id).name)}</em>\n"
    bot.send_message(examiner.tg_id, examiner_response)
    bot.send_message(message.chat.id, f"Список задач принимающего {examiner.name} {examiner.surname} обновлён")


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
        response += (f"- <strong>Задача:</strong> <code>{problem.id}</code> <em>{escape_html(problem.name)}</em>\n"
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
        participant: Participant = Participant.from_id(queue_entry.participant_id)
        bot.send_message(participant.tg_id, message.reply_to_message.text)
    announce_queue_entry(queue_entry)



@bot.message_handler(commands=['problem_create'], roles=['owner'])
def problem_create(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    name = get_arg(message, "Для задачи необходимо название")
    problem = Problem.create(current_olymp.id, name)
    bot.send_message(message.chat.id, f"Задача <em>{escape_html(problem.name)}</em> добавлена! ID: <code>{problem.id}</code>")


@bot.message_handler(commands=['problem_rename'], roles=['owner'])
def problem_rename(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    id, name = get_n_args(message, 2, 2, "Необходимо указать ID задачи и новое название")
    problem = Problem.from_id(int(id))
    if problem.olymp_id != current_olymp.id:
        raise UserError("Задача не относится к текущей олимпиаде")
    problem.name = name
    bot.send_message(message.chat.id, f"Задача <code>{problem.id}</code> переименована: <em>{escape_html(problem.name)}</em>")


@bot.message_handler(commands=['problem_list'], roles=['owner', 'examiner'])
def problem_list(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problems = current_olymp.get_problems()
    if len(problems) == 0:
        bot.send_message(message.chat.id, f"В олимпиаде <em>{current_olymp.name}</em> ещё нет задач")
        return
    response = f"Задачи олимпиады <em>{current_olymp.name}</em>:\n"
    for p in problems:
        response += f"- <code>{p.id}</code> <em>{escape_html(p.name)}</em>\n"
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
        response += "⚠️ Задача не относится к текущей олимпиаде\n"
    response += (f"Информация о задаче <em>{escape_html(problem.name)}</em>:\n"
                f"ID: <code>{problem.id}</code>\n")
    blocks = problem.get_blocks()
    if len(blocks) == 0:
        response += "Задача не входит ни в какие блоки"
    else:
        response += "Блоки задач:\n"
        for block in blocks:
            if block.block_type:
                response += f"- {block.block_type}\n"
            else:
                response += f"- Блок <code>{block.id}</code>\n"
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['problem_block_create'], roles=['owner'])
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
    filename = telebot.util.generate_random_token()
    dir = "downloaded_files"
    Path(dir).mkdir(exist_ok=True)
    path = os.path.join(dir, filename + ".pdf")
    with open(path, "wb") as f:
        f.write(file)
    problem_block = ProblemBlock.create(current_olymp.id, problems, block_type=block_type, path=path)
    response = (f"Блок <code>{problem_block.id}</code> "
                + (f"({problem_block.block_type}) " if problem_block.block_type else "")
                + f"создан!\nЗадачи:\n")
    for problem in problem_block.problems:
        response += f"- <code>{problem.id}</code> <em>{escape_html(problem.name)}</em>\n"
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['problem_block_delete'], roles=['owner'])
def problem_block_delete(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    arg = get_arg(message, "Необходимо указать ID или тип блока")
    if re.match(r"^(JUNIOR|SENIOR)_[123]$", arg):
        block_type = BlockType[arg]
        problem_block = ProblemBlock.from_block_type(current_olymp.id, block_type)
    elif arg.isnumeric():
        id = int(arg)
        problem_block = ProblemBlock.from_id(id)
    if problem_block.olymp_id != current_olymp.id:
        raise UserError("Блок задач не относится к текущей олимпиаде")
    problem_block.id
    response = f"Блок задач <code>{problem_block.id}</code>"
    if problem_block.block_type:
        response += f" ({problem_block.block_type})"
    response += " будет удалён безвозвратно. Ты уверен?"
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
    bot.send_message(callback_query.from_user.id, f"Блок <code>{problem_block.id}</code> удалён")


@bot.message_handler(commands=['choose_problems'], roles=['examiner'], olymp_statuses=[OlympStatus.REGISTRATION])
def examiner_problems(message: Message):
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    response = "Выбери задачу, чтобы добавить её в свой список задач или убрать её из него\n" + examiner.display_problem_data()
    all_problems = current_olymp.get_problems()
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
    bot.send_message(message.chat.id, response, reply_markup=reply_buttons)
    bot.register_next_step_handler_by_chat_id(message.chat.id, examiner_chooses_problem)


def examiner_chooses_problem(message: Message):
    if message.text == "[Закончить выбор]":
        bot.send_message(message.chat.id, "Выбор сохранён", reply_markup=ReplyKeyboardRemove())
        return
    problem = Problem.from_name(message.text, current_olymp.id)
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    if problem.id in examiner.problems:
        examiner.remove_problem(problem)
        response = f"Задача <em>{escape_html(problem.name)}</em> удалена из твоего списка задач"
    else:
        examiner.add_problem(problem)
        response = f"Задача <em>{escape_html(problem.name)}</em> добавлена в твой список задач"
    response += "\n" + examiner.display_problem_data()
    bot.send_message(message.chat.id, response)
    bot.register_next_step_handler_by_chat_id(message.chat.id, examiner_chooses_problem)


@bot.message_handler(commands=['free', 'busy'], roles=['examiner'], olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE], discussing_examiner=False)
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
            response = (f"Ты теперь в очереди на задачу {problem_number}: <em>{escape_html(problem.name)}</em>. "
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
            participant_response = f"Задача {problem_number}: <em>{escape_html(problem.name)}</em> не принята"
            if current_olymp.status == OlympStatus.CONTEST:
                attempts = participant.attempts_left(problem)
                participant_response += (f". У тебя {decline(attempts, 'остал', ('ась', 'ось', 'ось'))} "
                                        f"{attempts} {decline(attempts, 'попыт', ('ка', 'ки', 'ок'))}, "
                                        f"чтобы её сдать")
            examiner_response = (f"Задача <em>{escape_html(problem.name)}</em> отмечена "
                                 f"как несданная участником {participant.name} {participant.surname}")
        elif queue_entry.status == QueueStatus.CANCELED:
            participant_response = (f"Сдача задачи {problem_number}: <em>{escape_html(problem.name)}</em> отменена. "
                                    f"Ты больше не в очереди")
            if current_olymp.status == OlympStatus.CONTEST:
                participant_response += ". Попытка не потрачена"
            examiner_response = (f"Сдача задачи <em>{escape_html(problem.name)}</em> "
                                 f"участником {participant.name} {participant.surname} отменена")
        elif queue_entry.status == QueueStatus.SUCCESS:
            participant_response = f"Задача {problem_number}: <em>{escape_html(problem.name)}</em> принята! Поздравляем"
            if current_olymp.status == OlympStatus.CONTEST and participant.should_get_new_problem(problem):
                new_problem_block = participant.give_next_problem_block()
                participant_response += (f"\nЗа решение этой задачи тебе полагается {participant.last_block_number} блок задач. "
                                         f"Теперь можешь сдавать и их!")
            examiner_response = (f"Задача <em>{escape_html(problem.name)}</em> отмечена "
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
            examiner_response += "\nЧтобы продолжить принимать задачи, используй команду /free"
        if new_problem_block:
            bot.send_document(
                participant.tg_id, 
                document=InputFile(new_problem_block.path, f"Блок_{participant.last_block_number}.pdf"),
                caption=participant_response,
                reply_markup=keyboard
            )
        else:
            bot.send_message(participant.tg_id, participant_response, reply_markup=keyboard)
        bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
        if current_olymp.status == OlympStatus.QUEUE and not unhandled_queue_left:
            finish_olymp()
        return
    
    # Принимающий только что назначен
    participant_response = (f"Задачу {problem_number}: <em>{escape_html(problem.name)}</em> "
                            f"у тебя примет {examiner.name} {examiner.surname}.\n"
                            f"Ссылка: {examiner.conference_link}")
    bot.send_message(
        participant.tg_id, 
        participant_response, 
        reply_markup=quick_markup({'Принимающий не пришёл': {'callback_data': 'examiner_didnt_come'}}) if NO_EXAMINER_COMPLAINTS else None 
    )
    examiner_response = (f"К тебе идёт сдавать задачу <em>{escape_html(problem.name)}</em> "
                         f"участник {participant.name} {participant.surname} ({participant.grade} класс). "
                         f"Ты можешь принять или отклонить решение, а также отменить сдачу (например, если участник "
                         f"не пришёл или если ты не хочешь учитывать эту сдачу как потраченную попытку)")
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
                         f"Бот установил тебе статус \"занят(-а)\". "
                         f"Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
    bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
    queue_entry = participant.queue_entry
    new_examiner_id = queue_entry.look_for_examiner()
    if new_examiner_id:
        new_examiner: Examiner = Examiner.from_id(new_examiner_id)
        new_examiner.assign_to_queue_entry(queue_entry)
        announce_queue_entry(queue_entry)
    else:
        problem = Problem.from_id(queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        participant_response = (f"Вернули тебя в начало очереди на задачу {problem_number}: <em>{escape_html(problem.name)}</em>. "
                                f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится")
        if PROMOTE_COMMANDS:
            participant_response += f"Чтобы покинуть очередь, используй команду /leave_queue"
        bot.send_message(participant.tg_id, participant_response, reply_markup=participant_keyboard_in_queue)


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
                         f"Бот установил тебе статус \"занят(-а)\". "
                         f"Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
    bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
    queue_entry = participant.queue_entry
    owner_message = (f"{participant.name} {participant.surname} ({participant.grade} класс) пожаловался(-лась), "
                     f"что принимающего {examiner.name} {examiner.surname} не было на приёме задачи "
                     f"(запись в очереди: <code>{queue_entry.id}</code>)")
    bot.send_message(OWNER_ID, owner_message)
    bot.answer_callback_query(callback_query.id, "Мы сообщили организаторам о проблеме")
    new_examiner_id = queue_entry.look_for_examiner()
    if new_examiner_id:
        new_examiner: Examiner = Examiner.from_id(new_examiner_id)
        new_examiner.assign_to_queue_entry(queue_entry)
        announce_queue_entry(queue_entry)
    else:
        problem = Problem.from_id(queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        participant_response = (f"Вернули тебя в начало очереди на задачу {problem_number}: <em>{escape_html(problem.name)}</em>. "
                                f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится")
        if PROMOTE_COMMANDS:
            participant_response += f"Чтобы покинуть очередь, используй команду /leave_queue"
        bot.send_message(participant.tg_id, participant_response, reply_markup=participant_keyboard_in_queue)


@bot.message_handler(regexp=rf'(/queue( \d+)?|{JOIN_QUEUE_BUTTON})', roles=['participant'], olymp_statuses=[OlympStatus.CONTEST])
def queue(message: Message):
    temp_reply = bot.send_message(message.chat.id, "Обработка запроса…", reply_markup=ReplyKeyboardRemove())
    participant: Participant = Participant.from_tg_id(message.from_user.id, current_olymp.id)
    if participant.queue_entry:
        problem = Problem.from_id(participant.queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        error_message = f"Ты уже в очереди на задачу {problem_number}: <em>{escape_html(problem.name)}</em>"
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
        error_message = f"Ты уже в очереди на задачу {problem_number}: <em>{escape_html(problem.name)}</em>"
        if PROMOTE_COMMANDS:
            error_message += ". Чтобы покинуть очередь, используй команду /leave_queue"
        raise UserError(error_message, reply_markup=participant_keyboard_in_queue)
    problem_number = int(callback_query.data[len('join_queue_'):])
    join_queue(participant, problem_number)


def join_queue(participant: Participant, problem_number: int):
    problem = participant.problem_from_number(problem_number)
    if participant.attempts_left(problem) <= 0:
        raise UserError(f"У тебя не осталось попыток, чтобы сдать задачу {problem_number}: <em>{escape_html(problem.name)}</em>… "
                        f"Стоит заняться другими задачами",
                        reply_markup=participant_keyboard)
    if participant.solved(problem):
        raise UserError(f"Задача {problem_number}: <em>{escape_html(problem.name)}</em> уже сдана! Займись другими задачами",
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


@bot.message_handler(regexp=r"/.+")
def other_commands(message: Message):
    raise UserError("Неизвестная команда")

@bot.message_handler()
def other_messages(message: Message):
    raise UserError("Неизвестная команда\n"
                    "Если у тебя есть организационные вопросы касательно олимпиады, "
                    "пиши нам в <a href=\"vk.com/hseling.for.school\">группе ВКонтакте</a> "
                    "и в <a href=\"t.me/hselingforschool\">Телеграм-канале</a>")


print("Запускаю бота...")
bot.infinity_polling()