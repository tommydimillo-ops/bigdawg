import os
import tempfile
import threading
import unittest

import agent.conversation_store as conversation_store


class ConversationStoreTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.real_file = conversation_store.CONVERSATION_FILE
        conversation_store.CONVERSATION_FILE = os.path.join(
            self.temp_dir.name, "conversation.json"
        )

    def tearDown(self):
        conversation_store.CONVERSATION_FILE = self.real_file
        self.temp_dir.cleanup()

    def test_corrupt_or_non_list_storage_is_safe(self):
        with open(conversation_store.CONVERSATION_FILE, "w") as file:
            file.write("not json")
        self.assertEqual(conversation_store.load_conversation(), [])

    def test_concurrent_appends_do_not_lose_messages(self):
        threads = [
            threading.Thread(
                target=conversation_store.append_message,
                args=({"role": "user", "content": str(index)},),
            )
            for index in range(20)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        messages = conversation_store.load_conversation()
        self.assertEqual(len(messages), 20)
        self.assertEqual(
            {message["content"] for message in messages},
            {str(index) for index in range(20)},
        )


if __name__ == "__main__":
    unittest.main()
