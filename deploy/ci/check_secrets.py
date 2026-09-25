#!/usr/bin/env python3
"""Scan the working tree for credentials before the code leaves the machine.

Used as the first job of the GitHub Actions pipeline and can be run locally:

    python deploy/ci/check_secrets.py                 # report, fail on errors
    python deploy/ci/check_secrets.py --fail-on never # report only

Known literal secrets (an admin password, a deploy token, ...) can be supplied
through HUB_SECRET_LITERALS (comma separated) so the literal itself never has to
live in the repository:

    HUB_SECRET_LITERALS='Hub-2026-...,abc123' python deploy/ci/check_secrets.py

Exit codes: 0 clean (or below the --fail-on threshold), 1 findings at or above
the threshold, 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".zst", ".whl", ".so", ".dylib", ".dll", ".exe", ".woff", ".woff2",
    ".ttf", ".eot", ".sqlite", ".db",
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}

# Tests use dummy tokens and the docs quote the deployment host's public IP, so
# the noisy heuristics are muted there; pass --include-docs for a full audit.
NOISE_PREFIXES = ("tests/", "docs/", "web/")

# Documentation / reserved ranges that are never a real host of ours.
RESERVED_IPS = {
    "169.254.169.254",  # cloud metadata endpoint, appears in SSRF tests
    "93.184.216.34",  # example.com
    "8.8.8.8",
    "1.1.1.1",
    "0.0.0.0",
    "255.255.255.255",
}

# A value is considered a placeholder - not a real secret - when it matches one
# of these: <...>, ${...}, $VAR, *** , empty, or an obvious documentation dummy.
PLACEHOLDER_RE = re.compile(
    r"""^(
        <.*>|\$\{.*\}|\$[A-Z0-9_]+|%[A-Z0-9_]+%|\*{3,}|-{3,}|\.{3,}
        |your[-_ ]?\w+|changeme|example|placeholder|dummy|xxx+|todo|none|null|redacted
        |set[-_ ]?me|from[-_ ]?env|os\.getenv\(
    )$""",
    re.VERBOSE | re.IGNORECASE,
)

ASSIGNMENT_RE = re.compile(
    r"""(?i)\b(
        password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key
        |private[_-]?key|client[_-]?secret|bootstrap[_-]?password
    )\b\s*[:=]\s*(?P<quote>["']?)(?P<value>[^"'\s,;)]{4,})(?P=quote)?"""
)

PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
KNOWN_TOKEN_RE = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9]{32,}\b"
)
PUBLIC_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass
class Finding:
    severity: str  # "error" | "warning"
    path: str
    line: int
    rule: str
    excerpt: str

    def render(self) -> str:
        return f"{self.severity.upper():7} {self.path}:{self.line} [{self.rule}] {self.excerpt}"


def tracked_files(root: Path) -> list[Path]:
    """Prefer `git ls-files`; fall back to a walk when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8", "replace")
        candidates = [root / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        candidates = [p for p in root.rglob("*") if p.is_file()]

    files = []
    for path in candidates:
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        files.append(path)
    return files


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw:
        return None
    return raw.decode("utf-8", "replace")


def is_reserved_ip(ip: str) -> bool:
    if ip in RESERVED_IPS:
        return True
    a, b, c, _ = (int(part) for part in ip.split("."))
    # Loopback + RFC 1918 private ranges.
    if a in (10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31):
        return True
    # RFC 6598 Carrier-Grade NAT (shared address space, not a public host).
    if a == 100 and 64 <= b <= 127:
        return True
    # RFC 5737 documentation ranges: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24
    return (a, b, c) in {(192, 0, 2), (198, 51, 100), (203, 0, 113)}


def scan(
    root: Path, literals: list[str], ignore_ips: set[str], include_docs: bool = False
) -> list[Finding]:
    findings: list[Finding] = []
    for path in tracked_files(root):
        text = read_text(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()
        noisy = not include_docs and rel.startswith(NOISE_PREFIXES)
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue

            if PRIVATE_KEY_RE.search(line):
                findings.append(Finding("error", rel, number, "private-key", "PEM private key"))
            if KNOWN_TOKEN_RE.search(line):
                findings.append(Finding("error", rel, number, "known-token", "provider token"))

            for literal in literals:
                if literal and literal in line:
                    findings.append(
                        Finding("error", rel, number, "known-literal", "HUB_SECRET_LITERALS value")
                    )

            for match in ASSIGNMENT_RE.finditer(line):
                if noisy:
                    continue
                value = match.group("value")
                if PLACEHOLDER_RE.match(value):
                    continue
                if value.startswith(("os.environ", "os.getenv", "getenv", "config", "settings")):
                    continue
                if len(value) < 6 or value.isdigit():
                    continue
                label = f"{match.group(1)}=<len {len(value)}>"
                findings.append(Finding("warning", rel, number, "assignment", label))

            for ip in PUBLIC_IP_RE.findall(line):
                if ip in ignore_ips or is_reserved_ip(ip):
                    continue
                # The repository is public: a routable host address in any tracked
                # file is exactly the leak this gate exists to stop, so this rule
                # is deliberately never muted by the docs/tests noise suppression.
                findings.append(Finding("error", rel, number, "public-ip", ip))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan tracked files for credentials.")
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument(
        "--fail-on",
        choices=("error", "warning", "never"),
        default="error",
        help="minimum severity that makes the command exit non-zero",
    )
    parser.add_argument(
        "--ignore-ip",
        action="append",
        default=[],
        help="public IP to treat as expected (repeatable)",
    )
    parser.add_argument(
        "--include-docs",
        action="store_true",
        help="also report assignments and IPs inside tests/ and docs/",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    raw_literals = os.environ.get("HUB_SECRET_LITERALS", "")
    literals = [value.strip() for value in raw_literals.split(",") if value.strip()]
    findings = scan(root, literals, set(args.ignore_ip), args.include_docs)

    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")

    for finding in findings:
        print(finding.render())
    print(f"\nscanned {root}: {errors} error(s), {warnings} warning(s)")

    if args.fail_on == "never":
        return 0
    if args.fail_on == "error":
        return 1 if errors else 0
    return 1 if (errors or warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
