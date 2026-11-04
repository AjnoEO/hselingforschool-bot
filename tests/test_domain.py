from itertools import product
import random

from enums import BlockType, QueueStatus, Platform
from olymp import Olymp
from problem import Problem, ProblemBlock
from users import User, Examiner, Participant
from tests import DatabaseTestCase


class ParticipantQueueFlowTest(DatabaseTestCase):
    def test_queue_flow(self):
        olymp = Olymp.create("Тест")
        participant = Participant.create_as_new_user(
            "participant", "Иван", "Иванов", 8, olymp.id
        )
        problems = [
            Problem.create(olymp.id, f"Задача {number}")
            for number in range(1, 4)
        ]
        ProblemBlock.create(olymp.id, problems, BlockType.JUNIOR_1)

        self.assertEqual(participant.problem_from_number(1), problems[0])

        queue_entry = participant.join_queue(problems[0])
        self.assertEqual(queue_entry.status, QueueStatus.WAITING)
        self.assertIsNone(queue_entry.examiner_id)

        examiner = Examiner.create_as_new_user(
            "examiner", "Пётр", "Петров", "https://example.com", olymp.id,
            problems=[problems[0].id], is_busy=False
        )
        found_entry = examiner.look_for_queue_entry()
        self.assertEqual(found_entry.id, queue_entry.id)

        examiner.assign_to_queue_entry(found_entry)
        self.assertEqual(found_entry.status, QueueStatus.DISCUSSING)
        self.assertEqual(found_entry.examiner_id, examiner.id)
        self.assertTrue(examiner.is_busy)

        found_entry.status = QueueStatus.SUCCESS
        self.assertTrue(participant.solved(problems[0]))
        self.assertEqual(participant.attempts_left(problems[0]), 3)

        total, results = participant.results()
        self.assertGreater(total, 0)
        self.assertEqual(results[0][:2], (problems[0], True))


class ParticipantListTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        grades = list(range(8, 12))
        self.olymp = Olymp.create("Тест")
        user_list: list[User] = []
        for i, _ in enumerate(product(grades, Platform)):
            user_list.append(User.create(f"user{i}", f"Имя{i}", f"Фамилия{i}"))
        self.participants_list: list[Participant] = []
        random.seed(239)
        random.shuffle(user_list)
        for user, (grade, platform) in zip(user_list, product(grades, Platform)):
            user.platform = platform
            participant = Participant.create_for_existing_user(user, grade, self.olymp.id)
            if random.randint(0, 1):
                participant.finished = True
            participant.last_block_number = random.randint(1, 3)
            self.participants_list.append(participant)
        self.participants_list.sort(key=lambda p: p.user_id)

    def test_participants_no_filters(self):
        self.assertListEqual(self.olymp.get_participants(), self.participants_list)
        self.assertListEqual(self.olymp.get_participants(start=3, limit=3), self.participants_list[3:6])
        self.assertListEqual(self.olymp.get_participants(sort=True), sorted(self.participants_list, key=lambda p: p.id))

    def test_participants_single_filter(self):
        self.assertListEqual(
            self.olymp.get_participants(finished=True),
            [p for p in self.participants_list if p.finished],
        )
        self.assertListEqual(
            self.olymp.get_participants(junior=True),
            [p for p in self.participants_list if p.grade < 10]
        )
        self.assertListEqual(
            self.olymp.get_participants(platform=Platform.TELEGRAM),
            [p for p in self.participants_list if p.platform == Platform.TELEGRAM]
        )
        self.assertListEqual(
            self.olymp.get_participants(last_block_number=2),
            [p for p in self.participants_list if p.last_block_number == 2]
        )

    def test_participants_multi_filter(self):
        self.assertListEqual(
            self.olymp.get_participants(finished=True, junior=False),
            [p for p in self.participants_list if p.finished and p.grade >= 10],
        )
        self.assertListEqual(
            self.olymp.get_participants(junior=True, platform=Platform.VK),
            [p for p in self.participants_list if p.grade < 10 and p.platform == Platform.VK]
        )
        self.assertListEqual(
            self.olymp.get_participants(platform=Platform.TELEGRAM, last_block_number=1),
            [p for p in self.participants_list if p.platform == Platform.TELEGRAM and p.last_block_number == 1]
        )
        self.assertListEqual(
            self.olymp.get_participants(last_block_number=3, finished=False),
            [p for p in self.participants_list if p.last_block_number == 3 and not p.finished]
        )
        self.assertListEqual(
            self.olymp.get_participants(platform=Platform.VK, junior=False, last_block_number=2, finished=False),
            [
                p for p in self.participants_list
                if p.platform == Platform.VK and p.grade >= 10 and p.last_block_number == 2 and not p.finished
            ]
        )


class ExaminerListTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        examiner_nos = list(range(5))
        self.olymp = Olymp.create("Тест")
        user_list: list[User] = []
        for i, _ in enumerate(product(examiner_nos, Platform)):
            user_list.append(User.create(f"user{i}", f"Имя{i}", f"Фамилия{i}"))
        self.examiners_list: list[Examiner] = []
        random.seed(239)
        random.shuffle(user_list)
        for user, (no, platform) in zip(user_list, product(examiner_nos, Platform)):
            user.platform = platform
            examiner = Examiner.create_for_existing_user(user, f"link/{platform.code}{no}", self.olymp.id)
            if random.randint(0, 1):
                examiner.is_busy = False
            examiner.busyness_level = random.randint(0, 5)
            self.examiners_list.append(examiner)
        self.examiners_list.sort(key=lambda e: e.user_id)

    def test_examiners_no_filters(self):
        self.assertListEqual(self.olymp.get_examiners(), self.examiners_list)
        self.assertListEqual(self.olymp.get_examiners(start=3, limit=3), self.examiners_list[3:6])
        self.assertListEqual(self.olymp.get_examiners(sort=True), sorted(self.examiners_list, key=lambda e: e.id))
        self.assertListEqual(
            self.olymp.get_examiners(order_by_busyness=True), 
            sorted(self.examiners_list, key=lambda e: e.busyness_level)
        )

    def test_examiners_single_filter(self):
        self.assertListEqual(
            self.olymp.get_examiners(only_free=True),
            [e for e in self.examiners_list if not e.is_busy]
        )
        self.assertListEqual(
            self.olymp.get_examiners(platform=Platform.TELEGRAM),
            [e for e in self.examiners_list if e.platform == Platform.TELEGRAM]
        )

    def test_examiners_multi_filter(self):
        self.assertListEqual(
            self.olymp.get_examiners(only_free=True, platform=Platform.VK),
            [e for e in self.examiners_list if not e.is_busy and e.platform == Platform.VK]
        )


class EmptyDatabaseTest(DatabaseTestCase):
    def test_database_is_empty(self):
        self.assertEqual(Olymp.list_all(), [])
