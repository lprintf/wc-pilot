"""Markdown knowledge base indexer and keyword retriever.

Scans ``backend/knowledge/*.md``, chunks by H1/H2 headings, stores in
SQLite and provides keyword-based top-N retrieval.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    document_path: str
    title: str
    heading: str
    content: str
    ordinal: int

    @property
    def source_label(self) -> str:
        """Human-readable source citation."""
        path = self.document_path.replace("\\", "/")
        if self.heading:
            return f"{path} - {self.heading}"
        return path


class KnowledgeIndex:
    """SQLite-backed keyword index with FTS acceleration."""

    def __init__(self, db_path: Path, *, knowledge_dir: Path | None = None) -> None:
        import sqlite3
        import threading

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        root = Path(__file__).resolve().parents[2]
        self._knowledge_dir = knowledge_dir or (root / "knowledge")
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_document (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_chunk (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES knowledge_document(id) ON DELETE CASCADE,
                    heading TEXT,
                    content TEXT NOT NULL,
                    ordinal INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_doc
                    ON knowledge_chunk(document_id);
                """
            )
            try:
                self._connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunk_fts USING fts5(content)"
                )
            except Exception:
                pass

    def reindex(self) -> int:
        """Rescan all .md files and return chunk count."""
        import time
        import sqlite3

        files = sorted(self._knowledge_dir.glob("*.md"))
        total = 0
        with self._lock, self._connection:
            for file_path in files:
                rel = str(file_path.relative_to(self._knowledge_dir)).replace("\\", "/")
                text = file_path.read_text("utf-8")
                content_hash = hashlib.sha256(text.encode()).hexdigest()

                existing = self._connection.execute(
                    "SELECT id, content_hash FROM knowledge_document WHERE path=?",
                    (rel,),
                ).fetchone()
                if existing and existing["content_hash"] == content_hash:
                    total += self._connection.execute(
                        "SELECT COUNT(*) FROM knowledge_chunk WHERE document_id=?",
                        (existing["id"],),
                    ).fetchone()[0]
                    continue

                title = ""
                match = re.search(r"^# (.+)$", text, re.MULTILINE)
                if match:
                    title = match.group(1).strip()

                now = int(time.time())
                if existing:
                    self._connection.execute(
                        "DELETE FROM knowledge_document WHERE id=?",
                        (existing["id"],),
                    )

                cur = self._connection.execute(
                    "INSERT INTO knowledge_document(path,title,content_hash,updated_at) VALUES(?,?,?,?)",
                    (rel, title, content_hash, now),
                )
                doc_id = cur.lastrowid

                chunks = _split_markdown(title, text)
                for i, (heading, content) in enumerate(chunks):
                    self._connection.execute(
                        "INSERT INTO knowledge_chunk(document_id,heading,content,ordinal) VALUES(?,?,?,?)",
                        (doc_id, heading or None, content, i),
                    )
                total += len(chunks)

            deleted = self._connection.execute(
                "DELETE FROM knowledge_document WHERE path NOT IN ({})".format(
                    ",".join("?" for _ in files)
                ),
                [str(f.relative_to(self._knowledge_dir)).replace("\\", "/") for f in files],
            ).rowcount
            if deleted:
                pass  # CASCADE cleans chunks

            # Rebuild FTS
            try:
                self._connection.execute("DELETE FROM knowledge_chunk_fts")
                self._connection.execute(
                    "INSERT INTO knowledge_chunk_fts(content) SELECT content FROM knowledge_chunk"
                )
            except Exception:
                pass

        return total

    def search(self, query: str, top_n: int = 5) -> list[KnowledgeChunk]:
        """Keyword-overlap retrieval; returns top-N chunks."""
        tokens = _tokenize(query)
        if not tokens:
            return []

        with self._lock:
            rows = self._connection.execute(
                """SELECT d.path, d.title, c.heading, c.content, c.ordinal
                   FROM knowledge_chunk c
                   JOIN knowledge_document d ON d.id = c.document_id"""
            ).fetchall()

        scored: list[tuple[int, KnowledgeChunk]] = []
        for row in rows:
            chunk = KnowledgeChunk(
                document_path=row["path"],
                title=row["title"],
                heading=row["heading"] or "",
                content=row["content"],
                ordinal=row["ordinal"],
            )
            score = _keyword_score(tokens, chunk.content)
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [chunk for _, chunk in scored[:top_n]]

    def close(self) -> None:
        self._connection.close()


def _split_markdown(title: str, text: str) -> list[tuple[str, str]]:
    """Split Markdown by H2 headings, propagating the document title."""
    sections = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    chunks: list[tuple[str, str]] = []
    # sections[0] is everything before first H2
    preamble = sections[0].strip()
    if preamble:
        chunks.append((title, preamble))
    # sections[1] = heading, sections[2] = body, ...
    for i in range(1, len(sections) - 1, 2):
        heading = sections[i].strip()
        body = sections[i + 1].strip()
        if body:
            chunks.append((heading, body))
    return chunks


def _tokenize(text: str) -> list[str]:
    """Simple CJK + ASCII tokenizer with punctuation removal."""
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", " ", text.lower())
    tokens: list[str] = []
    for word in text.split():
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            tokens.extend(list(word))
        else:
            tokens.append(word)
    return tokens


def _keyword_score(tokens: list[str], content: str) -> int:
    """Count token occurrences in content."""
    content_lower = content.lower()
    score = 0
    for token in tokens:
        pos = 0
        while True:
            pos = content_lower.find(token, pos)
            if pos < 0:
                break
            score += 1
            pos += len(token)
    return score

