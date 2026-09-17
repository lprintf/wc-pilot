import tempfile
import unittest
from pathlib import Path

from wechat_bot.graph.graph import build_graph
from wechat_bot.graph.knowledge import KnowledgeIndex

_ROOT = Path(__file__).resolve().parents[1]


class KnowledgeIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db_dir = tempfile.TemporaryDirectory()
        cls._db_path = Path(cls._db_dir.name) / "test_kb.db"
        cls._index = KnowledgeIndex(cls._db_path, knowledge_dir=_ROOT / "knowledge")
        cls._chunk_count = cls._index.reindex()

    @classmethod
    def tearDownClass(cls):
        cls._index.close()
        cls._db_dir.cleanup()

    def test_index_reads_all_md_files(self):
        self.assertGreater(self._chunk_count, 0)

    def test_search_capabilities_finds_lead_gen(self):
        results = self._index.search("获客引流客服", top_n=5)
        labels = [c.source_label for c in results]
        self.assertTrue(
            any("获客引流" in label for label in labels),
            f"expected lead gen in {labels}",
        )

    def test_search_after_sales_finds_return(self):
        results = self._index.search("退货退款售后怎么处理", top_n=5)
        texts = [c.content for c in results]
        self.assertTrue(
            any("退换" in t for t in texts),
            f"expected return/refund in {[c.source_label for c in results]}",
        )

    def test_search_cost_finds_pricing(self):
        results = self._index.search("成本估算", top_n=5)
        labels = [c.source_label for c in results]
        self.assertTrue(
            any("pricing.md" in label for label in labels),
            f"expected pricing in {labels}",
        )

    def test_search_faq_finds_personal_center(self):
        results = self._index.search("怎么查看记录", top_n=5)
        texts = [c.content for c in results]
        self.assertTrue(
            any("个人中心" in t for t in texts),
            f"expected personal center in {[c.source_label for c in results]}",
        )

    def test_search_no_match_returns_empty(self):
        results = self._index.search("xyzzy_nonexistent_23r8j3", top_n=5)
        self.assertEqual(len(results), 0)

    def test_reindex_is_idempotent(self):
        count1 = self._index.reindex()
        count2 = self._index.reindex()
        self.assertEqual(count1, count2)


class GraphKnowledgeFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db_dir = tempfile.TemporaryDirectory()
        cls._db_path = Path(cls._db_dir.name) / "test_graph_kb.db"
        cls._index = KnowledgeIndex(cls._db_path, knowledge_dir=_ROOT / "knowledge")
        cls._index.reindex()
        cls._compiled = build_graph(knowledge_index=cls._index)

    @classmethod
    def tearDownClass(cls):
        cls._index.close()
        cls._db_dir.cleanup()

    def test_knowledge_qa_returns_source_label(self):
        import asyncio
        result = asyncio.run(self._compiled.ainvoke(
            {
                "intent": "knowledge_qa",
                "incoming_messages": [
                    {"role": "user", "content": "获客引流客服能做到什么"},
                ],
            }
        ))
        reply = result["reply_text"]
        self.assertIn("知识库", reply)
        self.assertIn("capabilities.md", reply)
        self.assertIn("获客引流", reply)

    def test_knowledge_qa_no_match_gives_fallback(self):
        import asyncio
        result = asyncio.run(self._compiled.ainvoke(
            {
                "intent": "knowledge_qa",
                "incoming_messages": [
                    {"role": "user", "content": "xyzzy_nonexistent_topic"},
                ],
            }
        ))
        self.assertIn("知识库", result["reply_text"])
        self.assertIn("找到", result["reply_text"] or "")


if __name__ == "__main__":
    unittest.main()
