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

    def test_in_memory_history_lists_recent_interactions(self):
        repo = InMemoryChatHistoryRepository()
        repo.save_message("alice", "session-1", ChatMessage(role="user", content="What is policy?"))
        repo.save_message(
            "alice",
            "session-1",
            ChatMessage(
                role="assistant",
                content="Policy answer",
                metadata={"tools_used": ["rag_search"], "trace_id": "trace-1"},
            ),
        )

        interactions = repo.list_recent_interactions()

        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0].user_id, "alice")
        self.assertEqual(interactions[0].question, "What is policy?")
        self.assertEqual(interactions[0].answer, "Policy answer")
        self.assertEqual(interactions[0].metadata["tools_used"], ["rag_search"])

    def test_history_context_is_bounded(self):
        messages = [ChatMessage(role="user", content=f"message {index}") for index in range(20)]
        context = build_history_context(messages, max_chars=80)
        self.assertIn("older messages omitted", context)
        self.assertIn("message 19", context)


if __name__ == "__main__":
    unittest.main()
