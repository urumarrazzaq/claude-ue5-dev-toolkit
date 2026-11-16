#!/usr/bin/env python3
"""Task board and safety checks for multi-agent Unreal Engine development.

The *coordinator* is the main Claude Code session (see the ``ue5-orchestrator``
skill). It delegates work to the subagents in ``.claude/agents/`` with Claude
Code's own Agent tool. This script does not run agents: it keeps the shared
task plan honest so the coordinator can decide, with evidence, what may run
next and what must wait.

Usage (``--plan`` defaults to ``.claude/orchestration/plan.json``)::

    python scripts/orchestrator.py init --title "Dash ability" --request "..."
    python scripts/orchestrator.py import-tasks tasks.json
    python scripts/orchestrator.py add --id T1 --agent ue5-cpp-developer ...
    python scripts/orchestrator.py validate [--json]
    python scripts/orchestrator.py preflight --project-dir <ue-project>
    python scripts/orchestrator.py ready [--max 3] [--json]
    python scripts/orchestrator.py brief T1
    python scripts/orchestrator.py start T1 [T2 ...]
    python scripts/orchestrator.py set-status T1 review [--note "..."]
    python scripts/orchestrator.py handoff T1 --summary "..." --files-changed a.cpp
    python scripts/orchestrator.py allow-dirty T1 --note "user approved in chat"
    python scripts/orchestrator.py decide "Use GAS, not a custom component"
    python scripts/orchestrator.py audit --project-dir <ue-project>
    python scripts/orchestrator.py status
    python scripts/orchestrator.py report [--output report.md]

Only the coordinator runs this script; subagents never edit the plan. All
writes go to the plan file (atomically); project files are never modified.
``preflight`` and ``audit`` only run read-only ``git status``.

Exit codes: 0 success, 1 validation failure / refused operation, 2 usage or
I/O error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PLAN_VERSION = 1
DEFAULT_PLAN = Path(".claude") / "orchestration" / "plan.json"
ORCHESTRATION_DIR = ".claude/orchestration/"
TOOLKIT_ROOT = Path(__file__).resolve().parent.parent

# Agent registry. ``writes`` must agree with the agent file's tools (Edit/Write);
# ``validate`` cross-checks this when the agent files are available.
# role: plan | implement | advise | build | review | manual
AGENTS: Dict[str, Dict[str, Any]] = {
    "ue5-architect": {"role": "plan", "writes": False},
    "ue5-cpp-developer": {"role": "implement", "writes": True},
    "ue5-blueprint-specialist": {"role": "advise", "writes": False},
    "ue5-build-engineer": {"role": "build", "writes": False},
    "ue5-code-reviewer": {"role": "review", "writes": False},
    # Steps only a human can do (editor work on .uasset/.umap, approvals).
    "user": {"role": "manual", "writes": False},
}

STATUSES = ("pending", "in_progress", "blocked", "review", "completed", "failed")
TRANSITIONS: Dict[str, Set[str]] = {
    "pending": {"in_progress", "blocked", "failed"},
    "in_progress": {"review", "completed", "blocked", "failed"},
    "blocked": {"pending", "in_progress", "failed"},
    "review": {"completed", "in_progress", "failed"},
    "failed": {"pending"},
    "completed": set(),
}
NOTE_REQUIRED = {"blocked", "failed"}
PRIORITIES = {"critical": 0, "high": 1, "medium": 2, "low": 3}
VERDICTS = {"approve", "approve_with_comments", "changes_requested"}
APPROVING = {"approve", "approve_with_comments"}

TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
REQUIRED_FIELDS = ("id", "description", "agent", "priority", "depends_on", "status",
                   "expected_output", "validation")
OPTIONAL_LIST_FIELDS = ("files", "context_files")
KNOWN_FIELDS = set(REQUIRED_FIELDS) | set(OPTIONAL_LIST_FIELDS) | {
    "allow_dirty", "history", "handoff", "previous_handoffs", "notes", "attempts"}
GENERATED_DIRS = {"binaries", "intermediate", "deriveddatacache", "saved"}
BINARY_EXTS = (".uasset", ".umap")


class OrchestratorError(Exception):
    """A refused operation; the message explains why."""


# --------------------------------------------------------------------------- helpers

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_path(path: str) -> str:
    """Normalise a project-relative path for comparison (case-insensitive, '/')."""
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = re.sub(r"/{2,}", "/", p)
    return p.casefold()


def paths_overlap(a: str, b: str) -> bool:
    """Equal paths, or one is a directory prefix (ending in '/') of the other."""
    a, b = norm_path(a), norm_path(b)
    if a == b:
        return True
    return (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))


def path_problem(path: str) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return "empty path"
    p = path.strip().replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return "must be relative to the project root"
    parts = [s for s in p.split("/") if s]
    if ".." in parts:
        return "must not contain '..'"
    if any(s.casefold() in GENERATED_DIRS for s in parts):
        return "is inside a generated folder (Binaries/Intermediate/Saved/DerivedDataCache)"
    if p.casefold().endswith(BINARY_EXTS):
        return "is a binary asset - assign editor work to 'user' and describe it instead"
    return None


def task_role(task: Dict[str, Any]) -> str:
    return AGENTS.get(task.get("agent", ""), {}).get("role", "unknown")


def is_writer(task: Dict[str, Any]) -> bool:
    return bool(AGENTS.get(task.get("agent", ""), {}).get("writes"))


# --------------------------------------------------------------------------- plan I/O

def new_plan(title: str, request: str = "") -> Dict[str, Any]:
    stamp = now()
    return {"version": PLAN_VERSION, "title": title, "request": request,
            "created_at": stamp, "updated_at": stamp, "seq": 0,
            "git_baseline": None, "decisions": [], "tasks": []}


def load_plan(path: Path) -> Dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise OrchestratorError(f"plan not found: {path} (run 'init' first)") from None
    except ValueError as exc:
        raise OrchestratorError(f"plan is not valid JSON: {path} ({exc})") from None
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
        raise OrchestratorError(f"plan has no 'tasks' list: {path}")
    return plan


def save_plan(path: Path, plan: Dict[str, Any]) -> None:
    plan["updated_at"] = now()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".plan-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def next_seq(plan: Dict[str, Any]) -> int:
    plan["seq"] = int(plan.get("seq", 0)) + 1
    return plan["seq"]


def task_map(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {t["id"]: t for t in plan["tasks"] if isinstance(t, dict) and isinstance(t.get("id"), str)}


def get_task(plan: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    task = task_map(plan).get(task_id)
    if task is None:
        raise OrchestratorError(f"unknown task id {task_id!r}")
    return task


def normalise_task(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fill defaults for a task coming from the architect or the CLI."""
    task = dict(raw)
    task.setdefault("status", "pending")
    task.setdefault("depends_on", [])
    task.setdefault("priority", "medium")
    for key in OPTIONAL_LIST_FIELDS:
        task.setdefault(key, [])
    if isinstance(task.get("validation"), str):
        task["validation"] = [task["validation"]]
    task.setdefault("history", [])
    return task


