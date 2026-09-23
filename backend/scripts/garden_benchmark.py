"""Benchmark the dedicated digital garden via GardenKnowledgeSource.

Usage:
    uv run python backend/scripts/garden_benchmark.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GARDEN_RAW = ROOT / "backend" / "garden" / "raw" / "zh"
OUT_DIR = ROOT / "docs" / "specs" / "260921"
sys.path.insert(0, str(ROOT / "backend"))

QUERIES = [
    ("产品介绍", ["产品介绍", "系统能力", "能做什么"]),
    ("获客引流", ["获客引流", "线索", "微信客服"]),
    ("电商售后", ["电商", "售后", "退换货", "物流"]),
    ("教育行业", ["教育", "培训", "获客"]),
    ("FDE定义", ["FDE", "定义", "AI落地"]),
    ("AI落地难点", ["AI落地", "难点", "流程", "数据"]),
    ("AI落地成本", ["成本", "LLM", "开发", "运维"]),
    ("人月神话", ["人月神话", "软件工程"]),
    ("AI对就业影响", ["AI", "就业", "分配", "劳动市场"]),
    ("个人信息合规", ["个人信息", "合规", "数据"]),
    ("知识库问答", ["知识库", "问答", "文档", "检索"]),
    ("数字花园维护", ["Synapse", "数字花园", "内容维护"]),
]


# ---------------------------------------------------------------------------
# Dedicated garden benchmark via GardenKnowledgeSource
# ---------------------------------------------------------------------------
def new_search(terms: list[str]) -> list[dict]:
    """Run all queries against the production GardenKnowledgeSource."""
    from wechat_bot.garden import GardenKnowledgeSource

    garden = GardenKnowledgeSource(GARDEN_RAW)
    results: list[dict] = []
    seen: set[str] = set()
    for query in terms:
        for hit in garden.search(query, top_n=5):
            if hit.slug not in seen:
                seen.add(hit.slug)
                results.append({
                    "slug": hit.slug,
                    "title": hit.title,
                    "score": hit.score,
                    "backlinks": hit.backlink_count,
                })
    return results[:8]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for name, terms in QUERIES:
        hits = new_search(terms)
        report.append({"case": name, "terms": terms, "hits": hits})
        print(f"done: {name}")

    json_path = OUT_DIR / "garden-benchmark.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    lines = []
    for row in report:
        lines.append(f"--- {row['case']} (terms: {', '.join(row['terms'])}) ---")
        if not row["hits"]:
            lines.append("  (none)")
        for item in row["hits"]:
            lines.append(
                f"  {item['slug']} :: {item.get('title', '')} :: score={item.get('score', 0)} back={item.get('backlinks', 0)}"
            )
        lines.append("")

    txt_path = OUT_DIR / "garden-benchmark.txt"
    txt_path.write_text("\n".join(lines), "utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")


if __name__ == "__main__":
    main()
