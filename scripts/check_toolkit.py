#!/usr/bin/env python3
"""Static self-check for this toolkit repository (or a project it was copied into).

Usage:
    python scripts/check_toolkit.py [ROOT]

Validates, without modifying anything:

* required repository files exist;
* every ``*.json`` file parses;
* every ``*.py`` file compiles (syntax check, no ``.pyc`` written);
* ``.claude/settings.json`` has the documented hooks/permissions shape and the
  hook command points at a script that exists;
* every ``.claude/skills/*/SKILL.md`` has YAML frontmatter with a ``name``
  matching its directory and a non-empty ``description``;
* every ``.claude/agents/*.md`` has ``name`` and ``description``, a known
  ``model`` value, known tool names, and references only existing skills.

The frontmatter reader supports the YAML subset used by Claude Code files
(``key: value`` scalars and ``- item`` lists), so no third-party YAML package
is required. Exit code 0 = all checks passed, 1 = at least one failure.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Union

REQUIRED_FILES = [
    "README.md", "LICENSE", "CLAUDE.md", ".gitignore",
    ".claude/settings.json", ".claude/hooks/ue5_safety_guard.py", ".claude/hooks/README.md",
    ".claude/skills/ue5-cpp/SKILL.md", ".claude/skills/ue5-blueprints/SKILL.md",
    ".claude/skills/ue5-ai-systems/SKILL.md", ".claude/skills/ue5-build-debugging/SKILL.md",
    ".claude/skills/ue5-multiplayer/SKILL.md", ".claude/skills/ue5-code-review/SKILL.md",
    ".claude/agents/ue5-architect.md", ".claude/agents/ue5-cpp-developer.md",
    ".claude/agents/ue5-code-reviewer.md", ".claude/agents/ue5-build-engineer.md",
    ".claude/agents/ue5-blueprint-specialist.md", ".claude/skills/ue5-orchestrator/SKILL.md",
    "scripts/validate_project.py", "scripts/orchestrator.py", "scripts/README.md",
    "tests/test_safety_guard.py", "tests/test_orchestrator.py",
    "docs/installation.md", "docs/architecture.md", "docs/usage-examples.md",
    "docs/multi-agent-architecture.md", "docs/parallel-workflows.md",
    "docs/context-management.md", "docs/example-development-session.md",
]

HOOK_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification", "UserPromptSubmit",
    "Stop", "SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "SessionStart",
    "SessionEnd", "PermissionRequest",
}
HANDLER_TYPES = {"command", "http", "mcp_tool", "prompt", "agent"}
KNOWN_TOOLS = {
    "Agent", "Bash", "Edit", "Glob", "Grep", "LS", "MultiEdit", "NotebookEdit", "NotebookRead",
    "PowerShell", "Read", "Skill", "Task", "TodoWrite", "WebFetch", "WebSearch", "Write",
}
MODEL_ALIASES = {"sonnet", "opus", "haiku", "fable", "inherit"}
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RULE_RE = re.compile(r"^[A-Za-z_][\w]*(\(.+\))?$")
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}

Frontmatter = Dict[str, Union[str, List[str]]]


class Checker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.failures: List[str] = []
        self.passes = 0

    def ok(self) -> None:
        self.passes += 1

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def files(self, pattern: str) -> List[Path]:
        return sorted(p for p in self.root.rglob(pattern)
                      if not any(part in SKIP_DIRS for part in p.relative_to(self.root).parts))


def parse_frontmatter(text: str) -> Tuple[Frontmatter, str]:
    """Parse a leading '---' YAML block (scalar and list values only)."""
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        raise ValueError("file does not start with '---' frontmatter")
    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("frontmatter is not closed with '---'") from None
    data: Frontmatter = {}
    current_list: List[str] = []
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", raw)
        if item:
            current_list.append(item.group(1).strip().strip("'\""))
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if not match:
            raise ValueError(f"unsupported frontmatter line: {raw!r}")
        key, value = match.group(1), match.group(2).strip()
        if value:
            data[key] = value.strip("'\"")
        else:
            current_list = []
            data[key] = current_list
    return data, "\n".join(lines[end + 1:])


def check_required(c: Checker) -> None:
    for rel in REQUIRED_FILES:
        if (c.root / rel).is_file():
            c.ok()
        else:
            c.fail(f"missing required file: {rel}")


def check_json(c: Checker) -> None:
    for path in c.files("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
            c.ok()
        except ValueError as exc:
            c.fail(f"{c.rel(path)}: invalid JSON ({exc})")


def check_python(c: Checker) -> None:
    for path in c.files("*.py"):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            c.ok()
        except SyntaxError as exc:
            c.fail(f"{c.rel(path)}: syntax error line {exc.lineno}: {exc.msg}")


def check_settings(c: Checker) -> None:
    path = c.root / ".claude" / "settings.json"
    if not path.is_file():
        return
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return  # already reported by check_json
    if not isinstance(settings, dict):
        c.fail("settings.json: top level must be an object")
        return

    perms = settings.get("permissions", {})
    if not isinstance(perms, dict):
        c.fail("settings.json: 'permissions' must be an object")
    for key in ("allow", "ask", "deny"):
        rules = perms.get(key, []) if isinstance(perms, dict) else []
        if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
            c.fail(f"settings.json: permissions.{key} must be a list of strings")
            continue
        for rule in rules:
            if RULE_RE.match(rule):
                c.ok()
            else:
                c.fail(f"settings.json: malformed permission rule {rule!r}")

    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        c.fail("settings.json: 'hooks' must be an object")
        return
    for event, groups in hooks.items():
        if event not in HOOK_EVENTS:
            c.fail(f"settings.json: unknown hook event {event!r}")
        if not isinstance(groups, list):
            c.fail(f"settings.json: hooks.{event} must be a list")
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                c.fail(f"settings.json: each hooks.{event} entry needs a 'hooks' list")
                continue
            if "matcher" in group and not isinstance(group["matcher"], str):
                c.fail(f"settings.json: hooks.{event} matcher must be a string")
            for handler in group["hooks"]:
                if not isinstance(handler, dict) or handler.get("type") not in HANDLER_TYPES:
                    c.fail(f"settings.json: hooks.{event} handler has invalid 'type'")
                    continue
                if handler["type"] == "command":
                    command = handler.get("command")
                    if not isinstance(command, str) or not command.strip():
                        c.fail(f"settings.json: hooks.{event} command handler has no command")
                        continue
                    for script in re.findall(r"\.claude/hooks/[\w.-]+\.py", command):
                        if (c.root / script).is_file():
                            c.ok()
                        else:
                            c.fail(f"settings.json: hook script not found: {script}")
                timeout = handler.get("timeout")
                if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
                    c.fail(f"settings.json: hooks.{event} timeout must be a positive number")
                else:
                    c.ok()


def check_skills(c: Checker) -> List[str]:
    names: List[str] = []
    for path in sorted((c.root / ".claude" / "skills").glob("*/SKILL.md")):
        rel = c.rel(path)
        try:
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            c.fail(f"{rel}: {exc}")
            continue
        name, desc = meta.get("name"), meta.get("description")
        if not isinstance(name, str) or not SKILL_NAME_RE.match(name) or len(name) > 64:
            c.fail(f"{rel}: 'name' must be lowercase letters/digits/hyphens, <= 64 chars")
        elif name != path.parent.name:
            c.fail(f"{rel}: name {name!r} does not match directory {path.parent.name!r}")
        else:
            names.append(name)
            c.ok()
        if not isinstance(desc, str) or not desc.strip():
            c.fail(f"{rel}: 'description' is required")
        elif len(desc) > 1024:
            c.fail(f"{rel}: description is {len(desc)} chars (keep <= 1024)")
        else:
            c.ok()
        if len(body.strip()) < 200:
            c.fail(f"{rel}: skill body is too short to be actionable")
    return names


def check_agents(c: Checker, skills: List[str]) -> None:
    for path in sorted((c.root / ".claude" / "agents").glob("*.md")):
        rel = c.rel(path)
        try:
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            c.fail(f"{rel}: {exc}")
            continue
        name = meta.get("name")
        if not isinstance(name, str) or not name or ":" in name:
            c.fail(f"{rel}: 'name' is required and must not contain ':'")
        elif name != path.stem:
            c.fail(f"{rel}: name {name!r} does not match file name {path.stem!r}")
        else:
            c.ok()
        if not isinstance(meta.get("description"), str) or not meta["description"].strip():
            c.fail(f"{rel}: 'description' is required")
        tools = meta.get("tools")
        if tools is not None:
            tool_list = tools if isinstance(tools, list) else [t.strip() for t in tools.split(",")]
            unknown = [t for t in tool_list if t.split("(")[0] not in KNOWN_TOOLS
                       and not t.startswith("mcp__")]
            if unknown:
                c.fail(f"{rel}: unknown tools {unknown}")
            else:
                c.ok()
        model = meta.get("model")
        if model is not None and not (model in MODEL_ALIASES or str(model).startswith("claude-")):
            c.fail(f"{rel}: unsupported model value {model!r}")
        for skill in meta.get("skills", []) or []:
            if skill in skills:
                c.ok()
            else:
                c.fail(f"{rel}: references unknown skill {skill!r}")
        if not body.strip():
            c.fail(f"{rel}: system prompt body is empty")


def main(argv: List[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
    c = Checker(root)
    check_required(c)
    check_json(c)
    check_python(c)
    check_settings(c)
    skills = check_skills(c)
    check_agents(c, skills)

    for failure in c.failures:
        print(f"FAIL  {failure}")
    status = "PASSED" if not c.failures else "FAILED"
    print(f"Toolkit check {status}: {c.passes} checks passed, {len(c.failures)} failed "
          f"({len(skills)} skills validated).")
    return 0 if not c.failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