# --------------------------------------------------------------------------- graph

def find_cycle(tasks: Dict[str, Dict[str, Any]]) -> Optional[List[str]]:
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {tid: WHITE for tid in tasks}
    stack: List[str] = []

    def visit(tid: str) -> Optional[List[str]]:
        colour[tid] = GREY
        stack.append(tid)
        for dep in tasks[tid].get("depends_on", []) or []:
            if dep not in tasks:
                continue
            if colour[dep] == GREY:
                return stack[stack.index(dep):] + [dep]
            if colour[dep] == WHITE:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        colour[tid] = BLACK
        return None

    for tid in sorted(tasks):
        if colour[tid] == WHITE:
            found = visit(tid)
            if found:
                return found
    return None


def ancestors(tasks: Dict[str, Dict[str, Any]]) -> Dict[str, Set[str]]:
    """Transitive prerequisites of every task (graph must be acyclic)."""
    memo: Dict[str, Set[str]] = {}

    def walk(tid: str) -> Set[str]:
        if tid in memo:
            return memo[tid]
        result: Set[str] = set()
        for dep in tasks[tid].get("depends_on", []) or []:
            if dep in tasks:
                result.add(dep)
                result |= walk(dep)
        memo[tid] = result
        return result

    for tid in tasks:
        walk(tid)
    return memo


def dependency_satisfied(task: Dict[str, Any], dep: Dict[str, Any]) -> bool:
    """'review' means the prerequisite's output exists and awaits review, so
    downstream work may build on it (a single review then covers the chain);
    only finalising a code task ('completed') needs an approving review.
    Manual 'user' steps wait for fully completed prerequisites."""
    if dep.get("status") == "completed":
        return True
    return dep.get("status") == "review" and task_role(task) != "manual"


def unmet_dependencies(plan: Dict[str, Any], task: Dict[str, Any]) -> List[str]:
    tasks = task_map(plan)
    return [d for d in task.get("depends_on", []) or []
            if d not in tasks or not dependency_satisfied(task, tasks[d])]


def failed_prerequisites(plan: Dict[str, Any], task: Dict[str, Any]) -> List[str]:
    tasks = task_map(plan)
    anc = ancestors(tasks) if find_cycle(tasks) is None else {}
    return sorted(a for a in anc.get(task["id"], set()) if tasks[a].get("status") == "failed")


# --------------------------------------------------------------------------- validation

