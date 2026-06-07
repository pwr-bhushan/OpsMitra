"""RED-phase tests for Step 10 documentation deliverables.

All tests read file content from the docs/ and root directories.
Every test will FAIL until the corresponding document is created (WS-A, B, C, E).

File assertions:
  - docs/security-checklist.md   (WS-A)
  - README.md                    (WS-B)
  - docs/architecture.md         (WS-B, Mermaid)
  - docs/aws-cost-controls.md    (WS-C)
  - docs/aws-window-replay.md    (WS-E)
  - docs/aws-window-replay-findings.md (WS-E)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_DOCS_DIR = _REPO_ROOT / "docs"


def _read(path: Path) -> str:
    """Read a file relative to repo root; raises FileNotFoundError if missing."""
    return path.read_text(encoding="utf-8")


def _h2_headings(text: str) -> list[str]:
    """Return a list of H2 heading titles (without the '## ' prefix)."""
    return [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.MULTILINE)]


def _count_mermaid_blocks(text: str) -> int:
    """Return the number of ```mermaid fenced code blocks in text."""
    return len(re.findall(r"^```mermaid", text, re.MULTILINE))


# ---------------------------------------------------------------------------
# WS-A — security-checklist.md
# ---------------------------------------------------------------------------


def test_security_checklist_sections_exist():
    """docs/security-checklist.md must exist with the required H2 section headings.

    RED: file does not exist yet.
    """
    path = _DOCS_DIR / "security-checklist.md"
    text = _read(path)  # FileNotFoundError → RED until WS-A creates the file
    headings = _h2_headings(text)

    required = {"IAM", "Secret Handling", "Model Data Exposure", "Athena Cost Risks", "Operational Failure Modes"}
    missing = [req for req in required if not any(req.lower() in h.lower() for h in headings)]
    assert not missing, f"Missing H2 sections in security-checklist.md: {missing!r}"


# ---------------------------------------------------------------------------
# WS-B — README.md
# ---------------------------------------------------------------------------


def test_readme_sections_exist():
    """README.md must have the 6 required H2 sections.

    RED: README currently lacks the required sections.
    """
    path = _REPO_ROOT / "README.md"
    text = _read(path)
    headings = _h2_headings(text)

    required = {
        "Overview",
        "Quickstart",
        "Environment Variables",
        "Local Demo Walkthrough",
        "Architecture",
        "AWS Deployment",
    }
    missing = [req for req in required if not any(req.lower() in h.lower() for h in headings)]
    assert not missing, f"Missing H2 sections in README.md: {missing!r}"


def test_readme_contains_overview_mermaid():
    """README.md must contain at least one ```mermaid fenced block (D4).

    RED: current README has no Mermaid block.
    """
    path = _REPO_ROOT / "README.md"
    text = _read(path)
    count = _count_mermaid_blocks(text)
    assert count >= 1, f"README.md must contain at least 1 ```mermaid block, found {count}"


# ---------------------------------------------------------------------------
# WS-B — architecture.md (Mermaid diagrams D1, D2, D3)
# ---------------------------------------------------------------------------


def test_architecture_contains_three_mermaid_blocks():
    """docs/architecture.md must contain at least 3 distinct ```mermaid blocks (D1, D2, D3).

    RED: docs/architecture.md may not exist or has fewer than 3 blocks.
    """
    path = _DOCS_DIR / "architecture.md"
    text = _read(path)  # FileNotFoundError → RED until WS-B creates/extends the file
    count = _count_mermaid_blocks(text)
    assert count >= 3, (
        f"docs/architecture.md must contain at least 3 ```mermaid blocks "
        f"(D1 pipeline flowchart, D2 sequence, D3 evaluation flowchart), found {count}"
    )


# ---------------------------------------------------------------------------
# WS-C — aws-cost-controls.md
# ---------------------------------------------------------------------------


def test_aws_cost_doc_sections_exist():
    """docs/aws-cost-controls.md must exist with the required H2 sections.

    RED: file does not exist yet.
    """
    path = _DOCS_DIR / "aws-cost-controls.md"
    text = _read(path)  # FileNotFoundError → RED until WS-C creates the file
    headings = _h2_headings(text)

    required = {
        "Partition Strategy",
        "Athena Scan Bytes",
        "S3 Storage Classes",
        "Workgroup Configuration",
        "Cost-Control Env Vars",
    }
    missing = [req for req in required if not any(req.lower() in h.lower() for h in headings)]
    assert not missing, f"Missing H2 sections in aws-cost-controls.md: {missing!r}"


# ---------------------------------------------------------------------------
# WS-E — aws-window-replay.md
# ---------------------------------------------------------------------------


def test_aws_window_replay_exercise_doc_exists():
    """docs/aws-window-replay.md must exist with the required H2 sections.

    RED: file does not exist yet.
    """
    path = _DOCS_DIR / "aws-window-replay.md"
    text = _read(path)  # FileNotFoundError → RED until WS-E creates the file
    headings = _h2_headings(text)

    required = {
        "Prerequisites",
        "Prepare Window",
        "Configure Environment",
        "Run Detector",
        "Capture Findings",
    }
    missing = [req for req in required if not any(req.lower() in h.lower() for h in headings)]
    assert not missing, f"Missing H2 sections in aws-window-replay.md: {missing!r}"


# ---------------------------------------------------------------------------
# WS-E — aws-window-replay-findings.md (template)
# ---------------------------------------------------------------------------


def test_aws_window_replay_findings_template_exists():
    """docs/aws-window-replay-findings.md must exist with the required template sections.

    RED: file does not exist yet.
    """
    path = _DOCS_DIR / "aws-window-replay-findings.md"
    text = _read(path)  # FileNotFoundError → RED until WS-E creates the file
    headings = _h2_headings(text)

    required = {
        "Run Date",
        "Window",
        "Detectors Triggered",
        "False Positives",
        "Conclusions",
    }
    missing = [req for req in required if not any(req.lower() in h.lower() for h in headings)]
    assert not missing, f"Missing H2 sections in aws-window-replay-findings.md: {missing!r}"


# ---------------------------------------------------------------------------
# M4 — keyword content smoke-checks
# ---------------------------------------------------------------------------


def test_docs_contain_required_keywords():
    """Smoke-check that critical docs retain expected content keywords."""
    checks = [
        ("docs/security-checklist.md", "IAM policy"),
        ("docs/aws-cost-controls.md", "BytesScannedCutoffPerQuery"),
        ("docs/architecture.md", "detect_anomalies"),
        ("docs/architecture.md", "Cooldown"),
    ]
    for rel_path, keyword in checks:
        doc_path = _REPO_ROOT / rel_path
        assert doc_path.exists(), f"{rel_path} missing"
        text = doc_path.read_text(encoding="utf-8")
        assert keyword in text, (
            f"{rel_path} must contain {keyword!r}; got first 200 chars: {text[:200]!r}"
        )
