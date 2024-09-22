import os
from db import DATABASE, create_tables
from users import User, Participant, Examiner
from olymp import Olymp, OlympStatus
from queue_entry import QueueEntry, QueueStatus
from problem import Problem, ProblemBlock, BlockType

class TestError:
    """Ошибка при тестировании"""

print(f"При тестировании база данных {DATABASE} будет очищена. Ты сделал бэкап? (напиши Да чтобы продолжить)")
answer = input()
if answer.strip().lower() != "да":
    raise InterruptedError("Тестирование отменено")

if os.path.exists(DATABASE):
    os.remove(DATABASE)
create_tables("db.sql")

olymp_test = Olymp.create("тестище", OlympStatus.TBA)
olymp_test.status = OlympStatus.RESULTS
olymp2007: Olymp = Olymp.create("верните мне мой 2007")
ajno: User = User.create("ajnoeo", "Тёма", "Бойко")
bejno_p: Participant = Participant.create_as_new_user("bjnoeo", "Света", "Бойко", 12, olymp2007.id)
ajno_e: Examiner = Examiner.create_for_existing_user(ajno, "aboba.ru", [1,3,6], olymp2007.id, is_busy=False)
p: Participant = Participant.create_as_new_user("lifelong_participant", "Участник", "Вечный", 1, olymp2007.id)
e: Examiner = Examiner.create_as_new_user("lifelong_examiner", "Принимающий", "Бесконечно", "lifelong.ex", [4], olymp2007.id)
bejno_p.name = "Бртём"
ajno_e.is_busy = True
p.grade = 2
e.busyness_level += 1
print(Participant.from_id(2).name)
print(Examiner.from_id(2).surname)
print(ajno_e.queue_entry)
problem_1: Problem = Problem.create(olymp2007.id, "minors")
Problem.create(olymp2007.id, "majors")
problem_2 = Problem.from_id(2)
problem_3: Problem = Problem.create(olymp2007.id, "mezors?")
try:
    ProblemBlock.create(olymp2007.id, [problem_1, problem_1, problem_1])
except Exception as err:
    print(f"Ура, оно упало: {err}")
try:
    ProblemBlock.create(olymp2007.id, [problem_2])
except Exception as err:
    print(f"Ура, оно упало: {err}")
problem_block = ProblemBlock.create(olymp2007.id, [problem_1, problem_2, problem_3], BlockType.JUNIOR_1, "jun1.pdf")
# problem_2: Problem = Problem.from_junior_number(olymp2007.id, 2)
# print(problem_2.name)
# problem_1.junior_number = 3
# print(Problem.from_id(1).junior_number)