def agent_tool_mismatches(agents_dir: Optional[Path], used: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Cross-check the registry against .claude/agents/<name>.md when present."""
    errors: List[str] = []
    warnings: List[str] = []
    if agents_dir is None or not agents_dir.is_dir():
        return errors, warnings
    for name in sorted(set(used)):
        if name not in AGENTS or AGENTS[name]["role"] == "manual":
            continue
        path = agents_dir / f"{name}.md"
        if not path.is_file():
            errors.append(f"agent {name!r} has no definition at {path.as_posix()}")
            continue
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^tools:\s*(.+)$", text, re.MULTILINE)
        if not match:
            warnings.append(f"agent {name!r} inherits all tools; cannot verify read-only/write role")
            continue
        tools = {t.strip().split("(")[0] for t in match.group(1).split(",")}
        can_write = bool(tools & {"Edit", "Write", "MultiEdit", "NotebookEdit"})
        if can_write != AGENTS[name]["writes"]:
            kind = "can write files" if can_write else "is read-only"
            errors.append(f"agent {name!r} {kind} in {path.name} but the registry says otherwise")
    return errors, warnings


def validate_plan(plan: Dict[str, Any], agents_dir: Optional[Path] = None) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). An empty error list means the plan is safe to execute."""
    errors: List[str] = []
    warnings: List[str] = []
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list):
        return ["plan has no 'tasks' list"], warnings

    seen: Dict[str, int] = {}
    good: Dict[str, Dict[str, Any]] = {}
    for index, task in enumerate(raw_tasks):
        where = f"task #{index + 1}"
        if not isinstance(task, dict):
            errors.append(f"{where}: must be an object")
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not TASK_ID_RE.match(tid):
            errors.append(f"{where}: 'id' must match {TASK_ID_RE.pattern}")
            continue
        where = f"task {tid}"
        if tid in seen:
            errors.append(f"{where}: duplicate task id")
            continue
        seen[tid] = index
        task_errors = len(errors)
        for key in REQUIRED_FIELDS:
            if key not in task:
                errors.append(f"{where}: missing required field '{key}'")
        unknown = sorted(set(task) - KNOWN_FIELDS)
        if unknown:
            warnings.append(f"{where}: unknown fields {unknown}")
        for key in ("description", "expected_output"):
            if key in task and (not isinstance(task[key], str) or not task[key].strip()):
                errors.append(f"{where}: '{key}' must be a non-empty string")
        if "agent" in task and task["agent"] not in AGENTS:
            errors.append(f"{where}: unknown agent {task['agent']!r} (known: {', '.join(sorted(AGENTS))})")
        if "priority" in task and task["priority"] not in PRIORITIES:
            errors.append(f"{where}: priority must be one of {sorted(PRIORITIES, key=PRIORITIES.get)}")
        if "status" in task and task["status"] not in STATUSES:
            errors.append(f"{where}: status must be one of {list(STATUSES)}")
        val = task.get("validation")
        if "validation" in task and (not isinstance(val, list) or not val
                                     or not all(isinstance(v, str) and v.strip() for v in val)):
            errors.append(f"{where}: 'validation' must be a non-empty list of strings")
        deps = task.get("depends_on")
        if "depends_on" in task and (not isinstance(deps, list) or not all(isinstance(d, str) for d in deps)):
            errors.append(f"{where}: 'depends_on' must be a list of task ids")
        for key in OPTIONAL_LIST_FIELDS:
            value = task.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(f"{where}: '{key}' must be a list of strings")
                continue
            for path in value:
                problem = path_problem(path)
                if problem and key == "files":
                    errors.append(f"{where}: owned file {path!r} {problem}")
                elif problem and problem.startswith(("must", "empty")):
                    errors.append(f"{where}: context file {path!r} {problem}")
            normed = [norm_path(v) for v in value]
            if len(set(normed)) != len(normed):
                warnings.append(f"{where}: duplicate entries in '{key}'")
        if task.get("agent") in AGENTS and isinstance(task.get("files", []), list):
            if is_writer(task) and not task.get("files"):
                errors.append(f"{where}: {task['agent']} modifies code, so it must declare owned 'files'")
            if not is_writer(task) and task.get("files"):
                errors.append(f"{where}: {task['agent']} is read-only and cannot own files "
                              f"(use 'context_files' for files it reads)")
        if len(errors) == task_errors:
            good[tid] = task

    # Dependencies
    for tid, task in good.items():
        for dep in task.get("depends_on", []):
            if dep == tid:
                errors.append(f"task {tid}: depends on itself")
            elif dep not in seen:
                errors.append(f"task {tid}: depends on unknown task {dep!r}")
    graph = {tid: t for tid, t in good.items()}
    cycle = find_cycle(graph)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))
        return errors, warnings

    anc = ancestors(graph)

    def ordered(a: str, b: str) -> bool:
        return a in anc[b] or b in anc[a]

    # File ownership: overlapping writers must be ordered by dependencies.
    ids = sorted(graph)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ta, tb = graph[a], graph[b]
            shared = sorted({fa for fa in ta.get("files", []) for fb in tb.get("files", [])
                             if paths_overlap(fa, fb)})
            if shared and not ordered(a, b):
                errors.append(f"file ownership conflict: tasks {a} and {b} both modify {shared} "
                              f"but neither depends on the other, so they could run concurrently")

    # Status consistency
    for tid, task in graph.items():
        status = task.get("status")
        if status in ("in_progress", "review", "completed"):
            unmet = [d for d in task.get("depends_on", [])
                     if d in graph and not dependency_satisfied(task, graph[d])]
            reopened = [d for d in unmet if last_seq_of(graph[d], "review") or
                        last_seq_of(graph[d], "completed")]
            never_done = [d for d in unmet if d not in reopened]
            if never_done:
                errors.append(f"task {tid}: status '{status}' but prerequisites never delivered: "
                              f"{never_done}")
            if reopened:
                warnings.append(f"task {tid}: prerequisites {reopened} were reopened for rework; "
                                f"re-check {tid} against the reworked code")
        if status == "completed" and is_writer(task) and not approving_reviews(plan, task):
            errors.append(f"task {tid}: completed without an approving review")
        if status == "review" and not is_writer(task):
            warnings.append(f"task {tid}: read-only tasks normally go straight to 'completed'")

    # Review coverage: every code change needs a reviewer task downstream.
    for tid, task in graph.items():
        if is_writer(task):
            reviewers = [r for r, rt in graph.items() if task_role(rt) == "review" and tid in anc[r]]
            if not reviewers:
                warnings.append(f"task {tid}: no ue5-code-reviewer task depends on it; it cannot be "
                                f"completed until one is added")

    agent_errors, agent_warnings = agent_tool_mismatches(
        agents_dir, (t.get("agent") for t in graph.values()))
    return errors + agent_errors, warnings + agent_warnings


