import unittest

from backend.app.history import ChatMessage, InMemoryChatHistoryRepository, build_history_context


class HistoryTests(unittest.TestCase):
    def test_in_memory_history_persists_messages(self):
        repo = InMemoryChatHistoryRepository()
        repo.save_message("user", "session", ChatMessage(role="user", content="Hello"))
        repo.save_message("user", "session", ChatMessage(role="assistant", content="Hi"))

        messages = repo.load_messages("user", "session")
        self.assertEqual([message.content for message in messages], ["Hello", "Hi"])
        self.assertEqual(repo.list_sessions("user")[0].session_id, "session")

    def test_history_context_is_bounded(self):
        messages = [ChatMessage(role="user", content=f"message {index}") for index in range(20)]
        context = build_history_context(messages, max_chars=80)
        self.assertIn("older messages omitted", context)
        self.assertIn("message 19", context)


if __name__ == "__main__":
    unittest.main()

