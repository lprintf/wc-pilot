import tempfile
import unittest
from pathlib import Path

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
        results = self._index.search("\u83b7\u5ba2\u5f15\u6d41\u5ba2\u670d", top_n=5)
        labels = [c.source_label for c in results]
        self.assertTrue(
            any("\u83b7\u5ba2\u5f15\u6d41" in label for label in labels),
            f"expected lead gen in {labels}",
        )

    def test_search_after_sales_finds_return(self):
        results = self._index.search("\u9000\u8d27\u9000\u6b3e\u552e\u540e\u600e\u4e48\u5904\u7406", top_n=5)
        texts = [c.content for c in results]
        self.assertTrue(
            any("\u9000\u6362\u8d27" in t for t in texts),
            f"expected return/refund in {[c.source_label for c in results]}",
        )

    def test_search_cost_finds_pricing(self):
        results = self._index.search("\u6210\u672c\u65b9\u6848", top_n=5)
        labels = [c.source_label for c in results]
        self.assertTrue(
            any("pricing.md" in label for label in labels),
            f"expected pricing in {labels}",
        )

    def test_search_faq_finds_personal_center(self):
        results = self._index.search("\u600e\u4e48\u67e5\u770b\u8bb0\u5f55", top_n=5)
        texts = [c.content for c in results]
        self.assertTrue(
            any("\u4e2a\u4eba\u4e2d\u5fc3" in t for t in texts),
            f"expected personal center in {[c.source_label for c in results]}",
        )

    def test_search_no_match_returns_empty(self):
        results = self._index.search("xyzzy_nonexistent_23r8j3", top_n=5)
        self.assertEqual(len(results), 0)

    def test_reindex_is_idempotent(self):
        count1 = self._index.reindex()
        count2 = self._index.reindex()
        self.assertEqual(count1, count2)


if __name__ == "__main__":
    unittest.main()
