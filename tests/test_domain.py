from enums import BlockType, QueueStatus
from olymp import Olymp
from problem import Problem, ProblemBlock
from users import Examiner, Participant
from tests import DatabaseTestCase


class ParticipantQueueFlowTest(DatabaseTestCase):
    def test_queue_flow(self):
        olymp = Olymp.create("Тестовая олимпиада")
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


class EmptyDatabaseTest(DatabaseTestCase):
    def test_database_is_empty(self):
        self.assertEqual(Olymp.list_all(), [])
