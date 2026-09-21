import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD_KB = ROOT / "backend" / "knowledge"
GARDEN_STATIC = Path(r"C:\Users\lprintf\ai-space\blog\nginx\static")
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

def old_search(query_terms):
    sys.path.insert(0, str(ROOT / "backend"))
    from wechat_bot.graph.knowledge import KnowledgeIndex
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    idx = KnowledgeIndex(Path(db.name), knowledge_dir=OLD_KB)
    try:
        idx.reindex()
        results = []
        for terms in query_terms:
            for hit in idx.search(terms, top_n=5):
                results.append({
                    "path": hit.document_path,
                    "heading": hit.heading,
                    "tags": list(hit.tags),
                })
        # unique by path+heading
        seen = set()
        unique = []
        for item in results:
            key = (item["path"], item["heading"])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique[:8]
    finally:
        idx.close()
        Path(db.name).unlink(missing_ok=True)

def load_garden():
    index = json.loads((GARDEN_STATIC / "search_index_zh.json").read_text("utf-8"))
    catalog_list = json.loads((GARDEN_STATIC / "catalog_zh.json").read_text("utf-8"))
    kg = json.loads((GARDEN_STATIC / "knowledge-graph_zh.json").read_text("utf-8"))
    catalog = {item["slug"]: item["title"] for item in catalog_list}
    node_by_id = {n["id"]: n for n in kg["nodes"]}
    backlinks = kg["backlinks"]
    return index, catalog, node_by_id, backlinks

def new_search(query_terms, garden):
    index, catalog, node_by_id, backlinks = garden
    slug_dict = index["slug_dict"]
    score = {}
    for term in query_terms:
        term_l = term.lower()
        found = set()
        for key, ids in index["index"].items():
            key_l = key.lower()
            if term_l == key_l or term_l in key_l or key_l in term_l:
                for i in ids:
                    if i < len(slug_dict):
                        found.add(slug_dict[i])
        for slug in found:
            score[slug] = score.get(slug, 0) + 1
    ranked = sorted(score.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return [
        {
            "slug": slug,
            "title": catalog.get(slug, ""),
            "group": node_by_id.get(slug, {}).get("group", ""),
            "score": s,
            "backlinks": len(backlinks.get(slug, [])),
        }
        for slug, s in ranked
    ]

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    garden = load_garden()
    report = []
    for name, terms in QUERIES:
        old = old_search(terms)
        new = new_search(terms, garden)
        report.append({"case": name, "terms": terms, "old": old, "new": new})
        print(f"done: {name}")
    out = OUT_DIR / "gap-results.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"wrote {out}")

if __name__ == "__main__":
    main()