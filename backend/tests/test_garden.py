import unittest
from pathlib import Path

from wechat_bot.garden import GardenKnowledgeSource

ROOT = Path(__file__).resolve().parents[2]


class GardenKnowledgeSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.garden = GardenKnowledgeSource()

    def test_node_count(self):
        self.assertGreaterEqual(self.garden.node_count, 30)

    def test_search_product(self):
        hits = self.garden.search("产品介绍 系统能力 能做什么", top_n=3)
        self.assertTrue(any("overview" in h.slug for h in hits))

    def test_search_fde(self):
        hits = self.garden.search("FDE 定义 AI落地", top_n=3)
        self.assertTrue(any("fde" in h.slug for h in hits))

    def test_search_compliance(self):
        hits = self.garden.search("个人信息 合规 数据", top_n=3)
        self.assertTrue(any("compliance" in h.slug for h in hits))

    def test_read_note_normalizes_links(self):
        body = self.garden.read_note("product/overview")
        self.assertIsNotNone(body)
        assert body is not None
        self.assertNotIn("[[lead-gen", body)
        self.assertIn("获客引流客服", body)

    def test_tags_filter(self):
        hits = self.garden.search("售后 退换货", tags=["电商"], top_n=3)
        self.assertTrue(any("ecommerce" in h.slug for h in hits))


if __name__ == "__main__":
    unittest.main()