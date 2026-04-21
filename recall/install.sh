#!/usr/bin/env bash
set -euo pipefail

echo "Installing /recall — Persistent Context for Claude Code"
echo ""

# 1. Python dependency
echo "Installing model2vec..."
pip install model2vec --quiet 2>&1 | tail -1 || echo "  (model2vec install failed — FTS5 fallback will be used)"

# 2. Copy context_store module
echo "Copying context_store module..."
mkdir -p ~/.claude/context-store/context_store
cp context_store/__init__.py ~/.claude/context-store/context_store/
cp context_store/storage.py ~/.claude/context-store/context_store/
cp context_store/index.py ~/.claude/context-store/context_store/
cp context_store/search.py ~/.claude/context-store/context_store/
cp context_store/redact.py ~/.claude/context-store/context_store/

# 3. Copy hook
echo "Copying capture hook..."
cp context-capture.py ~/.claude/hooks/context-capture.py

# 4. Copy skill
echo "Copying /recall skill..."
mkdir -p ~/.claude/skills/recall
cp SKILL.md ~/.claude/skills/recall/SKILL.md

# 5. Auto-register hooks in ~/.claude/settings.json
SETTINGS=~/.claude/settings.json
HOOK_CMD="PYTHONPATH=~/.claude/context-store python3 ~/.claude/hooks/context-capture.py"

if grep -q 'context-capture' "$SETTINGS" 2>/dev/null; then
    echo "Hooks already registered in settings.json — skipping"
else
    echo "Registering hooks in settings.json..."
    python3 - <<PYEOF
import json, sys
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
hook_cmd = "$HOOK_CMD"
hook_entry = {"type": "command", "command": hook_cmd, "async": True}

if settings_path.exists():
    with open(settings_path) as f:
        settings = json.load(f)
else:
    settings = {}

# PostToolUse with matcher
post_tool = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
already = any(isinstance(h, dict) and "context-capture" in h.get("command", "") for h in post_tool)
if not already:
    post_tool.append({
        "matcher": "Write|Edit|Bash|Agent",
        "hooks": [hook_entry]
    })

# Stop
stop_hooks = settings["hooks"].setdefault("Stop", [])
if not any(isinstance(h, dict) and "context-capture" in h.get("command", "") for h in stop_hooks):
    stop_hooks.append(hook_entry)

# PostCompact
compact_hooks = settings["hooks"].setdefault("PostCompact", [])
if not any(isinstance(h, dict) and "context-capture" in h.get("command", "") for h in compact_hooks):
    compact_hooks.append(hook_entry)

settings_path.parent.mkdir(parents=True, exist_ok=True)
with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
print("  hooks registered.")
PYEOF
fi

echo ""
echo "Done. Context capture is active. Use /recall after /clear."
