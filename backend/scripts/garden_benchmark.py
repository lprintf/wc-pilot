"""Benchmark old flat KnowledgeIndex vs the dedicated digital garden.

Usage:
    uv run python backend/scripts/garden_benchmark.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD_KB = ROOT / "backend" / "knowledge"
GARDEN_RAW = ROOT / "backend" / "garden" / "raw" / "zh"
OUT_DIR = ROOT / "docs" / "specs" / "260921"

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
# Old flat knowledge index
# ---------------------------------------------------------------------------
def old_search(terms: list[str]) -> list[dict]:
    sys.path.insert(0, str(ROOT / "backend"))
    from wechat_bot.graph.knowledge import KnowledgeIndex

    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "bench.db"
    idx = KnowledgeIndex(db_path, knowledge_dir=OLD_KB)
    try:
        idx.reindex()
        results: list[dict] = []
        for query in terms:
            for hit in idx.search(query, top_n=5):
                results.append({
                    "slug": hit.document_path,
                    "title": hit.title,
                    "heading": hit.heading,
                    "score": 0,
                    "backlinks": 0,
                })
        # de-duplicate by slug+heading
        seen = set()
        unique = []
        for item in results:
            key = (item["slug"], item["heading"])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique[:8]
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# Dedicated garden index prototype
# ---------------------------------------------------------------------------
def load_garden() -> tuple[dict, dict, dict, dict]:
    """Return (titles, tags, outgoing, backlinks) keyed by full slug."""
    nodes = {}
    tags = {}
    outgoing = {}
    backlinks = {}
    basename_to_slug = {}

    for main_file in sorted(GARDEN_RAW.rglob("main.md")):
        rel = main_file.parent.relative_to(GARDEN_RAW)
        slug = rel.as_posix()
        text = main_file.read_text("utf-8")

        title = ""
        match = re.search(r"^# (.+)$", text, re.MULTILINE)
        if match:
            title = match.group(1).strip()
        nodes[slug] = title
        basename_to_slug[slug.rsplit("/", 1)[-1]] = slug

        tag_file = main_file.parent / "tags.txt"
        if tag_file.exists():
            tag_list = []
            for line in tag_file.read_text("utf-8").splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                if key.strip() in ("human", "ai"):
                    tag_list.extend(
                        part.strip()
                        for part in value.split(",")
                        if part.strip()
                    )
            tags[slug] = tag_list

    # Resolve links after collecting all slugs
    for main_file in sorted(GARDEN_RAW.rglob("main.md")):
        rel = main_file.parent.relative_to(GARDEN_RAW)
        slug = rel.as_posix()
        text = main_file.read_text("utf-8")
        target_slugs = []
        for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
            left = raw.split("|", 1)[0].strip()
            resolved = basename_to_slug.get(left)
            if not resolved and left.startswith("product/"):
                resolved = left
            if resolved and resolved not in target_slugs:
                target_slugs.append(resolved)
        outgoing[slug] = target_slugs
        for target in target_slugs:
            backlinks.setdefault(target, []).append(slug)

    return nodes, tags, outgoing, backlinks


def new_search(terms: list[str], garden) -> list[dict]:
    nodes, tags, outgoing, backlinks = garden
    index: dict[str, list[str]] = {}
    for slug, tag_list in tags.items():
        for tag in tag_list:
            index.setdefault(tag, []).append(slug)

    scores: dict[str, int] = {}
    for term in terms:
        term_l = term.lower()
        for key, slug_list in index.items():
            key_l = key.lower()
            if term_l == key_l or term_l in key_l or key_l in term_l:
                for slug in slug_list:
                    scores[slug] = scores.get(slug, 0) + 1

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:5]
    return [
        {
            "slug": slug,
            "title": nodes.get(slug, ""),
            "heading": "",
            "score": score,
            "backlinks": len(backlinks.get(slug, [])),
        }
        for slug, score in ranked
    ]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    garden = load_garden()
    report = []
    for name, terms in QUERIES:
        old = old_search(terms)
        new = new_search(terms, garden)
        report.append({"case": name, "terms": terms, "old": old, "new": new})
        print(f"done: {name}")

    json_path = OUT_DIR / "garden-benchmark.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    lines = []
    for row in report:
        lines.append(f"--- {row['case']} (terms: {', '.join(row['terms'])}) ---")
        lines.append("OLD:")
        if not row["old"]:
            lines.append("  (none)")
        for item in row["old"][:5]:
            heading = item.get("heading") or ""
            lines.append(f"  {item['slug']} :: {heading} :: {item['title']}")
        lines.append("NEW:")
        if not row["new"]:
            lines.append("  (none)")
        for item in row["new"]:
            lines.append(
                f"  {item['slug']} :: {item['title']} :: score={item['score']} back={item['backlinks']}"
            )
        lines.append("")

    txt_path = OUT_DIR / "garden-benchmark.txt"
    txt_path.write_text("\n".join(lines), "utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")


if __name__ == "__main__":
    main()