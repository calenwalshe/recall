"""Signal filtering — determine whether a tool event is worth capturing.

Filters out low-signal noise (read-only commands, trivial edits, boilerplate)
before a chunk is written. High-signal events: file changes, meaningful commands,
decisions, errors, agent completions.
"""

import re

# Bash commands with no state-change signal — pure reads or env queries
_LOW_SIGNAL_BASH = re.compile(
    r"^\s*("
    r"ls(\s|$)|cat\s|head\s|tail\s|echo\s|pwd|whoami|date|uname|"
    r"git\s+(status|log|diff|show|branch|remote\s+-v)|"
    r"which\s|type\s|env(\s|$)|printenv|"
    r"python3?\s+-c\s+['\"]import\s+(sys|os)['\"]|"
    r"curl\s+.*\?(\w+=\w+&?)+\s*$"  # bare URL fetches without side effects
    r")"
)

# File extensions worth capturing (source, config, data)
_HIGH_SIGNAL_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".sh", ".bash", ".zsh", ".fish",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".env.example",
    ".md", ".txt", ".html", ".css", ".sql",
    "Dockerfile", "Makefile", "Procfile",
}

# Minimum content length for a chunk to be worth storing
_MIN_CONTENT_CHARS = 30


def is_high_signal_bash(command: str, output: str) -> bool:
    """Return True if this Bash call is worth capturing."""
    if not command or not command.strip():
        return False
    if _LOW_SIGNAL_BASH.match(command):
        return False
    # Commands that produced errors are always high signal
    if "error" in output.lower() or "traceback" in output.lower() or "exception" in output.lower():
        return True
    # git commits, installs, test runs, docker ops — state-changing
    if re.search(r"\b(commit|push|pull|install|pytest|npm\s+(run|install)|docker\s+(build|run|compose)|make\b)", command):
        return True
    # Has meaningful output
    return len(output.strip()) >= _MIN_CONTENT_CHARS


def is_high_signal_file_change(file_path: str, content: str) -> bool:
    """Return True if this Write/Edit is worth capturing."""
    if not file_path:
        return False
    from pathlib import Path
    p = Path(file_path)
    # Check extension
    ext = p.suffix.lower()
    name = p.name
    if ext in _HIGH_SIGNAL_EXTENSIONS or name in _HIGH_SIGNAL_EXTENSIONS:
        return True
    # No extension but meaningful content
    if not ext and len(content.strip()) >= _MIN_CONTENT_CHARS:
        return True
    return False


def is_high_signal_agent(description: str, prompt: str) -> bool:
    """Agent completions are almost always high signal."""
    return bool(description or prompt)
