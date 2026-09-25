"""R9 red-line gate: no deploy script may prune Docker images.

On 2026-09-25 a deploy script ran ``docker image prune -a`` while ENABLED
plugin Builds still referenced the cached images by digest; the runner then
failed every job on those builds within milliseconds and recovery required
re-installing each build's image archive by hand. The incident red line
(review doc 插件系统成熟度-审查方案-2026-09-25.md §0) forbids any form of
image prune in any script; space reclamation may only go through B4's
reference-aware deletion path. This gate scans every shell script under
``deploy/`` so the red line cannot regress silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DEPLOY_ROOT = Path("deploy")

# Banned fragments, compared case-insensitively against script contents.
# ``prune -a`` covers any other ``<something> prune -a`` spelling.
BANNED_PRUNE_FRAGMENTS = (
    "docker image prune",
    "docker system prune",
    "prune -a",
)

_BAN_MESSAGE = (
    "{path} 第 {lineno} 行出现禁用的 prune 命令 {fragment!r}: "
    "2026-09-25 事故红线 — 任何脚本不得出现任何形式的 image prune, "
    "空间回收只能走 B4 引用感知路径"
)


def assert_script_has_no_prune(path: Path) -> None:
    """Raise AssertionError when a script body contains a banned prune fragment.

    Whole-line ``#`` comments are ignored so deploy scripts can document the
    red line itself (deploy/ci/deploy-pipeline.sh carries such a note); every
    non-comment line is matched raw and case-insensitively.
    """
    lowered_lines = (
        (lineno, raw.lower())
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if not raw.lstrip().startswith("#")
    )
    for lineno, lowered in lowered_lines:
        for fragment in BANNED_PRUNE_FRAGMENTS:
            assert fragment not in lowered, _BAN_MESSAGE.format(
                path=path, lineno=lineno, fragment=fragment
            )


def test_every_deploy_script_is_free_of_prune_commands() -> None:
    scripts = sorted(DEPLOY_ROOT.rglob("*.sh"))

    assert scripts, "deploy/**.sh must exist for the prune-ban gate to scan"
    for script in scripts:
        assert_script_has_no_prune(script)


def test_prune_carve_out_only_covers_whole_line_comments(tmp_path: Path) -> None:
    """A comment may mention the red line, but trailing commands still trip it."""
    documented = tmp_path / "documented.sh"
    documented.write_text(
        "# NOTE: no `docker image prune` here, ever.\n"
        "docker compose up -d\n",
        encoding="utf-8",
    )
    assert_script_has_no_prune(documented)

    smuggled = tmp_path / "smuggled.sh"
    smuggled.write_text(
        "# harmless banner\n"
        "true && docker image prune -f # looks inline\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="2026-09-25"):
        assert_script_has_no_prune(smuggled)


@pytest.mark.parametrize(
    ("fragment", "spelling"),
    [
        ("docker image prune", "docker IMAGE prune -a"),
        ("docker system prune", "DOCKER SYSTEM PRUNE --volumes"),
        ("prune -a", "docker builder prune -a"),
    ],
)
def test_prune_ban_assertion_catches_each_banned_fragment(
    tmp_path: Path, fragment: str, spelling: str
) -> None:
    """The gate must be able to fail: a deliberately banned script trips it.

    The offenders are written inline to ``tmp_path`` — the assertion function
    is the exact one the traversal test applies to the real deploy tree.
    """
    offender = tmp_path / "offender.sh"
    offender.write_text(f"#!/usr/bin/env bash\n{spelling}\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="2026-09-25 事故红线"):
        assert_script_has_no_prune(offender)
