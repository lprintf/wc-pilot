"""Digital garden knowledge source.

Scans ``backend/garden/raw/zh`` for ``main.md`` + ``tags.txt`` files,
builds an in-memory inverted index and knowledge graph, and exposes
tag-aware search and full-note reading without document chunking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class GardenHit:
    slug: str
    title: str
    tags: tuple[str, ...] = ()
    outgoing: tuple[str, ...] = ()
    backlink_count: int = 0
    score: int = 0

    @property
    def source_label(self) -> str:
        return self.slug


@dataclass
class _Node:
    slug: str
    title: str
    path: Path
    tags: list[str] = field(default_factory=list)
    outgoing: list[str] = field(default_factory=list)
    backlinks: list[str] = field(default_factory=list)


class GardenKnowledgeSource:
    """In-memory tag-inverted-index + knowledge-graph for a Markdown digital garden."""

    def __init__(self, garden_root: Path | None = None) -> None:
        if garden_root is None:
            # garden data lives at <repo>/backend/garden/raw/zh
            # wechat_bot/ -> backend/ -> garden/raw/zh
            pkg_dir = Path(__file__).resolve().parent  # .../wechat_bot
            self._root = pkg_dir.parent / "garden" / "raw" / "zh"
        else:
            self._root = Path(garden_root)
        self._nodes: dict[str, _Node] = {}
        self._basename_to_slug: dict[str, str] = {}
        self._inverted_index: dict[str, list[str]] = {}
        self._reload()

    def _reload(self) -> None:
        self._nodes.clear()
        self._basename_to_slug.clear()
        self._inverted_index.clear()

        # Pass 1: titles, tags
        for main_file in sorted(self._root.rglob("main.md")):
            rel = main_file.parent.relative_to(self._root)
            slug = rel.as_posix()
            text = main_file.read_text("utf-8")
            title = ""
            match = re.search(r"^# (.+)$", text, re.MULTILINE)
            if match:
                title = match.group(1).strip()
            self._nodes[slug] = _Node(slug=slug, title=title, path=main_file.parent)
            base = slug.rsplit("/", 1)[-1]
            self._basename_to_slug[base] = slug

            tag_file = main_file.parent / "tags.txt"
            if tag_file.exists():
                for line in tag_file.read_text("utf-8").splitlines():
                    line = line.strip()
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    if key.strip() not in ("human", "ai"):
                        continue
                    for part in value.split(","):
                        t = part.strip()
                        if t:
                            self._nodes[slug].tags.append(t)
                            self._inverted_index.setdefault(t, []).append(slug)

        # Pass 2: links
        for slug, node in self._nodes.items():
            text = node.path.joinpath("main.md").read_text("utf-8")
            # strip code blocks
            text = re.sub(r"```[\s\S]*?```", "", text)
            text = re.sub(r"`[^`]+`", "", text)
            for raw in re.findall(r"\[\[([^\]]+)\]\]", text):
                left = raw.split("|", 1)[0].strip()
                resolved = self._basename_to_slug.get(left)
                if not resolved:
                    # try full slug match
                    if left in self._nodes:
                        resolved = left
                if resolved and resolved != slug and resolved not in node.outgoing:
                    node.outgoing.append(resolved)

        # Pass 3: backlinks
        for slug, node in self._nodes.items():
            for target in node.outgoing:
                if target in self._nodes:
                    self._nodes[target].backlinks.append(slug)

    @property
    def node_count(self) -> int:
        return len(self._nodes)


    def search(
        self,
        query: str,
        top_n: int = 5,
        tags: Sequence[str] | None = None,
    ) -> list[GardenHit]:
        """Tag-inverted-index keyword search.

        ``query`` is space-separated keywords.
        ``tags`` filters results to nodes that contain any of the given tags.
        Returns hits ranked by keyword match count, with metadata only (no body).
        """
        terms = [t.strip().lower() for t in query.split() if t.strip()]
        if not terms:
            return []

        scores: dict[str, int] = {}
        for term in terms:
            for key, slug_list in self._inverted_index.items():
                key_l = key.lower()
                if term == key_l or term in key_l or key_l in term:
                    for slug in slug_list:
                        scores[slug] = scores.get(slug, 0) + 1

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        results: list[GardenHit] = []
        tag_set = set(tags) if tags else None
        for slug, score in ranked:
            node = self._nodes.get(slug)
            if node is None:
                continue
            if tag_set and not tag_set.intersection(node.tags):
                continue
            outgoing = tuple(t for t in node.outgoing if t in self._nodes)[:10]
            results.append(
                GardenHit(
                    slug=slug,
                    title=node.title,
                    tags=tuple(node.tags),
                    outgoing=outgoing,
                    backlink_count=len(node.backlinks),
                    score=score,
                )
            )
            if len(results) >= top_n:
                break

        return results

    def read_note(self, slug: str) -> str | None:
        """Return full note body with resolved ``[[links]]``."""
        node = self._nodes.get(slug)
        if node is None:
            return None
        text = node.path.joinpath("main.md").read_text("utf-8")
        return self._normalize_links(text, slug)

    def _normalize_links(self, text: str, source_slug: str) -> str:
        def _replacer(m: re.Match[str]) -> str:
            raw = m.group(1)
            if "|" in raw:
                left, display = raw.split("|", 1)
                return display.strip()
            else:
                resolved = self._basename_to_slug.get(raw.strip())
                if resolved and resolved in self._nodes:
                    return self._nodes[resolved].title
                return raw
        return re.sub(r"\[\[([^\]]+)\]\]", _replacer, text)

    def list_slugs(self) -> list[str]:
        return sorted(self._nodes.keys())