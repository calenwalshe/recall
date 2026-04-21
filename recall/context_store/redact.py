"""Secret redaction and sensitive path suppression.

Applied before any chunk is written to disk or indexed.
Patterns cover API keys, tokens, private keys, and sensitive file paths.
"""

import re

# Patterns that match secret material — replace with placeholder
_SECRET_PATTERNS = [
    # Generic API key patterns (hex/base62/base64 long strings after key= or _key= etc.)
    (re.compile(r'(?i)(api[_-]?key|api[_-]?secret|access[_-]?key|secret[_-]?key)\s*[=:]\s*["\']?([A-Za-z0-9+/\-_]{20,})["\']?'), r'\1=<REDACTED>'),
    # Bearer / Authorization tokens
    (re.compile(r'(?i)(bearer|token|authorization)\s*[=:]\s*["\']?([A-Za-z0-9+/\-_.]{20,})["\']?'), r'\1=<REDACTED>'),
    # OpenAI / Anthropic / Google key shapes
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), '<REDACTED_SK>'),
    (re.compile(r'sk-ant-[A-Za-z0-9\-]{20,}'), '<REDACTED_ANT>'),
    (re.compile(r'AIza[A-Za-z0-9\-_]{30,}'), '<REDACTED_GCP>'),
    # GitHub tokens
    (re.compile(r'gh[pousr]_[A-Za-z0-9]{36,}'), '<REDACTED_GH>'),
    # AWS
    (re.compile(r'AKIA[A-Z0-9]{16}'), '<REDACTED_AWS_KEY>'),
    (re.compile(r'(?i)aws[_-]?secret[_-]?access[_-]?key\s*[=:]\s*["\']?([A-Za-z0-9+/]{40})["\']?'), 'aws_secret_access_key=<REDACTED>'),
    # Private key blocks
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.DOTALL), '<REDACTED_PRIVATE_KEY>'),
    # Generic high-entropy quoted strings (40+ chars of base64-ish content)
    (re.compile(r'["\']([A-Za-z0-9+/\-_]{40,}={0,2})["\']'), '"<REDACTED>"'),
]

# File paths to suppress entirely — return empty string if matched
_SUPPRESSED_PATH_PATTERNS = [
    re.compile(r'\.env(\.|$)', re.IGNORECASE),
    re.compile(r'\.pem$', re.IGNORECASE),
    re.compile(r'\.key$', re.IGNORECASE),
    re.compile(r'id_rsa'),
    re.compile(r'id_ed25519'),
    re.compile(r'id_ecdsa'),
    re.compile(r'\.ssh/'),
    re.compile(r'credentials\.json', re.IGNORECASE),
    re.compile(r'secret[s]?\.(json|yaml|yml|toml)', re.IGNORECASE),
    re.compile(r'\.api[-_]?keys?', re.IGNORECASE),
]


def is_suppressed_path(path: str) -> bool:
    """Return True if this file path should be completely suppressed (no chunk written)."""
    if not path:
        return False
    return any(p.search(path) for p in _SUPPRESSED_PATH_PATTERNS)


def redact(text: str) -> str:
    """Apply all redaction patterns to a text string."""
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_chunk_fields(summary: str, content: str, file_path: str) -> tuple[str, str, str]:
    """Redact all user-facing fields of a chunk before persistence.

    Returns (summary, content, file_path) with secrets scrubbed.
    If file_path is a suppressed path, returns empty strings for all fields
    so the caller can skip writing the chunk.
    """
    if is_suppressed_path(file_path):
        return "", "", ""
    return redact(summary), redact(content), file_path
