import os
import telebot.formatting
from data import TOKEN, OWNER_ID, OWNER_HANDLE
import telebot
from telebot.types import Message
from telebot.custom_filters import SimpleCustomFilter
from olymp import Olymp, OlympStatus
from users import Participant, Examiner
from utils import UserError


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
                             f"{exc.__class__.__name__} "
                             f"({filename}, строка {line_number}): {exc}")
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