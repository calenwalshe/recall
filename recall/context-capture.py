#!/usr/bin/env python3
"""Context capture hook — PostToolUse, Stop, and PostCompact handler.

Captures high-signal context chunks from Claude Code tool calls and session events.
Stores as markdown files with YAML frontmatter, indexed in SQLite with FTS5 and
Model2Vec embeddings.

Registered as async hook — must not block the session.
"""

import json
import os
import sys
import time
from pathlib import Path

# Add context_store to path
sys.path.insert(0, str(Path.home() / ".claude" / "context-store"))

from context_store.storage import (
    Chunk, write_chunk, generate_chunk_id, slug_from_cwd, get_config, write_default_config
)
from context_store.index import (
    open_index, get_index_path, insert_chunk, evict_old_chunks
)
from context_store.search import embed_text


def read_stdin() -> dict:
    """Read JSON from stdin with timeout handling."""
    try:
        data = sys.stdin.read()
        if not data:
            return {}
        return json.loads(data)
    except (json.JSONDecodeError, IOError):
        return {}


def extract_chunk_from_tool_use(event: dict) -> Chunk | None:
    """Extract a context chunk from a PostToolUse event."""
    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})
    tool_response = event.get("tool_response", {})
    session_id = event.get("session_id", "unknown")
    cwd = event.get("cwd", os.getcwd())
    project_slug = slug_from_cwd(cwd)

    config = get_config(project_slug)
    allowed_tools = config.get("tool_type_filter", ["Write", "Edit", "Bash", "Agent"])

    if tool_name not in allowed_tools:
        return None

    ts = time.time()
    chunk_id = generate_chunk_id(ts)

    if tool_name in ("Write", "Edit"):
        fp = tool_input.get("file_path", "")
        if tool_name == "Write":
            summary = f"Wrote file: {fp}"
            content = tool_input.get("content", "")[:500]
        else:
            old = tool_input.get("old_string", "")[:200]
            new = tool_input.get("new_string", "")[:200]
            summary = f"Edited file: {fp}"
            content = f"Changed:\n  - {old}\n  + {new}"
        chunk_type = "file_change"
        tags = ["file_change", Path(fp).suffix.lstrip(".") if fp else ""]
        tags = [t for t in tags if t]

    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        # Truncate long command output
        resp_text = ""
        if isinstance(tool_response, dict):
            resp_text = str(tool_response.get("stdout", ""))[:300]
        elif isinstance(tool_response, str):
            resp_text = tool_response[:300]
        summary = f"Ran command: {cmd[:100]}"
        content = f"$ {cmd}\n{resp_text}"
        chunk_type = "command_result"
        tags = ["command"]

    elif tool_name == "Agent":
        desc = tool_input.get("description", "")
        prompt = tool_input.get("prompt", "")[:300]
        summary = f"Agent task: {desc}"
        content = f"Agent: {desc}\nPrompt: {prompt}"
        chunk_type = "finding"
        tags = ["agent", "subagent"]

    else:
        return None

    return Chunk(
        id=chunk_id,
        timestamp=ts,
        session_id=session_id,
        project_slug=project_slug,
        chunk_type=chunk_type,
        summary=summary,
        tags=tags,
        content=content,
        tool_name=tool_name,
        file_path=tool_input.get("file_path", ""),
    )


def extract_session_summary(event: dict) -> Chunk | None:
    """Extract a session summary chunk from a Stop event."""
    session_id = event.get("session_id", "unknown")
    cwd = event.get("cwd", os.getcwd())
    project_slug = slug_from_cwd(cwd)
    transcript_path = event.get("transcript_path", "")

    # Try to read last portion of transcript for summary
    summary_text = "Session ended"
    if transcript_path and os.path.exists(transcript_path):
        try:
            with open(transcript_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                read_size = min(size, 8192)
                f.seek(size - read_size)
                tail = f.read().decode("utf-8", errors="replace")
            # Parse last few JSONL entries for assistant messages
            lines = tail.strip().split("\n")
            assistant_msgs = []
            for line in reversed(lines):
                try:
                    entry = json.loads(line)
                    if entry.get("role") == "assistant":
                        text = ""
                        content = entry.get("content", [])
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text += block.get("text", "")
                        if text:
                            assistant_msgs.append(text[:500])
                        if len(assistant_msgs) >= 3:
                            break
                except json.JSONDecodeError:
                    continue
            if assistant_msgs:
                summary_text = "Session summary: " + " | ".join(reversed(assistant_msgs))
        except Exception:
            pass

    ts = time.time()
    return Chunk(
        id=generate_chunk_id(ts),
        timestamp=ts,
        session_id=session_id,
        project_slug=project_slug,
        chunk_type="session_summary",
        summary=summary_text[:200],
        tags=["session_summary"],
        content=summary_text[:2000],
    )


def extract_compact_summary(event: dict) -> Chunk | None:
    """Extract a compact summary chunk from a PostCompact event."""
    session_id = event.get("session_id", "unknown")
    cwd = event.get("cwd", os.getcwd())
    project_slug = slug_from_cwd(cwd)
    compact_summary = event.get("compact_summary", "")

    if not compact_summary:
        return None

    ts = time.time()
    return Chunk(
        id=generate_chunk_id(ts),
        timestamp=ts,
        session_id=session_id,
        project_slug=project_slug,
        chunk_type="compact_summary",
        summary=f"Compaction summary: {compact_summary[:150]}",
        tags=["compact_summary", "compaction"],
        content=compact_summary[:3000],
    )


def store_chunk(chunk: Chunk):
    """Write chunk to disk and index."""
    # Ensure config exists
    write_default_config(chunk.project_slug)

    # Write markdown file
    filepath = write_chunk(chunk)

    # Compute embedding
    embedding = embed_text(chunk.summary)

    # Index in SQLite
    db_path = get_index_path(chunk.project_slug)
    conn = open_index(db_path)
    try:
        insert_chunk(conn, chunk, str(filepath), embedding)

        # Run eviction check
        config = get_config(chunk.project_slug)
        evict_old_chunks(
            conn,
            chunk.project_slug,
            max_chunks=config.get("chunk_limit", 5000),
            max_age_days=config.get("retention_days", 30),
        )
    finally:
        conn.close()


def main():
    event = read_stdin()
    if not event:
        sys.exit(0)

    hook_event = event.get("hook_event_name", "") or event.get("hookEventName", "")

    try:
        if hook_event == "PostToolUse":
            chunk = extract_chunk_from_tool_use(event)
            if chunk:
                store_chunk(chunk)

        elif hook_event == "Stop":
            chunk = extract_session_summary(event)
            if chunk:
                store_chunk(chunk)

        elif hook_event == "PostCompact":
            chunk = extract_compact_summary(event)
            if chunk:
                store_chunk(chunk)

    except Exception:
        # Silent failure — never block the session
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
