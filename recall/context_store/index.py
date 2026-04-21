"""SQLite index with FTS5 and embedding storage."""

import sqlite3
import struct
from pathlib import Path
from typing import Optional

from .storage import Chunk, get_store_dir


def open_index(db_path: str | Path) -> sqlite3.Connection:
    """Open or create the SQLite index database."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection):
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            session_id TEXT NOT NULL,
            project_slug TEXT NOT NULL,
            chunk_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tool_name TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT '',
            chunk_file_path TEXT NOT NULL DEFAULT '',
            embedding BLOB
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_timestamp ON chunks(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_slug);
        CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(chunk_type);
    """)
    # FTS5 virtual table for full-text search
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                summary,
                tags,
                content,
                content=chunks,
                content_rowid=rowid,
                tokenize='porter unicode61'
            );
        """)
        # Triggers to keep FTS in sync
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, summary, tags, content)
                VALUES (new.rowid, new.summary, new.tags, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, summary, tags, content)
                VALUES ('delete', old.rowid, old.summary, old.tags, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, summary, tags, content)
                VALUES ('delete', old.rowid, old.summary, old.tags, old.content);
                INSERT INTO chunks_fts(rowid, summary, tags, content)
                VALUES (new.rowid, new.summary, new.tags, new.content);
            END;
        """)
    except sqlite3.OperationalError:
        # FTS5 not available — degrade gracefully
        pass
    conn.commit()


def get_index_path(project_slug: str) -> Path:
    """Get the index database path for a project."""
    store_dir = get_store_dir(project_slug)
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / "index.db"


def insert_chunk(conn: sqlite3.Connection, chunk: Chunk, chunk_file_path: str, embedding: Optional[bytes] = None):
    """Insert a chunk into the index."""
    tags_str = ", ".join(chunk.tags)
    conn.execute(
        """INSERT OR REPLACE INTO chunks
           (id, timestamp, session_id, project_slug, chunk_type, summary, tags, content, tool_name, file_path, chunk_file_path, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (chunk.id, chunk.timestamp, chunk.session_id, chunk.project_slug,
         chunk.chunk_type, chunk.summary, tags_str, chunk.content,
         chunk.tool_name, chunk.file_path, chunk_file_path, embedding),
    )
    conn.commit()


def get_recent(conn: sqlite3.Connection, n: int = 10, project_slug: Optional[str] = None) -> list[dict]:
    """Get the N most recent chunks (LIFO)."""
    if project_slug:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE project_slug = ? ORDER BY timestamp DESC LIMIT ?",
            (project_slug, n),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM chunks ORDER BY timestamp DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [dict(r) for r in rows]


def search_fts5(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    """Search chunks using FTS5 full-text search with BM25 ranking."""
    try:
        rows = conn.execute(
            """SELECT chunks.*, bm25(chunks_fts) as rank
               FROM chunks_fts
               JOIN chunks ON chunks.rowid = chunks_fts.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # FTS5 not available — fall back to LIKE search
        pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT * FROM chunks
               WHERE summary LIKE ? OR tags LIKE ? OR content LIKE ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def search_vector(conn: sqlite3.Connection, query_embedding: bytes, limit: int = 50) -> list[dict]:
    """Search chunks by cosine similarity against stored embeddings."""
    rows = conn.execute(
        "SELECT * FROM chunks WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return []

    query_vec = _bytes_to_floats(query_embedding)
    scored = []
    for row in rows:
        row_dict = dict(row)
        emb = row_dict.get("embedding")
        if emb:
            stored_vec = _bytes_to_floats(emb)
            sim = _cosine_similarity(query_vec, stored_vec)
            row_dict["similarity"] = sim
            scored.append(row_dict)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:limit]


def evict_old_chunks(conn: sqlite3.Connection, project_slug: str, max_chunks: int = 5000, max_age_days: int = 30):
    """Evict chunks exceeding age or count limits. Returns number of chunks deleted."""
    import time
    cutoff = time.time() - (max_age_days * 86400)

    # Delete by age
    cursor = conn.execute(
        "DELETE FROM chunks WHERE project_slug = ? AND timestamp < ?",
        (project_slug, cutoff),
    )
    age_deleted = cursor.rowcount

    # Delete by count (keep newest max_chunks)
    cursor = conn.execute(
        """DELETE FROM chunks WHERE project_slug = ? AND id NOT IN (
            SELECT id FROM chunks WHERE project_slug = ? ORDER BY timestamp DESC LIMIT ?
        )""",
        (project_slug, project_slug, max_chunks),
    )
    count_deleted = cursor.rowcount

    if age_deleted or count_deleted:
        conn.commit()
    return age_deleted + count_deleted


def count_chunks(conn: sqlite3.Connection, project_slug: Optional[str] = None) -> int:
    """Count chunks in the index."""
    if project_slug:
        row = conn.execute("SELECT COUNT(*) FROM chunks WHERE project_slug = ?", (project_slug,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
    return row[0]


def _floats_to_bytes(vec: list[float]) -> bytes:
    """Pack a float list into bytes for BLOB storage."""
    return struct.pack(f"{len(vec)}f", *vec)


def _bytes_to_floats(data: bytes) -> list[float]:
    """Unpack bytes BLOB back to float list."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


def search_hybrid(conn: sqlite3.Connection, query_embedding: Optional[bytes], query_text: str, limit: int = 10, k: int = 60) -> list[dict]:
    """Hybrid search using Reciprocal Rank Fusion of vector + FTS5 results.

    k is the RRF constant (60 is standard). Higher k reduces impact of rank position.
    Falls back to FTS5-only if no embeddings available.
    """
    fts_results = search_fts5(conn, query_text, limit=limit * 5)
    vec_results = search_vector(conn, query_embedding, limit=limit * 5) if query_embedding else []

    # Build rank maps: chunk_id -> rank (1-based)
    fts_rank = {r["id"]: i + 1 for i, r in enumerate(fts_results)}
    vec_rank = {r["id"]: i + 1 for i, r in enumerate(vec_results)}

    # Collect all candidate IDs
    all_ids = set(fts_rank) | set(vec_rank)

    # RRF score: sum of 1/(k + rank) for each list the chunk appears in
    scores: dict[str, float] = {}
    for chunk_id in all_ids:
        score = 0.0
        if chunk_id in fts_rank:
            score += 1.0 / (k + fts_rank[chunk_id])
        if chunk_id in vec_rank:
            score += 1.0 / (k + vec_rank[chunk_id])
        scores[chunk_id] = score

    # Build lookup of all candidates
    all_chunks = {r["id"]: r for r in fts_results}
    all_chunks.update({r["id"]: r for r in vec_results})

    ranked = sorted(all_ids, key=lambda cid: scores[cid], reverse=True)
    results = []
    for cid in ranked[:limit]:
        row = dict(all_chunks[cid])
        row["rrf_score"] = scores[cid]
        results.append(row)
    return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
