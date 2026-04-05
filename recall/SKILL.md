# Recall — Persistent Context Restore

Restore prior conversation context from the persistent context store. Works after `/clear` to bring back what you were working on.

## User-invocable

When the user types `/recall`, run this skill.

## Arguments

- `/recall` — no args: LIFO restore, returns last N chunks (default 10)
- `/recall <query>` — semantic search: returns top 5 chunks matching the natural language query
- `/recall --n <count>` — LIFO with custom count
- `/recall --deep <query>` — forces Claude re-ranking for higher precision
- `/recall --stats` — show store statistics (chunk count, oldest, newest)

## Instructions

### Determine mode

Parse the arguments:
1. If `--stats` is present: run stats mode
2. If no arguments (or only `--n`): run LIFO mode
3. If any other text is present: run semantic mode
4. If `--deep` is present with text: run semantic mode with re-ranking forced

### LIFO Mode (no query)

Run the following Python script to retrieve recent chunks:

```python
import sys, json
sys.path.insert(0, str(__import__('pathlib').Path.home() / '.claude' / 'context-store'))
from context_store.storage import slug_from_cwd, get_config
from context_store.index import open_index, get_index_path, get_recent
import os

slug = slug_from_cwd(os.getcwd())
config = get_config(slug)
n = {N_VALUE}  # from --n flag, default config['fast_restore_count']
db_path = get_index_path(slug)
conn = open_index(db_path)
chunks = get_recent(conn, n=n, project_slug=slug)
conn.close()

for c in chunks:
    print(f"[{c['chunk_type']}] {c['summary']}")
    if c['content']:
        # Print first 200 chars of content
        print(f"  {c['content'][:200]}")
    print()
```

Replace `{N_VALUE}` with the `--n` argument value, or the config default (10).

Format the output as a structured context block:

```
Restored {count} chunks (LIFO, most recent first):

[chunk_type] summary
  content preview...

[chunk_type] summary
  content preview...

~{token_estimate} tokens injected.
```

### Semantic Mode (with query)

Run the following Python script:

```python
import sys, json
sys.path.insert(0, str(__import__('pathlib').Path.home() / '.claude' / 'context-store'))
from context_store.storage import slug_from_cwd
from context_store.index import open_index, get_index_path, search_vector, search_fts5
from context_store.search import embed_text, is_model_available
import os

slug = slug_from_cwd(os.getcwd())
db_path = get_index_path(slug)
conn = open_index(db_path)
query = "{QUERY}"  # the user's natural language query

if is_model_available():
    query_emb = embed_text(query)
    candidates = search_vector(conn, query_emb, limit=50)
else:
    # Fallback to FTS5
    candidates = search_fts5(conn, query, limit=50)

conn.close()

# Print candidates for re-ranking
for i, c in enumerate(candidates[:50]):
    sim = c.get('similarity', 'N/A')
    print(f"{i+1}. [{c['chunk_type']}] {c['summary']} (score: {sim})")
```

Replace `{QUERY}` with the user's search query.

**If `--deep` flag is set OR there are more than 10 candidates:**
After retrieving candidates, re-rank them by reading the summaries and selecting the top 5 most relevant to the query. Use your judgment as Claude to pick the best matches.

**If candidates <= 10 and no `--deep` flag:**
Return all candidates directly.

Format the output:

```
Found {count} relevant chunks (query: "{query}"):

[chunk_type] summary
  content preview...

~{token_estimate} tokens injected.
```

### Stats Mode

```python
import sys
sys.path.insert(0, str(__import__('pathlib').Path.home() / '.claude' / 'context-store'))
from context_store.storage import slug_from_cwd, get_config
from context_store.index import open_index, get_index_path, count_chunks, get_recent
import os

slug = slug_from_cwd(os.getcwd())
db_path = get_index_path(slug)
conn = open_index(db_path)
total = count_chunks(conn, slug)
recent = get_recent(conn, n=1, project_slug=slug)
oldest_q = conn.execute("SELECT * FROM chunks WHERE project_slug = ? ORDER BY timestamp ASC LIMIT 1", (slug,)).fetchone()
conn.close()
config = get_config(slug)

print(f"Project: {slug}")
print(f"Total chunks: {total}")
print(f"Chunk limit: {config['chunk_limit']}")
print(f"Retention: {config['retention_days']} days")
if recent:
    import datetime
    ts = recent[0]['timestamp']
    print(f"Newest: {datetime.datetime.fromtimestamp(ts).isoformat()} — {recent[0]['summary'][:80]}")
if oldest_q:
    ts = oldest_q['timestamp']
    print(f"Oldest: {datetime.datetime.fromtimestamp(ts).isoformat()} — {oldest_q['summary'][:80]}")
```

Format as a brief status block.

## Rules

- Always use the current working directory to derive the project slug
- If the context store doesn't exist yet (no index.db), say "No context stored yet for this project. Context accumulates automatically as you work."
- If Model2Vec model is not available during semantic search, fall back to FTS5 and warn: "Using keyword search (Model2Vec not available). Run `pip install model2vec` for semantic search."
- Keep output concise — chunk previews should be 1-2 lines max
- Estimate token count as ~4 chars per token on the total output
