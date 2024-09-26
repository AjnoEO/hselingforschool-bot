import os
from pathlib import Path
import re
from db import create_update_db
from data import TOKEN, OWNER_ID, OWNER_HANDLE
import telebot
from telebot.types import Message, CallbackQuery, InputFile, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telebot.formatting import escape_markdown
from telebot.custom_filters import SimpleCustomFilter, AdvancedCustomFilter
from telebot.util import quick_markup
from olymp import Olymp, OlympStatus
from users import User, OlympMember, Participant, Examiner
from problem import Problem, ProblemBlock, BlockType
from queue_entry import QueueEntry, QueueStatus
from utils import UserError, decline, get_arg, get_n_args, get_file
import pandas as pd
from io import BytesIO


create_update_db()


class MyExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exc: Exception):
        message = None
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
        else:
            traceback = exc.__traceback__
            while traceback.tb_next: traceback = traceback.tb_next
            filename = os.path.split(traceback.tb_frame.f_code.co_filename)[1]
            line_number = traceback.tb_lineno
            error_message = (f"⚠️ Во время выполнения операции произошла ошибка:\n"
                             f"`{exc.__class__.__name__} "
                             f"({filename}, строка {line_number}): {exc}`")
        error_message += f"\nЕсли тебе кажется, что это баг, сообщи {OWNER_HANDLE}"
        bot.send_message(message.chat.id, error_message, reply_markup=ReplyKeyboardRemove())
        return handled


bot = telebot.TeleBot(TOKEN, parse_mode="markdown", disable_web_page_preview=True, exception_handler=MyExceptionHandler())

class RolesFilter(AdvancedCustomFilter): # owner, examiner, participant
    key = 'roles'
    @staticmethod
    def check(message: Message, roles: list[str]):
        if 'owner' in roles and message.from_user.id == OWNER_ID:
            return True
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


@bot.message_handler(commands=['start', 'authenticate'])
def send_welcome(message: Message):
    if not current_olymp or current_olymp.status == OlympStatus.TBA:
        response = ("Привет!\n"
                    "Пока что никакой олимпиады нет, но ты можешь следить за обновлениями "
                    "в каналах УОЛ и Лингвокружка:\n"
                    "- ВКонтакте: vk.com/hseling.for.school\n"
                    "- Телеграм: t.me/hselingforschool\n"
                    "- Сайт: ling.hse.ru/junior")
        bot.send_message(message.chat.id, response)
        return
    
    olymp_id = current_olymp.id
    tg_id = message.from_user.id
    member: Participant | Examiner | None = \
        Participant.from_tg_id(tg_id, olymp_id, no_error=True) or Examiner.from_tg_id(tg_id, olymp_id, no_error=True)
    if member:
        response = ("Ты уже авторизован как "
                    + ("участник" if isinstance(member, Participant) else "принимающий")
                    + "!\n" + member.display_data())
        bot.send_message(message.chat.id, response)
        return
    tg_handle = message.from_user.username
    new_member: Participant | Examiner | None = \
        Participant.from_tg_handle(tg_handle, olymp_id, no_error=True) or Examiner.from_tg_handle(tg_handle, olymp_id, no_error=True)
    user_old_handle = User.from_tg_id(tg_id, no_error=True)
    if user_old_handle and new_member:
        bot.send_message(
            message.chat.id, 
            f"У тебя поменялся хэндл в Телеграме с @{user_old_handle.tg_handle} на @{new_member.tg_handle}?",
            reply_markup=quick_markup(
                {'Да': {'callback_data': 'handle_changed_yes'}, 'Нет': {'callback_data': 'handle_changed_no'}},
                row_width=2
            )
        )
        return
    
    if new_member:
        new_member.tg_id = tg_id
        response = ("Ты успешно авторизовался как "
                    + ("участник" if isinstance(new_member, Participant) else "принимающий")
                    + "!\n" + new_member.display_data())
        bot.send_message(message.chat.id, response)
        return
    
    bot.send_message(message.chat.id, f"Пользователь не найден. Если вы регистрировались на олимпиаду, напишите {OWNER_HANDLE}")


@bot.callback_query_handler(lambda callback_query: callback_query.data.startswith('handle_changed_'))
def handle_change_handler(callback_query: CallbackQuery):
    handle_changed = (callback_query.data.endswith('_yes'))
    message = callback_query.message
    bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=message.id, reply_markup=None)
    if not handle_changed:
        bot.send_message(message.chat.id, f"Что-то пошло не так. Напиши {OWNER_HANDLE}")
    else:
        old_user = User.from_tg_id(callback_query.from_user.id)
        new_user = User.from_tg_handle(callback_query.from_user.username)
        old_user.conflate_with(new_user)
        user_id = old_user.user_id
        olymp_id = current_olymp.id
        member: Participant | Examiner | None = \
            Participant.from_user_id(user_id, olymp_id, no_error=True) or Examiner.from_user_id(user_id, olymp_id, no_error=True)
        if member:
            response = ("Ты успешно авторизовался как "
                        + ("участник" if isinstance(member, Participant) else "принимающий")
                        + "!\n" + member.display_data())
            bot.send_message(message.chat.id, response)
            bot.answer_callback_query(callback_query.id)
            return


