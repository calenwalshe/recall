#!/usr/bin/env bash
set -euo pipefail

echo "Installing /recall — Persistent Context for Claude Code"
echo ""

# 1. Python dependency
echo "Installing model2vec..."
pip install model2vec 2>&1 | tail -1

# 2. Copy context_store module
echo "Copying context_store module..."
mkdir -p ~/.claude/context-store/context_store
cp context_store/__init__.py ~/.claude/context-store/context_store/
cp context_store/storage.py ~/.claude/context-store/context_store/
cp context_store/index.py ~/.claude/context-store/context_store/
cp context_store/search.py ~/.claude/context-store/context_store/

# 3. Copy hook
echo "Copying capture hook..."
cp context-capture.py ~/.claude/hooks/context-capture.py

# 4. Copy skill
echo "Copying /recall skill..."
mkdir -p ~/.claude/skills/recall
cp SKILL.md ~/.claude/skills/recall/SKILL.md

# 5. Register hooks
SETTINGS=~/.claude/settings.json
if [ -f "$SETTINGS" ]; then
    if grep -q 'context-capture' "$SETTINGS"; then
        echo "Hooks already registered in settings.json"
    else
        echo ""
        echo "ACTION REQUIRED: Add these hooks to your ~/.claude/settings.json"
        echo ""
        echo "PostToolUse (matcher: Write|Edit|Bash|Agent, async: true):"
        echo "  PYTHONPATH=~/.claude/context-store python3 ~/.claude/hooks/context-capture.py"
        echo ""
        echo "Stop (async: true):"
        echo "  PYTHONPATH=~/.claude/context-store python3 ~/.claude/hooks/context-capture.py"
        echo ""
        echo "PostCompact (async: true):"
        echo "  PYTHONPATH=~/.claude/context-store python3 ~/.claude/hooks/context-capture.py"
    fi
else
    echo "WARNING: ~/.claude/settings.json not found. Create it and add hook registrations."
fi

echo ""
echo "Done. Context capture is active. Use /recall after /clear."
