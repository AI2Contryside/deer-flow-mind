"""Tests for the read_file tool short-circuit on SKILL.md paths.

The read_file tool intentionally refuses to load a skill's `SKILL.md`, because
the skills loader already injects every enabled manifest into the agent's
system prompt. Re-reading it as a tool call duplicates 5-15K tokens of context
for zero new information and is a major contributor to context bloat in
multi-step ERPNext flows.

These tests pin the matcher's behaviour so the short-circuit never silently
regresses, and so the matcher doesn't expand to swallow legitimate reads.
"""

import pytest

from src.sandbox.tools import _is_skill_manifest


@pytest.mark.parametrize(
    "path",
    [
        # Virtual path the agent normally sees.
        "/mnt/skills/public/erpnext-cli/SKILL.md",
        "/mnt/skills/custom/my-skill/SKILL.md",
        # Host-translated paths after replace_virtual_path().
        "/data/deer-flow/skills/public/erpnext-cli/SKILL.md",
        "/var/lib/skills/public/foo/SKILL.md",
        # Case-insensitive match (Frappe / Mac filesystems).
        "/mnt/skills/public/erpnext-cli/skill.md",
        "/mnt/SKILLS/public/x/SKILL.MD",
    ],
)
def test_skill_manifest_paths_are_blocked(path: str) -> None:
    assert _is_skill_manifest(path)


@pytest.mark.parametrize(
    "path",
    [
        # Concrete files inside a skill — must remain readable.
        "/mnt/skills/public/erpnext-cli/scripts/erpnext.py",
        "/mnt/skills/public/erpnext-cli/README.md",
        # Anything outside the skills tree.
        "/mnt/user-data/uploads/order.xlsx",
        "/mnt/user-data/workspace/notes.md",
        "/etc/hosts",
        "",
        # Path that happens to mention SKILL.md but isn't shaped like a manifest.
        "/mnt/user-data/workspace/SKILL.md",  # only 1 path segment under uploads, not 2
        "/mnt/skills/SKILL.md",  # missing group + name segments
        "/mnt/skills/public/SKILL.md",  # missing skill name segment
    ],
)
def test_non_manifest_paths_pass_through(path: str) -> None:
    assert not _is_skill_manifest(path)
