import os
from data import TOKEN, OWNER_ID, OWNER_HANDLE
import telebot
from telebot.types import Message
from telebot.custom_filters import SimpleCustomFilter
from olymp import Olymp, OlympStatus
from users import OlympMember, Participant, Examiner
from utils import UserError, decline
import requests
import pandas as pd


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
            error_message = "Ошибка!\n" + str(exc)
            handled = True
        else:
            traceback = exc.__traceback__
            while traceback.tb_next: traceback = traceback.tb_next
            filename = os.path.split(traceback.tb_frame.f_code.co_filename)[1]
            line_number = traceback.tb_lineno
            error_message = (f"Во время выполнения операции произошла ошибка:\n"
                             f"`{exc.__class__.__name__} "
                             f"({filename}, строка {line_number}): {exc}`")
        error_message += f"\nЕсли тебе кажется, что это баг, сообщи {OWNER_HANDLE}"
        bot.send_message(message.chat.id, error_message)
        return handled


bot = telebot.TeleBot(TOKEN, parse_mode="markdown", disable_web_page_preview=True, exception_handler=MyExceptionHandler())

class OwnerOnly(SimpleCustomFilter):
    key = 'owner_only'
    @staticmethod
    def check(message: Message):
        return message.from_user.id == OWNER_ID

bot.add_custom_filter(OwnerOnly())

current_olymp = Olymp.current()


@bot.message_handler(commands=['raise_error'])
def raise_error(message: Message):
    raise UserError("Пользователь лох")

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
    if new_member:
        new_member.tg_id = tg_id
        response = ("Ты успешно авторизовался как "
                    + ("участник" if isinstance(new_member, Participant) else "принимающий")
                    + "!\n" + new_member.display_data())
        bot.send_message(message.chat.id, response)
        return
    
    bot.send_message(message.chat.id, f"Пользователь не найден. Если вы регистрировались на олимпиаду, напишите {OWNER_HANDLE}")


@bot.message_handler(commands=['olymp_registration_start', 'olymp_reg_start'], owner_only = True)
def olymp_reg_start(message: Message):
    if not current_olymp:
        bot.send_message(message.chat.id, "Нет текущей олимпиады")
        return
    if current_olymp.status == OlympStatus.REGISTRATION:
        bot.send_message(message.chat.id, "Регистрация уже идёт")
        return
    if current_olymp.status != OlympStatus.TBA:
        bot.send_message(message.chat.id, "Олимпиада уже идёт или завершилась")
        return
    current_olymp.status = OlympStatus.REGISTRATION
    bot.send_message(message.chat.id, f"Регистрация на олимпиаду _{current_olymp.name}_ запущена")


@bot.message_handler(commands=['olymp_finish'], owner_only = True)
def olymp_finish(message: Message):
    if not current_olymp:
        bot.send_message(message.chat.id, "Нет текущей олимпиады")
        return
    if current_olymp.status == OlympStatus.RESULTS:
        bot.send_message(message.chat.id, "Олимпиада уже завершена")
        return
    if current_olymp.status == OlympStatus.QUEUE:
        bot.send_message(message.chat.id, "Олимпиада уже завершается, происходит работа с очередью")
        return
    if current_olymp.status != OlympStatus.CONTEST:
        bot.send_message(message.chat.id, "Олимпиада ещё не начата")
        return
    
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


@bot.message_handler(commands=['olymp_start'], owner_only = True)
def olymp_start(message: Message):
    if not current_olymp:
        bot.send_message(message.chat.id, "Нет текущей олимпиады")
        return
    if current_olymp.status == OlympStatus.TBA:
        bot.send_message(message.chat.id, "Сначала необходимо запустить регистрацию")
        return
    if current_olymp.status != OlympStatus.REGISTRATION:
        bot.send_message(message.chat.id, "Олимпиада уже идёт или завершилась")
        return
    current_olymp.status = OlympStatus.CONTEST
    participants = current_olymp.get_participants()
    for p in participants:
        if p.tg_id:
            bot.send_message(p.tg_id, "Олимпиада началась! Можешь приступать к решению задач")
    examiners = current_olymp.get_examiners()
    for e in examiners:
        if e.tg_id:
            bot.send_message(e.tg_id, "Олимпиада началась! Напиши /free и ожидай участников")
    bot.send_message(message.chat.id, f"Олимпиада _{current_olymp.name}_ начата")


@bot.message_handler(commands=['olymp_create'], owner_only=True)
def olymp_create(message: Message):
    global current_olymp
    if current_olymp and current_olymp.status != OlympStatus.RESULTS:
        bot.send_message(
            message.chat.id,
            f"Уже имеется незавершённая олимпиада _{current_olymp.name}_. Заверши её, чтобы создать новую"
        )
        return
    command_arg = message.text.split(maxsplit=1)
    if len(command_arg) == 1:
        bot.send_message(message.chat.id, "Для олимпиады необходимо название")
        return
    name = command_arg[1]
    current_olymp = Olymp.create(name)
    bot.send_message(message.chat.id, f"Олимпиада _{current_olymp.name}_ успешно создана")


def upload_members(message: Message, required_columns: list[str], member_class: type[OlympMember],
                   term_stem: str, term_endings: tuple[str]):
    if not current_olymp:
        bot.send_message(message.chat.id, "Нет текущей олимпиады")
        return
    if current_olymp.status != OlympStatus.TBA:
        bot.send_message(message.chat.id, "Регистрация на олимпиаду или олимпиада уже начата")
        return
    if not message.document and not (message.reply_to_message and message.reply_to_message.document):
        bot.send_message(message.chat.id, "Необходимо указать Excel-таблицу")
        return
    document: telebot.types.Document = message.document or message.reply_to_message.document
    file_path = bot.get_file(document.file_id).file_path
    file_data = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}").content
    member_table = pd.read_excel(file_data)
    if set(member_table.columns) != set(required_columns):
        bot.send_message(message.chat.id, "Таблица должна содержать столбцы " + ', '.join([f'`{col}`' for col in required_columns]))
        return
    for i, m in member_table.iterrows():
        member_class.create_as_new_user(**m, olymp_id=current_olymp.id, ok_if_user_exists=True)
    amount = member_table.shape[0]
    response = (f"{amount} {decline(amount, term_stem, term_endings)} успешно "
                f"{decline(amount, 'добавлен', ('', 'ы', 'ы'))} в олимпиаду _{current_olymp.name}_")
    bot.send_message(message.chat.id, response)


@bot.message_handler(commands=['upload_participants'], owner_only=True)
def upload_participants(message: Message):
    upload_members(message, ["name", "surname", "tg_handle", "grade"], Participant, 'участник', ('', 'а', 'ов'))


@bot.message_handler(commands=['upload_examiners'], owner_only=True)
def upload_examiners(message: Message):
    upload_members(message, ["name", "surname", "tg_handle", "conference_link"], Examiner, 'принимающ', ('ий', 'их', 'их'))


@bot.message_handler(commands=['olymp_info'], owner_only=True)
def olymp_info(message: Message):
    if not current_olymp:
        response = f"Нет текущей олимпиады"
    else:
        response = (f"Олимпиада _{current_olymp.name}_:\n"
                    f"ID: `{current_olymp.id}`\n"
                    f"Состояние: `{current_olymp.status.name}`")
    bot.send_message(message.chat.id, response)

# @bot.message_handler(func=lambda message: True)
# def echo_all(message: Message):
#     bot.send_message(message.chat.id, message.text)

print("Запускаю бота...")
bot.infinity_polling()