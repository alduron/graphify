"""`graphify update` must accept the same worker flag `extract` does.

The aethergraph CLI passes the SAME argv to update as to extract. Rejecting `--max-workers` made
update exit 2 at ARG PARSING, so every incremental structure sync failed and silently degraded to a
full extract - identical to the earlier `--exclude` failure
(Caveat_SyncPassesMaxWorkersGraphifyUpdateRejects). The cost is invisible: the fallback works, you
just pay full-extract time on every sync forever.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# THIS worktree, not whatever graphify is pip-installed. The subprocess runs with cwd set to a temp
# fixture, so a relative PYTHONPATH would resolve there and silently test the installed package -
# which is exactly how this test first "passed" against the unfixed code.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "graphify", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return root


def test_update_accepts_max_workers(tmp_path: Path) -> None:
    root = _fixture(tmp_path)

    result = _run(["update", str(root), "--max-workers", "2", "--no-cluster"], root)

    assert "unknown update option" not in result.stderr, result.stderr
    assert result.returncode != 2, f"arg parsing rejected the flag: {result.stderr}"


def test_update_accepts_max_workers_equals_form(tmp_path: Path) -> None:
    root = _fixture(tmp_path)

    result = _run(["update", str(root), "--max-workers=2", "--no-cluster"], root)

    assert "unknown update option" not in result.stderr, result.stderr
    assert result.returncode != 2


def test_update_still_rejects_a_genuinely_unknown_option(tmp_path: Path) -> None:
    """The permissive change must not swallow real typos."""
    root = _fixture(tmp_path)

    result = _run(["update", str(root), "--not-a-real-flag"], root)

    assert "unknown update option" in result.stderr
    assert result.returncode == 2


def test_a_malformed_worker_count_warns_rather_than_failing_the_rebuild(tmp_path: Path) -> None:
    """Failing a whole incremental rebuild over a bad worker count is the disproportionate
    failure this flag already caused once."""
    root = _fixture(tmp_path)

    result = _run(["update", str(root), "--max-workers", "abc", "--no-cluster"], root)

    assert "ignoring non-integer --max-workers" in result.stderr
    assert result.returncode != 2
