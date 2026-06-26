#!/usr/bin/env python3
"""HarnessBuddy orchestrator — claims GitHub issues and drives implement→review loops."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_CYCLES = 5
AGENT_TIMEOUT = 600  # seconds per agent invocation
IN_PROGRESS = "agent:in-progress"
PR_OPEN = "agent:pr-open"
SKIP_LABELS = {IN_PROGRESS, PR_OPEN}

REPO_ROOT = Path(__file__).parent.parent.resolve()
PROMPTS_DIR = REPO_ROOT / "prompts"


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=capture, text=True, check=check)


def setup() -> None:
    """Ensure GitHub coordination labels exist."""
    for name, color, desc in [
        (IN_PROGRESS, "0075ca", "Agent is actively working on this issue"),
        (PR_OPEN, "e4e669", "Agent has opened a PR for this issue"),
    ]:
        _run(
            ["gh", "label", "create", name, "--color", color, "--description", desc],
            capture=True,
            check=False,
        )


def unclaimed_issues() -> list[dict]:
    result = _run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--sort",
            "created",
            "--json",
            "number,title,body,labels",
            "--limit",
            "50",
        ],
        capture=True,
    )
    issues: list[dict] = json.loads(result.stdout)
    return [
        i for i in issues if not SKIP_LABELS.intersection({label["name"] for label in i["labels"]})
    ]


def claim(issue_number: int) -> bool:
    """Atomically claim an issue by pushing its branch to origin. Returns False if taken."""
    branch = f"agent/issue-{issue_number}"
    result = _run(
        ["git", "push", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=REPO_ROOT,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    _run(["gh", "issue", "edit", str(issue_number), "--add-label", IN_PROGRESS])
    return True


def make_worktree(issue_number: int) -> Path:
    path = REPO_ROOT / ".worktrees" / str(issue_number)
    _run(
        ["git", "worktree", "add", str(path), f"agent/issue-{issue_number}"],
        cwd=REPO_ROOT,
    )
    return path


def render(template: Path, issue: dict, feedback: str = "") -> str:
    text = template.read_text()
    text = text.replace("{{ISSUE_NUMBER}}", str(issue["number"]))
    text = text.replace("{{ISSUE_TITLE}}", issue["title"])
    text = text.replace("{{ISSUE_BODY}}", issue.get("body") or "")
    if feedback:
        text = text.replace("{{#if REVIEWER_FEEDBACK}}", "")
        text = text.replace("{{/if}}", "")
        text = text.replace("{{REVIEWER_FEEDBACK}}", feedback)
    else:
        text = re.sub(r"\{\{#if REVIEWER_FEEDBACK\}\}.*?\{\{/if\}\}", "", text, flags=re.DOTALL)
    return text


def run_implementer(prompt: str, worktree: Path) -> None:
    result = subprocess.run(
        ["claude", "--dangerously-skip-permissions", "-p", prompt],
        cwd=worktree,
        timeout=AGENT_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"implementer exited {result.returncode}")


def run_reviewer(prompt: str, worktree: Path) -> tuple[bool, list[str]]:
    result = subprocess.run(
        ["claude", "--dangerously-skip-permissions", "-p", prompt],
        cwd=worktree,
        capture_output=True,
        text=True,
        timeout=AGENT_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"reviewer exited {result.returncode}:\n{result.stderr}")
    return _parse_review(result.stdout)


def _parse_review(text: str) -> tuple[bool, list[str]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip optional code fence wrapper
        cleaned = "\n".join(cleaned.splitlines()[1:]).rstrip("`").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            raise RuntimeError(f"reviewer output contained no JSON:\n{text}") from None
        data = json.loads(m.group())
    return bool(data.get("approved")), list(data.get("feedback", []))


def open_pr(issue: dict, worktree: Path, cycles: int, approved: bool) -> None:
    number, title = issue["number"], issue["title"]
    _run(["git", "add", "-A"], cwd=worktree)
    if _run(["git", "status", "--porcelain"], cwd=worktree, capture=True).stdout.strip():
        _run(
            [
                "git",
                "commit",
                "-m",
                f"agent: implement #{number} — {title}\n\n"
                f"Completed in {cycles} cycle(s). Approved: {approved}",
            ],
            cwd=worktree,
        )
    _run(["git", "push", "-u", "origin", f"agent/issue-{number}"], cwd=worktree)
    body = [f"Closes #{number}", "", f"Implemented by agent in {cycles} review cycle(s)."]
    if not approved:
        body += ["", "> Max review cycles reached without explicit approval."]
    _run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            f"agent: {title}",
            "--body",
            "\n".join(body),
            "--head",
            f"agent/issue-{number}",
        ]
    )


def process(issue: dict) -> None:
    number = issue["number"]
    print(f"[orchestrator] claiming #{number}: {issue['title']}")
    if not claim(number):
        print(f"[orchestrator] #{number} already claimed, skipping")
        return

    worktree = make_worktree(number)
    feedback = ""
    approved = False
    cycles = 0

    try:
        for cycles in range(1, MAX_CYCLES + 1):
            print(f"[orchestrator] #{number} cycle {cycles}/{MAX_CYCLES}")
            run_implementer(render(PROMPTS_DIR / "implementer.md", issue, feedback), worktree)
            approved, found = run_reviewer(render(PROMPTS_DIR / "reviewer.md", issue), worktree)
            if approved:
                print(f"[orchestrator] #{number} approved on cycle {cycles}")
                break
            feedback = "\n".join(found)
            print(f"[orchestrator] #{number} cycle {cycles} rejected ({len(found)} issues)")

        open_pr(issue, worktree, cycles, approved)
        _run(
            [
                "gh",
                "issue",
                "edit",
                str(number),
                "--remove-label",
                IN_PROGRESS,
                "--add-label",
                PR_OPEN,
            ]
        )
    finally:
        _run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=REPO_ROOT,
            check=False,
        )
        shutil.rmtree(REPO_ROOT / ".agent-work" / str(number), ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="HarnessBuddy issue orchestrator")
    parser.add_argument("--loop", action="store_true", help="Process all unclaimed issues in order")
    args = parser.parse_args()

    setup()
    issues = unclaimed_issues()
    if not issues:
        print("[orchestrator] no unclaimed issues")
        return 0

    targets = issues if args.loop else issues[:1]
    failed = False
    for issue in targets:
        try:
            process(issue)
        except Exception as exc:
            print(f"[orchestrator] ERROR on #{issue['number']}: {exc}", file=sys.stderr)
            failed = True
            if not args.loop:
                return 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
