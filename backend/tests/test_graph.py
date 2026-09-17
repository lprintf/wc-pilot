import unittest

import asyncio

from wechat_bot.graph.graph import COMPILED_GRAPH
from wechat_bot.graph.intents import Intent, is_profile_command, merge_question_text


class ProfileCommandTests(unittest.TestCase):
    def test_profile_keywords_are_deterministic(self):
        self.assertTrue(is_profile_command("个人中心"))
        self.assertTrue(is_profile_command("我的信息"))
        self.assertTrue(is_profile_command("我的消息"))
        

    def test_non_command_text_is_not_profile(self):
        self.assertFalse(is_profile_command("获客引流"))
        self.assertFalse(is_profile_command("售后"))
        self.assertFalse(is_profile_command("随便聊聊"))
        self.assertFalse(is_profile_command(""))


class MergeQuestionTextTests(unittest.TestCase):
    def test_merges_only_user_messages(self):
        messages = [
            {"role": "user", "content": "第一个问题"},
            {"role": "assistant", "content": "不应被合并"},
            {"role": "user", "content": "第二个问题"},
        ]
        self.assertEqual(
            merge_question_text(messages),
            "第一个问题\n\n第二个问题",
        )


class GraphSkeletonTests(unittest.TestCase):
    def test_profile_command_routes_to_profile_intent(self):
        result = asyncio.run(COMPILED_GRAPH.ainvoke(
            {"incoming_messages": [{"role": "user", "content": "个人中心"}]},
        ))
        self.assertEqual(result["intent"], Intent.PROFILE)
        self.assertEqual(result["intent"], "profile")
        self.assertTrue(result["reply_text"])

    def test_unknown_intent_uses_other(self):
        result = asyncio.run(COMPILED_GRAPH.ainvoke(
            {"incoming_messages": [{"role": "user", "content": "你们能做什么？"}]},
        ))
        self.assertEqual(result["intent"], Intent.OTHER)
        self.assertEqual(result["intent"], "other")
        self.assertTrue(result["reply_text"])


if __name__ == "__main__":
    unittest.main()
