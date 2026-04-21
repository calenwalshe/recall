"""Chunk storage layer — markdown files with YAML frontmatter."""

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Chunk:
    """A single context chunk."""
    id: str
    timestamp: float
    session_id: str
    project_slug: str
    chunk_type: str  # decision, file_change, finding, command_result, session_summary, compact_summary
    summary: str
    tags: list[str] = field(default_factory=list)
    content: str = ""
    tool_name: str = ""
    file_path: str = ""

    def to_markdown(self) -> str:
        """Serialize to markdown with YAML frontmatter."""
        tags_str = ", ".join(self.tags) if self.tags else ""
        lines = [
            "---",
            f"id: {self.id}",
            f"timestamp: {self.timestamp}",
            f"session_id: {self.session_id}",
            f"project_slug: {self.project_slug}",
            f"chunk_type: {self.chunk_type}",
            f"summary: {self.summary}",
            f"tags: [{tags_str}]",
            f"tool_name: {self.tool_name}",
            f"file_path: {self.file_path}",
            "---",
            "",
            self.content,
        ]
        return "\n".join(lines)

    @classmethod
    def from_markdown(cls, text: str) -> "Chunk":
        """Deserialize from markdown with YAML frontmatter."""
        if not text.startswith("---"):
            raise ValueError("Missing YAML frontmatter")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Malformed frontmatter")
        meta_lines = parts[1].strip().split("\n")
        meta = {}
        for line in meta_lines:
            if ": " in line:
                key, val = line.split(": ", 1)
                meta[key.strip()] = val.strip()
        tags_raw = meta.get("tags", "[]")
        tags_raw = tags_raw.strip("[]")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        return cls(
            id=meta.get("id", ""),
            timestamp=float(meta.get("timestamp", 0)),
            session_id=meta.get("session_id", ""),
            project_slug=meta.get("project_slug", ""),
            chunk_type=meta.get("chunk_type", ""),
            summary=meta.get("summary", ""),
            tags=tags,
            content=parts[2].strip(),
            tool_name=meta.get("tool_name", ""),
            file_path=meta.get("file_path", ""),
        )


def get_store_dir(project_slug: str) -> Path:
    """Get the store directory for a project."""
    base = Path.home() / ".claude" / "context-store"
    return base / project_slug


def get_chunks_dir(project_slug: str) -> Path:
    """Get the chunks directory for a project."""
    d = get_store_dir(project_slug) / "chunks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_chunk_id(timestamp: float) -> str:
    """Generate a unique chunk ID from timestamp."""
    ts_str = f"{timestamp:.6f}".replace(".", "-")
    return f"chunk-{ts_str}"


def write_chunk(chunk: Chunk) -> Path | None:
    """Write a chunk to disk after redaction. Returns path or None if suppressed."""
    from .redact import redact_chunk_fields
    summary, content, file_path = redact_chunk_fields(chunk.summary, chunk.content, chunk.file_path)
    if not summary and not content:
        return None
    chunk = Chunk(
        id=chunk.id, timestamp=chunk.timestamp, session_id=chunk.session_id,
        project_slug=chunk.project_slug, chunk_type=chunk.chunk_type,
        summary=summary, tags=chunk.tags, content=content,
        tool_name=chunk.tool_name, file_path=file_path,
    )
    chunks_dir = get_chunks_dir(chunk.project_slug)
    filepath = chunks_dir / f"{chunk.id}.md"
    filepath.write_text(chunk.to_markdown(), encoding="utf-8")
    return filepath


def read_chunk(filepath: Path) -> Chunk:
    """Read a chunk from a markdown file."""
    text = filepath.read_text(encoding="utf-8")
    return Chunk.from_markdown(text)


def list_chunks(project_slug: str) -> list[Path]:
    """List all chunk files for a project, sorted by name (timestamp order)."""
    chunks_dir = get_chunks_dir(project_slug)
    files = sorted(chunks_dir.glob("chunk-*.md"))
    return files


def slug_from_cwd(cwd: str) -> str:
    """Derive a collision-resistant project slug from a directory path.

    Uses git root if available (prevents two projects named 'src' from colliding).
    Falls back to path hash when git is absent.
    Format: {dirname}-{8-char-hash}
    """
    cwd_path = Path(cwd).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd_path),
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            root_hash = hashlib.sha1(root.encode()).hexdigest()[:8]
            name = Path(root).name.lower().replace(" ", "-")
            return f"{name}-{root_hash}"
    except Exception:
        pass
    # Fallback: use full path hash
    path_hash = hashlib.sha1(str(cwd_path).encode()).hexdigest()[:8]
    name = cwd_path.name.lower().replace(" ", "-")
    return f"{name}-{path_hash}"


def get_config(project_slug: str) -> dict:
    """Read config.json for a project, with defaults."""
    defaults = {
        "chunk_limit": 5000,
        "retention_days": 30,
        "fast_restore_count": 10,
        "tool_type_filter": ["Write", "Edit", "Bash", "Agent"],
        "model_name": "minishlab/potion-retrieval-32M",
    }
    config_path = get_store_dir(project_slug) / "config.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            user_config = json.load(f)
        defaults.update(user_config)
    return defaults


def write_default_config(project_slug: str) -> Path:
    """Write default config.json if it doesn't exist."""
    store_dir = get_store_dir(project_slug)
    store_dir.mkdir(parents=True, exist_ok=True)
    config_path = store_dir / "config.json"
    if not config_path.exists():
        config = {
            "chunk_limit": 5000,
            "retention_days": 30,
            "fast_restore_count": 10,
            "tool_type_filter": ["Write", "Edit", "Bash", "Agent"],
            "model_name": "minishlab/potion-retrieval-32M",
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
    return config_path