# --------------------------------------------------------------------------- transitions

def last_seq_of(task: Dict[str, Any], status: str) -> int:
    seqs = [h.get("seq", 0) for h in task.get("history", []) if h.get("status") == status]
    return max(seqs) if seqs else 0


def approving_reviews(plan: Dict[str, Any], task: Dict[str, Any]) -> List[str]:
    """Completed reviewer tasks that depend on ``task``, approved it, and finished
    after the task last entered 'review'."""
    tasks = task_map(plan)
    entered = last_seq_of(task, "review")
    result = []
    for rid, rt in tasks.items():
        if task_role(rt) != "review" or task["id"] not in (rt.get("depends_on") or []):
            continue
        verdict = (rt.get("handoff") or {}).get("verdict")
        if rt.get("status") == "completed" and verdict in APPROVING \
                and last_seq_of(rt, "completed") > entered:
            result.append(rid)
    return result


def check_transition(plan: Dict[str, Any], task: Dict[str, Any], new_status: str,
                     note: str = "") -> None:
    """Raise OrchestratorError if moving ``task`` to ``new_status`` is unsafe."""
    old = task.get("status")
    if new_status not in STATUSES:
        raise OrchestratorError(f"unknown status {new_status!r}")
    if new_status not in TRANSITIONS.get(old, set()):
        allowed = sorted(TRANSITIONS.get(old, set())) or ["(none - terminal)"]
        raise OrchestratorError(f"task {task['id']}: cannot go from '{old}' to '{new_status}' "
                                f"(allowed: {', '.join(allowed)})")
    if new_status in NOTE_REQUIRED and not note.strip():
        raise OrchestratorError(f"task {task['id']}: moving to '{new_status}' requires --note "
                                f"explaining the reason")
    if new_status == "in_progress":
        unmet = unmet_dependencies(plan, task)
        if unmet:
            raise OrchestratorError(f"task {task['id']}: prerequisites not done: {unmet}")
        conflicts = active_conflicts(plan, task)
        if conflicts:
            raise OrchestratorError(f"task {task['id']}: " + "; ".join(conflicts))
        dirty = dirty_owned_files(plan, task)
        if dirty and not task.get("allow_dirty"):
            raise OrchestratorError(
                f"task {task['id']}: owned files have uncommitted user changes {dirty}. Ask the "
                f"user, then run 'allow-dirty {task['id']}' only if they agree")
    if new_status == "completed" and is_writer(task):
        if old != "review":
            raise OrchestratorError(f"task {task['id']}: code changes must pass through 'review' "
                                    f"before 'completed'")
        if not approving_reviews(plan, task):
            raise OrchestratorError(f"task {task['id']}: no completed ue5-code-reviewer task with "
                                    f"verdict approve/approve_with_comments since it entered review")
    if new_status == "completed" and task_role(task) == "review":
        verdict = (task.get("handoff") or {}).get("verdict")
        if verdict not in VERDICTS:
            raise OrchestratorError(f"task {task['id']}: record the review with 'handoff "
                                    f"{task['id']} --verdict ...' before completing it")


def apply_transition(plan: Dict[str, Any], task_id: str, new_status: str, note: str = "") -> Dict[str, Any]:
    task = get_task(plan, task_id)
    check_transition(plan, task, new_status, note)
    old = task["status"]
    if old == "failed" and new_status == "pending":
        task["attempts"] = int(task.get("attempts", 1)) + 1
    elif new_status == "in_progress" and "attempts" not in task:
        task["attempts"] = 1
    task["status"] = new_status
    entry = {"seq": next_seq(plan), "at": now(), "from": old, "status": new_status}
    if note.strip():
        entry["note"] = note.strip()
    task.setdefault("history", []).append(entry)
    return task


def active_conflicts(plan: Dict[str, Any], task: Dict[str, Any],
                     extra_active: Iterable[Dict[str, Any]] = ()) -> List[str]:
    """Reasons ``task`` must not start while the given/active tasks run."""
    reasons = []
    active = [t for t in plan["tasks"] if t.get("status") == "in_progress" and t["id"] != task["id"]]
    active += [t for t in extra_active if t["id"] != task["id"]]
    for other in active:
        shared = sorted({f for f in task.get("files", []) for g in other.get("files", [])
                         if paths_overlap(f, g)})
        if shared:
            reasons.append(f"{other['id']} is modifying {shared}")
        if task_role(task) == "build" and is_writer(other):
            reasons.append(f"build validation must not run while {other['id']} is editing code")
        if is_writer(task) and task_role(other) == "build":
            reasons.append(f"{other['id']} is compiling; wait before editing code")
    return reasons


# --------------------------------------------------------------------------- git safety

