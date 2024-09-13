import os
from db import DATABASE, create_tables
from users import User, Participant, Examiner
from olymp import Olymp, OlympStatus
from queue_entry import QueueEntry, QueueStatus
from problem import Problem

class TestError:
    """Ошибка при тестировании"""

print(f"При тестировании база данных {DATABASE} будет очищена. Ты сделал бэкап? (напиши Да чтобы продолжить)")
answer = input()
if answer.strip().lower() != "да":
    raise InterruptedError("Тестирование отменено")

if os.path.exists(DATABASE):
    os.remove(DATABASE)
create_tables("db.sql")

Olymp.create(1, "тестище", OlympStatus.RESULTS)
olymp2007: Olymp = Olymp.create(2007, "верните мне мой")
ajno: User = User.create("ajnoeo", "Тёма", "Бойко")
ajno_p: Participant = Participant.create_for_existing_user(ajno, 12, olymp2007.id)
ajno_e: Examiner = Examiner.create_for_existing_user(ajno, "aboba.ru", [1,3,6], olymp2007.id, is_busy=False)
p: Participant = Participant.create_as_new_user("lifelong_participant", "Участник", "Вечный", 1, olymp2007.id, tg_id=12345)
e: Examiner = Examiner.create_as_new_user("lifelong_examiner", "Принимающий", "Бесконечно", "lifelong.ex", [4], olymp2007.id)
ajno_p.name = "Артём"
ajno_e.is_busy = True
p.grade = 2
e.busyness_level += 1
print(Participant.from_id(2).name)
print(Examiner.from_id(2).surname)
problem_1: Problem = Problem.create(olymp2007.id, 1, None, "minors")
Problem.create(olymp2007.id, 2, 1, "majors")
problem_2: Problem = Problem.from_junior_number(olymp2007.id, 2)
print(problem_2.name)
problem_1.junior_number = 3
print(Problem.from_id(1).junior_number)
