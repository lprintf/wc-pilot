import unittest


from wechat_bot.graph.intents import Intent, is_profile_command, merge_question_text


class ProfileCommandTests(unittest.TestCase):
    def test_profile_keywords_are_deterministic(self):
        self.assertTrue(is_profile_command("\u4e2a\u4eba\u4e2d\u5fc3"))
        self.assertTrue(is_profile_command("\u6211\u7684\u4fe1\u606f"))
        self.assertTrue(is_profile_command("\u6211\u7684\u6d88\u606f"))


    def test_non_command_text_is_not_profile(self):
        self.assertFalse(is_profile_command("\u83b7\u5ba2\u5f15\u6d41"))
        self.assertFalse(is_profile_command("\u552e\u540e"))
        self.assertFalse(is_profile_command("\u968f\u4fbf\u804a\u804a"))
        self.assertFalse(is_profile_command(""))


class MergeQuestionTextTests(unittest.TestCase):
    def test_merges_only_user_messages(self):
        messages = [
            {"role": "user", "content": "\u7b2c\u4e00\u4e2a\u95ee\u9898"},
            {"role": "assistant", "content": "\u4e0d\u5e94\u88ab\u5408\u5e76"},
            {"role": "user", "content": "\u7b2c\u4e8c\u4e2a\u95ee\u9898"},
        ]
        self.assertEqual(
            merge_question_text(messages),
            "\u7b2c\u4e00\u4e2a\u95ee\u9898\n\n\u7b2c\u4e8c\u4e2a\u95ee\u9898",
        )


if __name__ == "__main__":
    unittest.main()