@bot.message_handler(commands=['olymp_registration_start', 'olymp_reg_start'], roles=['owner'])
def olymp_reg_start(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status == OlympStatus.REGISTRATION:
        raise UserError("Регистрация уже идёт")
    if current_olymp.status != OlympStatus.TBA:
        raise UserError("Олимпиада уже идёт или завершилась")
    current_olymp.status = OlympStatus.REGISTRATION
    bot.send_message(message.chat.id, f"Регистрация на олимпиаду _{current_olymp.name}_ запущена")


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
    for p in participants:
        if p.tg_id:
            problem_block = p.last_block
            bot.send_document(
                p.tg_id, 
                document=InputFile(problem_block.path, "Блок_1.pdf"),
                caption="Олимпиада началась! Можешь приступать к решению задач"
            )
    examiners = current_olymp.get_examiners()
    for e in examiners:
        if e.tg_id:
            bot.send_message(e.tg_id, "Олимпиада началась! Напиши /free и ожидай участников")
    bot.send_message(message.chat.id, f"Олимпиада _{current_olymp.name}_ начата")


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
        current_olymp.status = OlympStatus.RESULTS
        e_message = "Олимпиада завершилась! Очередь пуста, так что можешь идти отдыхать"
        for p in participants:
            if p.tg_id:
                bot.send_message(p.tg_id, p_not_in_queue_message)
        for e in examiners:
            if e.tg_id:
                bot.send_message(e.tg_id, e_message)
        bot.send_message(message.chat.id, "Олимпиада завершена. Очередь полностью обработана")


@bot.message_handler(commands=['olymp_create'], roles=['owner'])
def olymp_create(message: Message):
    global current_olymp
    if current_olymp and current_olymp.status != OlympStatus.RESULTS:
        bot.send_message(
            message.chat.id,
            f"Уже имеется незавершённая олимпиада _{current_olymp.name}_. Заверши её, чтобы создать новую"
        )
        return
    name = get_arg(message, "Для олимпиады необходимо название")
    current_olymp = Olymp.create(name)
    bot.send_message(message.chat.id, f"Олимпиада _{current_olymp.name}_ успешно создана")


def upload_members(message: Message, required_columns: list[str], member_class: type[OlympMember],
                   term_stem: str, term_endings: tuple[str]):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    if current_olymp.status != OlympStatus.TBA:
        raise UserError("Регистрация на олимпиаду или олимпиада уже начата")
    file_data = get_file(message, bot, "Необходимо указать Excel-таблицу")
    member_table = pd.read_excel(BytesIO(file_data))
    if set(member_table.columns) != set(required_columns):
        bot.send_message(message.chat.id, "Таблица должна содержать столбцы " + ', '.join([f'`{col}`' for col in required_columns]))
        return
    for i, m in member_table.iterrows():
        member_class.create_as_new_user(**m, olymp_id=current_olymp.id, ok_if_user_exists=True)
    amount = member_table.shape[0]
    response = (f"{amount} {decline(amount, term_stem, term_endings)} успешно "
                f"{decline(amount, 'добавлен', ('', 'ы', 'ы'))} в олимпиаду _{current_olymp.name}_")
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['upload_participants'], roles=['owner'])
def upload_participants(message: Message):
    upload_members(message, ["name", "surname", "tg_handle", "grade"], Participant, 'участник', ('', 'а', 'ов'))


@bot.message_handler(commands=['upload_examiners'], roles=['owner'])
def upload_examiners(message: Message):
    upload_members(message, ["name", "surname", "tg_handle", "conference_link"], Examiner, 'принимающ', ('ий', 'их', 'их'))


