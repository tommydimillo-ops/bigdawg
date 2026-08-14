import unittest

from agent.executor import _bounded_model_history


class TestBoundedModelHistory(unittest.TestCase):

    def test_recent_messages_are_retained(self):
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
            for i in range(11)
        ]
        result = _bounded_model_history(history, limit=4)
        self.assertEqual([item["content"] for item in result], ["8", "9", "10"])
        self.assertEqual(result[0]["role"], "user")

    def test_input_is_not_mutated(self):
        history = [{"role": "user", "content": str(i)} for i in range(5)]
        _bounded_model_history(history, limit=2)
        self.assertEqual(len(history), 5)

    def test_non_positive_limit_disables_truncation(self):
        history = [{"role": "user", "content": str(i)} for i in range(5)]
        self.assertEqual(_bounded_model_history(history, limit=0), history)


if __name__ == "__main__":
    unittest.main()