def git_dirty_files(project_dir: Path) -> List[str]:
    """Paths with uncommitted changes (read-only 'git status')."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_dir), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            capture_output=True, check=False)
    except FileNotFoundError:
        raise OrchestratorError("git is not installed; cannot protect uncommitted changes") from None
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()
        raise OrchestratorError(f"'{project_dir}' is not a usable git repository: {msg}")
    entries = proc.stdout.decode("utf-8", "replace").split("\0")
    files: List[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        files.append(path)
        if "R" in code or "C" in code:  # rename/copy: next entry is the source path
            if i < len(entries) and entries[i]:
                files.append(entries[i])
            i += 1
    return sorted(set(files))


def dirty_owned_files(plan: Dict[str, Any], task: Dict[str, Any]) -> List[str]:
    baseline = plan.get("git_baseline") or {}
    dirty = baseline.get("dirty", [])
    return sorted({d for d in dirty for f in task.get("files", []) if paths_overlap(d, f)})


# --------------------------------------------------------------------------- scheduling

def ready_batch(plan: Dict[str, Any], max_parallel: int = 3) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """Pick pending tasks that can start now without conflicting with each other or
    with running tasks. Returns (batch, [(task_id, reason_waiting)])."""
    batch: List[Dict[str, Any]] = []
    waiting: List[Tuple[str, str]] = []
    pending = [t for t in plan["tasks"] if t.get("status") == "pending"]
    pending.sort(key=lambda t: (PRIORITIES.get(t.get("priority"), 9), t["id"]))
    for task in pending:
        failed = failed_prerequisites(plan, task)
        if failed:
            waiting.append((task["id"], f"prerequisite failed: {failed}"))
            continue
        unmet = unmet_dependencies(plan, task)
        if unmet:
            waiting.append((task["id"], f"waiting for {unmet}"))
            continue
        dirty = dirty_owned_files(plan, task)
        if dirty and not task.get("allow_dirty"):
            waiting.append((task["id"], f"needs user approval: uncommitted changes in {dirty}"))
            continue
        conflicts = active_conflicts(plan, task, batch)
        if conflicts:
            waiting.append((task["id"], "; ".join(conflicts)))
            continue
        if len(batch) >= max_parallel:
            waiting.append((task["id"], f"parallel limit {max_parallel} reached"))
            continue
        batch.append(task)
    return batch, waiting


# --------------------------------------------------------------------------- briefs, handoffs, reports

def build_brief(plan: Dict[str, Any], task_id: str) -> str:
    """Focused prompt for the subagent: only this task, its files and upstream summaries."""
    task = get_task(plan, task_id)
    tasks = task_map(plan)
    lines = [f"# Task {task['id']} ({task['agent']})", "",
             f"Feature: {plan.get('title', '')}", "", "## Goal", task["description"], "",
             "## Expected output", task["expected_output"], "", "## Validation required"]
    lines += [f"- {v}" for v in task.get("validation", [])]
    if task.get("files"):
        lines += ["", "## Files you own (modify ONLY these)"]
        lines += [f"- {f}" for f in task["files"]]
    if task.get("context_files"):
        lines += ["", "## Read these first (do not re-explore the whole project)"]
        lines += [f"- {f}" for f in task["context_files"]]
    upstream = [tasks[d] for d in task.get("depends_on", []) if d in tasks]
    if upstream:
        lines += ["", "## Upstream handoffs"]
        for dep in upstream:
            handoff = dep.get("handoff") or {}
            lines.append(f"### {dep['id']} ({dep['agent']}, {dep['status']})")
            lines.append(handoff.get("summary", "(no handoff recorded)"))
            if handoff.get("files_changed"):
                lines.append("Files changed: " + ", ".join(handoff["files_changed"]))
            for issue in handoff.get("issues", []):
                lines.append(f"- Issue: {issue}")
    if plan.get("decisions"):
        lines += ["", "## Decisions already made (do not reopen)"]
        lines += [f"- {d['text']}" for d in plan["decisions"]]
    template: Dict[str, Any] = {
        "task": task["id"], "status": "review|completed|blocked|failed", "summary": "...",
        "files_changed": [], "decisions": [], "issues": [],
        "validation_result": "what was actually run and its result, or 'not run'", "followups": []}
    if task_role(task) == "review":
        template["verdict"] = "approve|approve_with_comments|changes_requested"
    lines += ["", "## Rules",
              "- Stay inside this task. Do not edit files you do not own; report needed changes instead.",
              "- Do not run git commands that modify the repository (commit, checkout, reset, stash, merge).",
              "- Do not touch .uasset/.umap or generated folders.",
              "- Unverified engine APIs must be labelled as unverified.",
              "- End with the handoff block below (JSON), filled in honestly.", "",
              "```json",
              json.dumps(template, indent=2),
              "```"]
    return "\n".join(lines) + "\n"


def record_handoff(plan: Dict[str, Any], task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    task = get_task(plan, task_id)
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise OrchestratorError("handoff needs a non-empty 'summary'")
    handoff: Dict[str, Any] = {"summary": summary.strip(), "at": now()}
    for key in ("files_changed", "decisions", "issues", "followups"):
        value = data.get(key) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise OrchestratorError(f"handoff '{key}' must be a list of strings")
        handoff[key] = value
    if data.get("validation_result"):
        handoff["validation_result"] = str(data["validation_result"])
    verdict = data.get("verdict")
    if verdict is not None:
        if task_role(task) != "review":
            raise OrchestratorError(f"task {task_id}: only reviewer tasks carry a verdict")
        if verdict not in VERDICTS:
            raise OrchestratorError(f"verdict must be one of {sorted(VERDICTS)}")
        handoff["verdict"] = verdict
    outside = sorted(f for f in handoff["files_changed"]
                     if not any(paths_overlap(f, own) for own in task.get("files", [])))
    if outside:
        handoff["ownership_violations"] = outside
    if task.get("handoff"):
        task.setdefault("previous_handoffs", []).append(task["handoff"])
    task["handoff"] = handoff
    for text in handoff["decisions"]:
        plan.setdefault("decisions", []).append({"at": handoff["at"], "task": task_id, "text": text})
    return handoff


def audit_changes(plan: Dict[str, Any], changed: List[str]) -> List[str]:
    """Problems with files changed since the baseline (changed = current dirty files)."""
    baseline = {norm_path(p) for p in (plan.get("git_baseline") or {}).get("dirty", [])}
    problems = []
    for path in changed:
        if norm_path(path) in baseline:
            continue  # user's pre-existing change; not ours
        if norm_path(path).startswith(ORCHESTRATION_DIR):
            continue  # the task board's own files
        owners = [t for t in plan["tasks"] if any(paths_overlap(path, f) for f in t.get("files", []))]
        if not owners:
            problems.append(f"{path}: changed but no task owns it")
        elif all(t.get("status") == "pending" for t in owners):
            problems.append(f"{path}: changed but its owner {[t['id'] for t in owners]} has not started")
    for task in plan["tasks"]:
        for path in (task.get("handoff") or {}).get("ownership_violations", []):
            problems.append(f"{path}: reported changed by {task['id']}, which does not own it")
    return problems


def status_table(plan: Dict[str, Any]) -> str:
    rows = ["| ID | Agent | Priority | Status | Depends on | Owned files |",
            "|---|---|---|---|---|---|"]
    for t in plan["tasks"]:
        rows.append(f"| {t.get('id')} | {t.get('agent')} | {t.get('priority')} | {t.get('status')} | "
                    f"{', '.join(t.get('depends_on', [])) or '-'} | {', '.join(t.get('files', [])) or '-'} |")
    counts = {s: sum(1 for t in plan["tasks"] if t.get("status") == s) for s in STATUSES}
    summary = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    return "\n".join(rows) + f"\n\n{summary or 'no tasks'}\n"


def build_report(plan: Dict[str, Any]) -> str:
    lines = [f"# Orchestration report: {plan.get('title', '')}", ""]
    if plan.get("request"):
        lines += ["## Request", plan["request"], ""]
    lines += ["## Tasks", status_table(plan)]
    changed: List[str] = []
    lines.append("## Handoffs")
    for t in plan["tasks"]:
        h = t.get("handoff")
        if not h:
            continue
        lines.append(f"### {t['id']} - {t['agent']} ({t['status']})")
        lines.append(h["summary"])
        if h.get("verdict"):
            lines.append(f"- Verdict: **{h['verdict']}**")
        if h.get("files_changed"):
            lines.append("- Files changed: " + ", ".join(h["files_changed"]))
        for earlier in [*t.get("previous_handoffs", []), h]:
            changed += earlier.get("files_changed", [])
        if t.get("previous_handoffs"):
            lines.append(f"- Earlier handoffs: {len(t['previous_handoffs'])} (rework)")
        if h.get("validation_result"):
            lines.append(f"- Validation: {h['validation_result']}")
        for issue in h.get("issues", []):
            lines.append(f"- Issue: {issue}")
        for v in h.get("ownership_violations", []):
            lines.append(f"- OWNERSHIP VIOLATION: {v}")
        lines.append("")
    if plan.get("decisions"):
        lines += ["## Decisions"] + [f"- {d['text']}" + (f" ({d['task']})" if d.get("task") else "")
                                      for d in plan["decisions"]] + [""]
    open_items = []
    for t in plan["tasks"]:
        if t.get("status") != "completed":
            last = (t.get("history") or [{}])[-1]
            open_items.append(f"- {t['id']} is {t.get('status')}" +
                              (f": {last['note']}" if last.get("note") else ""))
        for f in (t.get("handoff") or {}).get("followups", []):
            open_items.append(f"- Follow-up from {t['id']}: {f}")
    changed_lines = [f"- {f}" for f in sorted(set(changed))] or ["- none recorded"]
    lines += ["## Files changed", *changed_lines, ""]
    lines += ["## Open items", *(open_items or ["- none"]), ""]
    lines.append("## Build validation")
    builds = [t for t in plan["tasks"] if task_role(t) == "build"]
    if not builds:
        lines.append("- Not requested (no build task in the plan): code is NOT compiled.")
    tasks = task_map(plan)
    anc = ancestors(tasks) if find_cycle(tasks) is None else {}
    for b in builds:
        result = (b.get("handoff") or {}).get("validation_result", "no result recorded")
        lines.append(f"- {b['id']} ({b['status']}): {result}")
        done = last_seq_of(b, "completed")
        stale = sorted(a for a in anc.get(b["id"], set())
                       if is_writer(tasks[a]) and done and last_seq_of(tasks[a], "review") > done)
        if stale:
            lines.append(f"  - STALE: {stale} changed after this build validation; re-run it")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI

def _plan_path(args: argparse.Namespace) -> Path:
    return Path(args.plan)


def _agents_dir(args: argparse.Namespace) -> Optional[Path]:
    if getattr(args, "agents_dir", None):
        return Path(args.agents_dir)
    for candidate in (Path(".claude") / "agents", TOOLKIT_ROOT / ".claude" / "agents"):
        if candidate.is_dir():
            return candidate
    return None


def _save_if_valid(path: Path, plan: Dict[str, Any], args: argparse.Namespace) -> None:
    errors, warnings = validate_plan(plan, _agents_dir(args))
    if errors:
        raise OrchestratorError("refusing to save an invalid plan:\n  - " + "\n  - ".join(errors))
    for w in warnings:
        print(f"WARN  {w}")
    save_plan(path, plan)


def cmd_init(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    if path.exists() and not args.force:
        raise OrchestratorError(f"{path} already exists (use --force to replace it)")
    save_plan(path, new_plan(args.title, args.request or ""))
    print(f"Created {path}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise OrchestratorError(f"cannot read tasks file {args.file}: {exc}") from None
    raw = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise OrchestratorError("tasks file must be a list of tasks or an object with 'tasks'")
    for t in raw:
        if not isinstance(t, dict):
            raise OrchestratorError("every task must be an object")
        if t.get("status", "pending") != "pending":
            raise OrchestratorError(f"imported task {t.get('id')} must start as 'pending'")
    plan["tasks"].extend(normalise_task(t) for t in raw)
    if isinstance(data, dict):
        for text in data.get("decisions", []) or []:
            plan.setdefault("decisions", []).append({"at": now(), "task": "plan", "text": str(text)})
    _save_if_valid(path, plan, args)
    print(f"Imported {len(raw)} task(s) into {path}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    task = normalise_task({
        "id": args.id, "description": args.description, "agent": args.agent,
        "priority": args.priority, "depends_on": args.depends_on or [],
        "expected_output": args.expected_output, "validation": args.validation,
        "files": args.files or [], "context_files": args.context_files or []})
    plan["tasks"].append(task)
    _save_if_valid(path, plan, args)
    print(f"Added task {args.id}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    plan = load_plan(_plan_path(args))
    errors, warnings = validate_plan(plan, _agents_dir(args))
    if args.json:
        print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings}, indent=2))
    else:
        for e in errors:
            print(f"ERROR {e}")
        for w in warnings:
            print(f"WARN  {w}")
        print(f"Plan {'VALID' if not errors else 'INVALID'}: {len(plan['tasks'])} task(s), "
              f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 0 if not errors else 1


def cmd_preflight(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    dirty = git_dirty_files(Path(args.project_dir))
    if plan.get("git_baseline") and not args.refresh:
        print(f"Baseline already recorded at {plan['git_baseline']['captured_at']} "
              f"({len(plan['git_baseline']['dirty'])} dirty file(s)); use --refresh to replace it")
    else:
        if args.refresh and any(t.get("status") != "pending" for t in plan["tasks"]):
            raise OrchestratorError("cannot refresh the baseline after tasks started: agent edits "
                                    "would be mistaken for user changes")
        plan["git_baseline"] = {"captured_at": now(), "dirty": dirty}
        save_plan(path, plan)
        print(f"Recorded git baseline: {len(dirty)} file(s) with uncommitted changes")
    blocked = [(t["id"], dirty_owned_files(plan, t)) for t in plan["tasks"]]
    blocked = [(tid, files) for tid, files in blocked if files]
    for tid, files in blocked:
        flag = " (approved)" if get_task(plan, tid).get("allow_dirty") else ""
        print(f"ATTENTION {tid} owns files with uncommitted user changes: {files}{flag}")
    if not blocked:
        print("No owned file has uncommitted user changes.")
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    plan = load_plan(_plan_path(args))
    errors, _ = validate_plan(plan, _agents_dir(args))
    if errors:
        raise OrchestratorError("plan is invalid; run 'validate' and fix it first")
    batch, waiting = ready_batch(plan, args.max)
    if args.json:
        print(json.dumps({"ready": [t["id"] for t in batch],
                          "waiting": [{"id": i, "reason": r} for i, r in waiting]}, indent=2))
        return 0
    if batch:
        print("Ready to start together (no shared files, prerequisites done):")
        for t in batch:
            print(f"  {t['id']:<8} {t['agent']:<26} {t['priority']:<8} {t['description']}")
    else:
        print("Nothing is ready to start.")
    for tid, reason in waiting:
        print(f"  waiting  {tid}: {reason}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    errors, _ = validate_plan(plan, _agents_dir(args))
    if errors:
        raise OrchestratorError("plan is invalid; run 'validate' and fix it first")
    started: List[Dict[str, Any]] = []
    for tid in args.ids:
        task = get_task(plan, tid)
        clash = active_conflicts(plan, task, started)
        if clash:
            raise OrchestratorError(f"task {tid} cannot start with the others: " + "; ".join(clash))
        apply_transition(plan, tid, "in_progress", args.note or "")
        started.append(task)
    save_plan(path, plan)
    print(f"In progress: {', '.join(args.ids)}")
    return 0


def cmd_set_status(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    task = apply_transition(plan, args.id, args.status, args.note or "")
    save_plan(path, plan)
    print(f"{args.id}: {task['history'][-1]['from']} -> {args.status}")
    if args.status == "failed":
        dependents = [t["id"] for t in plan["tasks"] if args.id in failed_prerequisites(plan, t)]
        if dependents:
            print(f"Tasks that cannot proceed until {args.id} is retried or re-planned: {dependents}")
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    if args.json_file:
        try:
            data = json.loads(Path(args.json_file).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise OrchestratorError(f"cannot read handoff file: {exc}") from None
        if not isinstance(data, dict):
            raise OrchestratorError("handoff file must contain a JSON object")
    else:
        data = {"summary": args.summary, "files_changed": args.files_changed or [],
                "decisions": args.decision or [], "issues": args.issue or [],
                "followups": args.followup or [], "validation_result": args.validation_result,
                "verdict": args.verdict}
    handoff = record_handoff(plan, args.id, data)
    save_plan(path, plan)
    print(f"Recorded handoff for {args.id}")
    for v in handoff.get("ownership_violations", []):
        print(f"WARN  {args.id} reports changing {v}, which it does not own - review before accepting")
    return 0


def cmd_allow_dirty(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    task = get_task(plan, args.id)
    task["allow_dirty"] = True
    task.setdefault("history", []).append({"seq": next_seq(plan), "at": now(), "from": task["status"],
                                           "status": task["status"], "note": f"allow_dirty: {args.note}"})
    save_plan(path, plan)
    print(f"{args.id} may now edit files with uncommitted user changes ({args.note})")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    path = _plan_path(args)
    plan = load_plan(path)
    plan.setdefault("decisions", []).append({"at": now(), "task": args.task or "coordinator",
                                             "text": args.text})
    save_plan(path, plan)
    print(f"Recorded decision #{len(plan['decisions'])}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    plan = load_plan(_plan_path(args))
    if plan.get("git_baseline") is None:
        raise OrchestratorError("no git baseline; run 'preflight' before agents start editing")
    problems = audit_changes(plan, git_dirty_files(Path(args.project_dir)))
    for p in problems:
        print(f"PROBLEM {p}")
    print(f"Audit {'clean' if not problems else 'found ' + str(len(problems)) + ' problem(s)'}")
    return 0 if not problems else 1


def cmd_brief(args: argparse.Namespace) -> int:
    print(build_brief(load_plan(_plan_path(args)), args.id), end="")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(status_table(load_plan(_plan_path(args))), end="")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    report = build_report(load_plan(_plan_path(args)))
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(report, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plan", default=str(DEFAULT_PLAN), help="plan file (default: %(default)s)")
    parser.add_argument("--agents-dir", help="directory with agent .md files (default: auto-detect)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create an empty plan")
    p.add_argument("--title", required=True)
    p.add_argument("--request", help="the original development request")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("import-tasks", help="append tasks from the architect's JSON")
    p.add_argument("file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("add", help="append one task")
    p.add_argument("--id", required=True)
    p.add_argument("--agent", required=True, choices=sorted(AGENTS))
    p.add_argument("--description", required=True)
    p.add_argument("--priority", default="medium", choices=list(PRIORITIES))
    p.add_argument("--depends-on", nargs="*")
    p.add_argument("--files", nargs="*", help="files the task owns (writers only)")
    p.add_argument("--context-files", nargs="*")
    p.add_argument("--expected-output", required=True)
    p.add_argument("--validation", nargs="+", required=True)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("validate", help="check the plan for errors")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("preflight", help="record uncommitted user changes (read-only git status)")
    p.add_argument("--project-dir", default=".")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("ready", help="list tasks that can start now, together")
    p.add_argument("--max", type=int, default=3, help="max tasks to start together")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ready)

    p = sub.add_parser("brief", help="print the focused prompt for a task's subagent")
    p.add_argument("id")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("start", help="mark tasks in_progress (all-or-nothing safety checks)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--note")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("set-status", help="move a task to a new status")
    p.add_argument("id")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--note")
    p.set_defaults(func=cmd_set_status)

    p = sub.add_parser("handoff", help="record a subagent's handoff report")
    p.add_argument("id")
    p.add_argument("--json-file", help="handoff JSON produced by the subagent")
    p.add_argument("--summary")
    p.add_argument("--files-changed", nargs="*")
    p.add_argument("--decision", action="append")
    p.add_argument("--issue", action="append")
    p.add_argument("--followup", action="append")
    p.add_argument("--validation-result")
    p.add_argument("--verdict", choices=sorted(VERDICTS))
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("allow-dirty", help="after the user agrees, let a task edit files they changed")
    p.add_argument("id")
    p.add_argument("--note", required=True, help="record of the user's approval")
    p.set_defaults(func=cmd_allow_dirty)

    p = sub.add_parser("decide", help="record a decision that later tasks must respect")
    p.add_argument("text")
    p.add_argument("--task")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("audit", help="compare git changes with file ownership")
    p.add_argument("--project-dir", default=".")
    p.set_defaults(func=cmd_audit)

    sub.add_parser("status", help="print the task table").set_defaults(func=cmd_status)

    p = sub.add_parser("report", help="final Markdown report")
    p.add_argument("--output")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OrchestratorError as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