@bot.message_handler(commands=['olymp_info'], roles=['owner'])
def olymp_info(message: Message):
    if not current_olymp:
        response = f"Нет текущей олимпиады"
    else:
        response = (f"Олимпиада _{current_olymp.name}_:\n"
                    f"ID: `{current_olymp.id}`\n"
                    f"Состояние: `{current_olymp.status.name}`")
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['problem_create'], roles=['owner'])
def problem_create(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    name = get_arg(message, "Для задачи необходимо название")
    problem = Problem.create(current_olymp.id, name)
    bot.send_message(message.chat.id, f"Задача _{problem.name}_ добавлена! ID: `{problem.id}`")


@bot.message_handler(commands=['problem_list'], roles=['owner', 'examiner'])
def problem_list(message: Message):
    if not current_olymp:
        raise UserError("Нет текущей олимпиады")
    problems = current_olymp.get_problems()
    if len(problems) == 0:
        bot.send_message(message.chat.id, f"В олимпиаде _{current_olymp.name}_ ещё нет задач")
        return
    response = f"Задачи олимпиады _{current_olymp.name}_:\n"
    for p in problems:
        response += f"- `{p.id}` _{p.name}_\n"
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
    response += (f"Информация о задаче _{problem.name}_:\n"
                f"ID: `{problem.id}`\n")
    blocks = problem.get_blocks()
    if len(blocks) == 0:
        response += "Задача не входит ни в какие блоки"
    else:
        response += "Блоки задач:\n"
        for block in blocks:
            if block.block_type:
                response += f"- {block.block_type.description()}\n"
            else:
                response += f"- Блок `{block.id}`\n"
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
            raise UserError("Тип блока должен быть указан в форме `(JUNIOR|SENIOR)_(1|2|3)`")
        block_type = BlockType[args[3]]
    filename = telebot.util.generate_random_token()
    dir = "downloaded_files"
    Path(dir).mkdir(exist_ok=True)
    path = os.path.join(dir, filename + ".pdf")
    with open(path, "wb") as f:
        f.write(file)
    problem_block = ProblemBlock.create(current_olymp.id, problems, block_type=block_type, path=path)
    response = (f"Блок `{problem_block.id}` "
                + (f"({problem_block.block_type.description()}) " if problem_block.block_type else "")
                + f"создан!\nЗадачи:\n")
    for problem in problem_block.problems:
        response += f"- `{problem.id}` _{problem.name}_\n"
    bot.send_message(message.chat.id, response)


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
        response = f"Задача _{escape_markdown(problem.name)}_ удалена из твоего списка задач"
    else:
        examiner.add_problem(problem)
        response = f"Задача _{escape_markdown(problem.name)}_ добавлена в твой список задач"
    response += "\n" + examiner.display_problem_data()
    bot.send_message(message.chat.id, response)
    bot.register_next_step_handler_by_chat_id(message.chat.id, examiner_chooses_problem)


@bot.message_handler(commands=['free', 'busy'], roles=['examiner'], olymp_statuses=[OlympStatus.CONTEST, OlympStatus.QUEUE], discussing_examiner=False)
def examiner_busyness_status(message: Message):
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    command = telebot.util.extract_command(message.text)
    if command == 'free' and not examiner.is_busy:
        raise UserError("Ты уже свободен(-на). Если хочешь отметить, что ты занят(-а), используй команду /busy")
    if command == 'busy' and examiner.is_busy:
        raise UserError("Ты уже занят(-а). Если хочешь отметить, что ты свободен(-на), используй команду /free")
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
    
    if not queue_entry.examiner_id:
        if queue_entry.status == QueueStatus.WAITING:
            response = (f"Ты теперь в очереди на задачу {problem_number}: _{escape_markdown(problem.name)}_. "
                        f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится. "
                        f"Чтобы покинуть очередь, используй команду /leave\_queue")
            bot.send_message(participant.tg_id, response)
            return
        elif queue_entry.status == QueueStatus.CANCELED:
            response = ("Ты больше не в очереди. Чтобы записаться на сдачу задачи снова, используй команду `/queue <номер задачи>`")
            bot.send_message(participant.tg_id, response)
            return
    
    examiner: Examiner = Examiner.from_id(queue_entry.examiner_id)

    if queue_entry.status == QueueStatus.FAIL:
        attempts = participant.attempts_left(problem)
        participant_response = (f"Задача {problem_number} _{escape_markdown(problem.name)}_ не принята. "
                                f"У тебя {decline(attempts, 'остал', ('ась', 'ось', 'ось'))} {attempts} {decline(attempts, 'попыт', ('ка', 'ки', 'ок'))}, "
                                f"чтобы её сдать\n"
                                f"Чтобы записаться на сдачу задачи, используй команду `/queue <номер задачи>`")
        bot.send_message(participant.tg_id, participant_response)
        examiner_response = (f"Задача _{escape_markdown(problem.name)}_ отмечена как несданная участником {participant.name} {participant.surname}. "
                             f"Чтобы продолжить принимать задачи, используй команду /free")
        bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
        return
    if queue_entry.status == QueueStatus.CANCELED:
        participant_response = ("Сдача задачи отменена. Ты больше не в очереди. Попытка не потрачена\n"
                                "Чтобы записаться на сдачу задачи снова, используй команду `/queue <номер задачи>`")
        bot.send_message(participant.tg_id, participant_response)
        examiner_response = (f"Сдача задачи _{escape_markdown(problem.name)}_ участником {participant.name} {participant.surname} отменена. "
                             f"Чтобы продолжить принимать задачи, используй команду /free")
        bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
        return
    if queue_entry.status == QueueStatus.SUCCESS:
        participant_response = (f"Задача {problem_number} _{escape_markdown(problem.name)}_ принята! Поздравляем\n"
                                f"Чтобы записаться на сдачу другой задачи, используй команду `/queue <номер задачи>`")
        bot.send_message(participant.tg_id, participant_response)
        examiner_response = (f"Задача _{escape_markdown(problem.name)}_ отмечена как успешно сданная участником {participant.name} {participant.surname}. "
                             f"Чтобы продолжить принимать задачи, используй команду /free")
        bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
        return
    participant_response = (f"Задачу {problem_number} _{escape_markdown(problem.name)}_ у тебя примет {examiner.name} {examiner.surname}.\n"
                            f"Ссылка: {examiner.conference_link}")
    bot.send_message(participant.tg_id, participant_response, reply_markup=quick_markup({'Принимающий не пришёл': {'callback_data': 'examiner_didnt_come'}}))
    examiner_response = (f"К тебе идёт сдавать задачу _{escape_markdown(problem.name)}_ "
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
                         f"Бот установил тебе статус \"занят(-а)\". Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
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
        participant_response = (f"Вернули тебя в начало очереди на задачу {problem_number}: _{escape_markdown(problem.name)}_. "
                                f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится. "
                                f"Чтобы покинуть очередь, используй команду /leave\_queue")
        bot.send_message(participant.tg_id, participant_response)


@bot.message_handler(discussing_examiner = True)
def examiner_buttons_callback(message: Message):
    result_status = QueueStatus.from_message(message.text, no_error = True)
    if not result_status:
        bot.send_message(message.chat.id, "Выбери результат сдачи на клавиатуре")
        return
    examiner: Examiner = Examiner.from_tg_id(message.from_user.id, current_olymp.id)
    queue_entry: QueueEntry = examiner.queue_entry
    queue_entry.status = result_status
    announce_queue_entry(queue_entry)


@bot.callback_query_handler(lambda callback_query: callback_query.data.startswith('examiner_didnt_come'))
def examiner_didnt_come_handler(callback_query: CallbackQuery):
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
                         f"Бот установил тебе статус \"занят(-а)\". Когда вернёшься, используй команду /free, чтобы продолжить принимать задачи")
    bot.send_message(examiner.tg_id, examiner_response, reply_markup=ReplyKeyboardRemove())
    queue_entry = participant.queue_entry
    owner_message = (f"{participant.name} {participant.surname} ({participant.grade} класс) пожаловался(-лась), "
                     f"что принимающего {examiner.name} {examiner.surname} не было на приёме задачи "
                     f"(запись в очереди: `{queue_entry.id}`)")
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
        participant_response = (f"Вернули тебя в начало очереди на задачу {problem_number}: _{escape_markdown(problem.name)}_. "
                                f"Свободных принимающих пока нет, но бот напишет тебе, когда подходящий принимающий освободится. "
                                f"Чтобы покинуть очередь, используй команду /leave\_queue")
        bot.send_message(participant.tg_id, participant_response)


@bot.message_handler(commands=['queue'], roles=['participant'], olymp_statuses=[OlympStatus.CONTEST])
def join_queue(message: Message):
    participant: Participant = Participant.from_tg_id(message.from_user.id, current_olymp.id)
    if participant.queue_entry:
        problem = Problem.from_id(participant.queue_entry.problem_id)
        problem_number = participant.get_problem_number(problem)
        raise UserError(f"Ты уже в очереди на задачу {problem_number}: _{problem.name}_. Чтобы покинуть очередь, используй команду /leave\_queue")
    args = get_arg(message, "Необходимо указать номер задачи")
    if not args.isnumeric():
        raise UserError("Необходимо указать номер задачи")
    problem_number = int(args)
    problem = participant.problem_from_number(problem_number)
    queue_entry = participant.join_queue(problem)
    announce_queue_entry(queue_entry)


@bot.message_handler(commands=['leave_queue'], roles=['participant'], olymp_statuses=[OlympStatus.CONTEST])
def leave_queue(message: Message):
    participant: Participant = Participant.from_tg_id(message.from_user.id, current_olymp.id)
    if not participant.queue_entry:
        raise UserError("Ты уже не в очереди. Чтобы записаться на сдачу задачи, используй команду `/queue <номер задачи>`")
    queue_entry = participant.queue_entry
    if queue_entry.status != QueueStatus.WAITING:
        raise UserError("Нельзя покинуть очередь во время сдачи задач")
    queue_entry.status = QueueStatus.CANCELED
    announce_queue_entry(queue_entry)


print("Запускаю бота...")
bot.infinity_polling()