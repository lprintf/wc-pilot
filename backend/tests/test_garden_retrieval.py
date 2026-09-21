"""Regression tests: garden retrieval must hit expected slugs for each case.

Run with:
    uv run python -m unittest discover -s backend/tests -p "test_garden_retrieval.py" -v
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "backend/garden/fixtures/cases.json"
GARDEN_RAW = ROOT / "backend/garden/raw/zh"

def _load_cases():
    return json.loads(CASES_PATH.read_text("utf-8"))

def _load_garden():
    nodes = {}
    tags = {}
    outgoing = {}
    backlinks = {}
    basename_to_slug = {}

    for main_file in sorted(GARDEN_RAW.rglob("main.md")):
        rel = main_file.parent.relative_to(GARDEN_RAW)
        slug = rel.as_posix()
        text = main_file.read_text("utf-8")
        m = re.search(r"^# (.+)$", text, re.MULTILINE)
        nodes[slug] = m.group(1).strip() if m else ""
        basename_to_slug[slug.rsplit("/", 1)[-1]] = slug

        tag_file = main_file.parent / "tags.txt"
        if tag_file.exists():
            tl = []
            for line in tag_file.read_text("utf-8").splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                if k.strip() in ("human", "ai"):
                    tl.extend(p.strip() for p in v.split(",") if p.strip())
            tags[slug] = tl

    for main_file in sorted(GARDEN_RAW.rglob("main.md")):
        rel = main_file.parent.relative_to(GARDEN_RAW)
        slug = rel.as_posix()
        text = main_file.read_text("utf-8")
        target_slugs = []
        for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
            left = raw.split("|", 1)[0].strip()
            resolved = basename_to_slug.get(left)
            if not resolved and left.startswith(("product/", "fde/", "compliance/", "ai/", "software-engineering/")):
                resolved = left
            if resolved and resolved not in target_slugs:
                target_slugs.append(resolved)
        outgoing[slug] = target_slugs
        for target in target_slugs:
            backlinks.setdefault(target, []).append(slug)

    return nodes, tags, outgoing, backlinks

def _search(terms, garden):
    nodes, tags, outgoing, backlinks = garden
    index = {}
    for slug, tl in tags.items():
        for tag in tl:
            index.setdefault(tag, []).append(slug)

    scores = {}
    for term in terms:
        tl = term.lower()
        for key, slugs in index.items():
            kl = key.lower()
            if tl == kl or tl in kl or kl in tl:
                for slug in slugs:
                    scores[slug] = scores.get(slug, 0) + 1

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
    return [{"slug": s, "score": sc} for s, sc in ranked]


class TestGardenRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = _load_cases()
        cls.garden = _load_garden()

    def _assert_case(self, index):
        case = self.cases[index]
        hits = _search(case["terms"], self.garden)
        top_slugs = [h["slug"] for h in hits[:3]]
        expected = case["expected_hit"]
        if expected:
            matched = [s for s in expected if s in top_slugs]
            self.assertTrue(
                matched,
                f"\nCase: {case['case']}\nTerms: {case['terms']}\nExpected any of: {expected}\nGot top-3: {top_slugs}",
            )
        else:
            self.assertFalse(
                top_slugs,
                f"\nCase: {case['case']} should not surface customer knowledge. Got top-3: {top_slugs}",
            )

    def test_0_product(self):
        self._assert_case(0)

    def test_1_lead_gen(self):
        self._assert_case(1)

    def test_2_ecommerce(self):
        self._assert_case(2)

    def test_3_education(self):
        self._assert_case(3)

    def test_4_fde(self):
        self._assert_case(4)

    def test_5_ai_landing_difficulty(self):
        self._assert_case(5)

    def test_6_cost(self):
        self._assert_case(6)

    def test_7_human_month(self):
        self._assert_case(7)

    def test_8_ai_employment(self):
        self._assert_case(8)

    def test_9_compliance(self):
        self._assert_case(9)

    def test_10_knowledge_qa(self):
        self._assert_case(10)

    def test_11_digital_garden_ops(self):
        self._assert_case(11)


if __name__ == "__main__":
    unittest.main()
