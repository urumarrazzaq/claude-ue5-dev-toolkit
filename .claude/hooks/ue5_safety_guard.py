#!/usr/bin/env python3
"""UE5 safety guard: a Claude Code PreToolUse hook for Bash and PowerShell.

The hook reads the PreToolUse payload from stdin, inspects the shell command
Claude is about to run, and returns one of three outcomes:

* **deny**  - the command is clearly destructive (git history destruction,
  recursive deletion of the project, its source/content folders, or a drive
  root). Claude receives the reason and the command never runs.
* **ask**   - the command is risky but sometimes legitimate (force-push with
  lease, deleting a branch, recursive delete of a path built from an
  unresolved variable, deleting an asset file outside the editor). The user
  is prompted to confirm.
* **no decision** - nothing suspicious was found. The hook prints nothing and
  exits 0, so Claude Code's normal permission rules apply unchanged. The hook
  never emits "allow", so it can only ever make Claude Code *stricter*.

Output follows the documented PreToolUse contract: exit code 0 with a single
JSON object on stdout of the form::

    {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                            "permissionDecision": "deny" | "ask",
                            "permissionDecisionReason": "..."}}

Malformed input never crashes the hook. By default it fails open (exit 1, a
non-blocking hook error whose stderr is shown to the user); set the
environment variable ``UE5_GUARD_FAIL_CLOSED=1`` to block instead (exit 2).

The script uses only the Python standard library (3.8+) and never touches the
file system beyond reading stdin.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

GUARDED_TOOLS = {"Bash", "PowerShell"}

# Top-level folders of a UE project (or plugin) that hold irreplaceable work.
PROTECTED_DIR_NAMES = {"source", "content", "config", "plugins", ".git"}

# Files whose deletion through the shell is either catastrophic for the
# project or bypasses the editor's redirector / reference handling.
PROTECTED_FILE_SUFFIXES = (".uproject", ".uplugin", ".build.cs", ".target.cs")
ASSET_FILE_SUFFIXES = (".uasset", ".umap")

# Paths that mean "everything" on their own.
CATASTROPHIC_TARGETS = {
    "/", "/*", "~", "~/", "~/*", "*", ".", "./", "./*", "..", "../", "../*",
    "$home", "${home}", "$home/*", "${home}/*", "$env:userprofile",
    "$env:userprofile/*", "%userprofile%", "/mnt", "/mnt/*",
}

DECISION_DENY = "deny"
DECISION_ASK = "ask"

# Commands that simply run another command; we look through them.
WRAPPER_COMMANDS = {"sudo", "doas", "command", "builtin", "exec", "nohup",
                    "time", "env", "xargs", "nice", "&", "call"}
WRAPPER_OPTIONS_WITH_VALUE = {
    "sudo": {"-u", "-g", "-h", "-p", "-U", "-C", "-D", "-r", "-t"},
    "doas": {"-u", "-C"},
    "xargs": {"-n", "-I", "-L", "-P", "-s", "-d", "-E", "-a"},
    "env": {"-u", "-C", "-S"},
    "nice": {"-n"},
}

# Shells that accept a command string after -c / -Command.
SHELL_COMMANDS = {"bash", "sh", "zsh", "dash", "ksh", "powershell",
                  "powershell.exe", "pwsh", "pwsh.exe", "cmd", "cmd.exe"}

POWERSHELL_DELETE = {"remove-item", "ri", "rm", "rmdir", "rd", "del", "erase"}


@dataclass(frozen=True)
class Finding:
    """A single reason to deny or ask about a command."""

    decision: str
    reason: str


# --------------------------------------------------------------------------
# Command splitting and tokenising
# --------------------------------------------------------------------------

def split_segments(command: str) -> List[str]:
    """Split a command line into simple commands on ; & | && || and newlines.

    Splitting is quote-aware so that text inside quotes - for example a commit
    message that mentions ``git reset --hard`` - is never treated as a command.
    Backtick (PowerShell escape) and backslash (POSIX escape) are honoured.
    """
    segments: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < len(command):
                current.append(command[i + 1])
                i += 1
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch in ";|&\n\r":
            segments.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    segments.append("".join(current))
    return [s.strip() for s in segments if s.strip()]


def tokenize(segment: str) -> List[str]:
    """Tokenise one simple command, tolerating Windows paths and bad quoting."""
    # POSIX mode eats backslashes in unquoted Windows paths (C:\a -> C:a), so
    # use non-POSIX mode whenever a backslash is present and strip quotes.
    posix = "\\" not in segment
    try:
        lexer = shlex.shlex(segment, posix=posix)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes
        tokens = segment.split()
    return [_strip_quotes(t) for t in tokens]


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _program_name(token: str) -> str:
    """Return a lower-case program name without directory or .exe suffix."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def unwrap(tokens: List[str]) -> List[str]:
    """Drop leading wrappers (sudo, env VAR=1, xargs, ...) and assignments."""
    tokens = list(tokens)
    while tokens:
        head = tokens[0]
        name = _program_name(head)
        if name in WRAPPER_COMMANDS:
            tokens = tokens[1:]
            # Skip the wrapper's own options (e.g. sudo -u root, xargs -0),
            # including the value of options that take one.
            takes_value = WRAPPER_OPTIONS_WITH_VALUE.get(name, set())
            while tokens and tokens[0].startswith("-") and len(tokens) > 1:
                option = tokens[0]
                tokens = tokens[1:]
                if option in takes_value and len(tokens) > 1:
                    tokens = tokens[1:]
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", head):
            tokens = tokens[1:]  # VAR=value prefix
        elif head in ("(", "{", "!", "&"):
            tokens = tokens[1:]
        else:
            break
    return tokens


# --------------------------------------------------------------------------
# Path classification
# --------------------------------------------------------------------------

def _normalize(path: str) -> str:
    p = path.strip().strip("'\"").replace("\\", "/").lower()
    while len(p) > 1 and p.endswith("/") and not re.fullmatch(r"[a-z]:/", p):
        p = p[:-1]
    return p


def _to_comparable(path: str) -> str:
    """Normalise a path so Git Bash (/d/x), WSL (/mnt/d/x) and D:\\x compare."""
    p = _normalize(path)
    m = re.match(r"^(?:/mnt)?/([a-z])(/.*)?$", p)
    if m:
        p = f"{m.group(1)}:{m.group(2) or '/'}"
    if re.fullmatch(r"[a-z]:", p):
        p += "/"
    return p


def _join(cwd: str, target: str) -> str:
    """Resolve *target* against *cwd* textually (no file-system access)."""
    t = _to_comparable(target)
    if re.match(r"^[a-z]:/", t) or t.startswith("/"):
        combined = t
    elif not cwd:
        return t
    else:
        combined = _to_comparable(cwd).rstrip("/") + "/" + t
    drive = ""
    if re.match(r"^[a-z]:/", combined):
        drive, combined = combined[:2], combined[2:]
    parts: List[str] = []
    for part in combined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return drive + "/" + "/".join(parts)


def _has_unresolved_variable(path: str) -> bool:
    lowered = path.lower()
    if lowered in CATASTROPHIC_TARGETS:
        return False
    return bool(re.search(r"\$\{?[a-z_]|\$env:|%[a-z_]+%|\$\(|`", lowered))


def classify_delete_target(target: str, cwd: str) -> Optional[Finding]:
    """Decide whether recursively deleting *target* is dangerous."""
    norm = _normalize(target)
    if not norm:
        return None

    if norm in CATASTROPHIC_TARGETS or re.fullmatch(r"([a-z]:|/[a-z]|/mnt/[a-z])/?\*?", norm):
        return Finding(DECISION_DENY,
                       f"Recursive deletion of '{target}' would wipe the project, "
                       "a home directory or an entire drive.")

    if _has_unresolved_variable(norm):
        return Finding(DECISION_ASK,
                       f"Recursive deletion of '{target}' depends on a variable or "
                       "sub-expression. If it expands to an empty string the command "
                       "could delete far more than intended. Confirm the resolved path.")

    # Strip trailing glob so 'Content/*' is judged as 'Content'.
    base_path = re.sub(r"/\*+$", "", norm)
    basename = base_path.rsplit("/", 1)[-1]
    if basename in PROTECTED_DIR_NAMES:
        return Finding(DECISION_DENY,
                       f"'{target}' is a protected Unreal project folder "
                       "(Source, Content, Config, Plugins or .git). Deleting it "
                       "recursively destroys source code, "
                       "assets, configuration or version-control history. Remove "
                       "individual files instead, or ask the user to do it manually.")

    if cwd:
        resolved = _join(cwd, base_path)
        here = _join(cwd, ".")
        if resolved == here or here.startswith(resolved.rstrip("/") + "/"):
            return Finding(DECISION_DENY,
                           f"'{target}' resolves to the current project directory or one "
                           "of its parents. Refusing to delete the working tree.")
    return None


def classify_file_delete(target: str) -> Optional[Finding]:
    """Non-recursive deletion of key UE files."""
    norm = _normalize(target)
    if norm.endswith(PROTECTED_FILE_SUFFIXES):
        return Finding(DECISION_ASK,
                       f"Deleting '{target}' removes a project/plugin descriptor or "
                       "build rules file. Confirm this is intentional.")
    if norm.endswith(ASSET_FILE_SUFFIXES):
        return Finding(DECISION_ASK,
                       f"Deleting '{target}' from the shell bypasses the Unreal Editor's "
                       "reference checks and redirector handling, which can leave broken "
                       "references. Prefer deleting assets inside the editor.")
    return None


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def _short_flag_set(args: Sequence[str]) -> set:
    """Expand POSIX short flag clusters: ['-rf', '-v'] -> {'r','f','v'}."""
    chars: set = set()
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            chars.update(a[1:])
    return chars


def check_git(args: List[str]) -> List[Finding]:
    """Rules for git sub-commands. *args* excludes the leading 'git'."""
    # Skip global options such as -C <path>, -c key=value, --no-pager.
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i >= len(args):
        return []
    sub, rest = args[i].lower(), args[i + 1:]
    lowered = [r.lower() for r in rest]
    short = _short_flag_set(rest)
    findings: List[Finding] = []

    if sub == "reset" and ("--hard" in lowered or "--merge" in lowered or "--keep" in lowered):
        findings.append(Finding(DECISION_DENY,
            "'git reset --hard' discards all uncommitted work and can move the branch "
            "away from commits. Use 'git stash' or create a backup branch first, and "
            "ask the user to run the reset themselves if it is really needed."))

    elif sub == "clean" and ("f" in short or "--force" in lowered):
        if "n" not in short and "--dry-run" not in lowered:
            findings.append(Finding(DECISION_DENY,
                "'git clean -f' permanently deletes untracked files, which in an Unreal "
                "project often includes new assets and source files that were never "
                "committed. Run 'git clean -n' to preview and let the user decide."))

    elif sub == "push":
        if "--force-with-lease" in " ".join(lowered) or "--force-if-includes" in lowered:
            findings.append(Finding(DECISION_ASK,
                "Force-push (with lease) rewrites remote history. Confirm the target "
                "branch is not shared."))
        elif "--force" in lowered or "f" in short or any(p.startswith("+") for p in rest):
            findings.append(Finding(DECISION_DENY,
                "'git push --force' can overwrite commits on the remote that other "
                "people depend on. Use '--force-with-lease' and confirm with the user."))
        elif "--delete" in lowered or "d" in short or any(
                p.startswith(":") and len(p) > 1 for p in rest):
            findings.append(Finding(DECISION_ASK,
                "This push deletes a remote branch or tag. Confirm it is intended."))
        elif "--mirror" in lowered:
            findings.append(Finding(DECISION_DENY,
                "'git push --mirror' overwrites every ref on the remote."))

    elif sub in ("checkout", "restore"):
        pathspec_all = {".", "*", ":/", "--", "./"}
        has_discard_all = any(p in (".", "*", ":/", "./") for p in rest)
        if sub == "restore" and ("--staged" in lowered or "s" in short) and \
                "--worktree" not in lowered and "w" not in short:
            pass  # unstaging only; working tree is untouched
        elif has_discard_all or "--force" in lowered or (sub == "checkout" and "f" in short):
            findings.append(Finding(DECISION_DENY,
                f"'git {sub}' with '.' or --force discards every uncommitted change in "
                "the working tree. Commit or stash first, or ask the user."))
        elif sub == "restore" or "--" in rest:
            paths = [p for p in rest if p not in pathspec_all and not p.startswith("-")]
            if paths:
                findings.append(Finding(DECISION_ASK,
                    f"'git {sub}' will discard uncommitted changes to: "
                    f"{', '.join(paths)}. Confirm these edits can be lost."))

    elif sub == "branch" and ("D" in short or ("d" in short and "f" in short)
                              or ("--delete" in lowered and "--force" in lowered)):
        findings.append(Finding(DECISION_ASK,
            "'git branch -D' force-deletes a branch even if it is not merged."))

    elif sub == "stash" and lowered[:1] == ["clear"]:
        findings.append(Finding(DECISION_DENY,
            "'git stash clear' permanently drops every stash entry."))
    elif sub == "stash" and lowered[:1] == ["drop"]:
        findings.append(Finding(DECISION_ASK,
            "'git stash drop' permanently removes a stash entry."))

    elif sub in ("filter-branch", "filter-repo"):
        findings.append(Finding(DECISION_DENY,
            f"'git {sub}' rewrites the entire repository history. This must be run "
            "deliberately by the user, not by an agent."))

    elif sub == "reflog" and lowered[:1] in (["expire"], ["delete"]):
        findings.append(Finding(DECISION_DENY,
            "Expiring or deleting reflog entries removes the safety net used to "
            "recover lost commits."))

    elif sub == "gc" and any(a.startswith("--prune=now") or a == "--prune=all" for a in lowered):
        findings.append(Finding(DECISION_DENY,
            "'git gc --prune=now' permanently deletes unreachable commits that could "
            "otherwise be recovered from the reflog."))

    elif sub == "update-ref" and "-d" in lowered:
        findings.append(Finding(DECISION_ASK,
            "'git update-ref -d' deletes a ref directly. Confirm it is intended."))

    return findings


def check_posix_rm(args: List[str], cwd: str) -> List[Finding]:
    short = _short_flag_set(args)
    recursive = bool(short & {"r", "R"}) or "--recursive" in args
    targets = [a for a in args if not a.startswith("-")]
    findings: List[Finding] = []
    if "--no-preserve-root" in args:
        findings.append(Finding(DECISION_DENY,
            "'--no-preserve-root' disables rm's protection against deleting '/'."))
    for t in targets:
        f = classify_delete_target(t, cwd) if recursive else classify_file_delete(t)
        if f:
            findings.append(f)
    return findings


def check_powershell_remove(args: List[str], cwd: str) -> List[Finding]:
    lowered = [a.lower() for a in args]
    recursive = any(a in ("-recurse", "-r", "-rec") or a.startswith("-recurse") for a in lowered)
    targets: List[str] = []
    skip_next = False
    for a, low in zip(args, lowered):
        if skip_next:
            skip_next = False
            continue
        if low in ("-path", "-literalpath", "-lp"):
            continue  # the value follows and is treated as a positional target
        if low in ("-include", "-exclude", "-filter", "-credential", "-stream"):
            skip_next = True
            continue
        if low.startswith("-"):
            continue
        targets.extend(x for x in a.split(",") if x)
    findings: List[Finding] = []
    for t in targets:
        f = classify_delete_target(t, cwd) if recursive else classify_file_delete(t)
        if f:
            findings.append(f)
    return findings


def check_cmd_delete(args: List[str], cwd: str) -> List[Finding]:
    """cmd.exe 'rmdir /s' and 'del /s'."""
    lowered = [a.lower() for a in args]
    recursive = "/s" in lowered
    targets = [a for a in args if not (a.startswith("/") and len(a) <= 3)]
    findings: List[Finding] = []
    for t in targets:
        f = classify_delete_target(t, cwd) if recursive else classify_file_delete(t)
        if f:
            findings.append(f)
    return findings


def check_find(args: List[str], cwd: str) -> List[Finding]:
    lowered = [a.lower() for a in args]
    deleting = "-delete" in lowered or any(
        lowered[i] in ("-exec", "-execdir", "-ok") and i + 1 < len(lowered)
        and _program_name(lowered[i + 1]) in ("rm", "shred")
        for i in range(len(lowered)))
    if not deleting:
        return []
    roots = []
    for a in args:
        if a.startswith("-") or a in ("(", "!"):
            break
        roots.append(a)
    findings = [f for f in (classify_delete_target(r, cwd) for r in roots or ["."]) if f]
    if not findings:
        findings.append(Finding(DECISION_ASK,
            "'find ... -delete' removes every match without confirmation. Review the "
            "match list first by running the same 'find' without '-delete'."))
    return findings


def check_disk_destroyers(prog: str, args: List[str]) -> List[Finding]:
    lowered = [a.lower() for a in args]
    if prog in ("mkfs",) or prog.startswith("mkfs."):
        return [Finding(DECISION_DENY, "Formatting a file system is never part of UE development.")]
    if prog in ("format-volume", "clear-disk", "initialize-disk", "remove-partition"):
        return [Finding(DECISION_DENY, f"'{prog}' destroys disk contents.")]
    if prog == "format" and any(re.fullmatch(r"[a-z]:", a) for a in lowered):
        return [Finding(DECISION_DENY, "Formatting a drive is never part of UE development.")]
    if prog == "dd" and any(a.startswith("of=/dev/") for a in lowered):
        return [Finding(DECISION_DENY, "'dd' writing to a raw device can destroy a disk.")]
    if prog == "shred":
        return [Finding(DECISION_ASK, "'shred' irreversibly overwrites files.")]
    return []


def _inner_shell_command(prog: str, args: List[str]) -> Optional[str]:
    """Extract the script passed to bash -c / pwsh -Command / cmd /c."""
    for i, a in enumerate(args):
        low = a.lower()
        if prog in ("cmd",) and low in ("/c", "/k"):
            return " ".join(args[i + 1:])
        if low in ("-c", "-command", "-cmd") or (prog in ("bash", "sh", "zsh", "dash", "ksh")
                                                and low.startswith("-") and "c" in low[1:]
                                                and not low.startswith("--")):
            return " ".join(args[i + 1:])
    return None


def analyze_command(command: str, cwd: str = "", depth: int = 0) -> List[Finding]:
    """Return every finding for a full command line (Bash or PowerShell)."""
    findings: List[Finding] = []
    if depth > 3:  # guard against pathological nesting
        return findings
    for segment in split_segments(command):
        tokens = unwrap(tokenize(segment))
        if not tokens:
            continue
        prog, args = _program_name(tokens[0]), tokens[1:]

        if prog in SHELL_COMMANDS:
            inner = _inner_shell_command(prog.replace(".exe", ""), args)
            if inner:
                findings.extend(analyze_command(inner, cwd, depth + 1))
            continue

        if prog == "git":
            findings.extend(check_git(args))
        elif prog == "rm":
            # 'rm' is POSIX rm in Bash but an alias of Remove-Item in PowerShell.
            if any(a.lower().startswith("-recurse") for a in args):
                findings.extend(check_powershell_remove(args, cwd))
            else:
                findings.extend(check_posix_rm(args, cwd))
        elif prog in POWERSHELL_DELETE and prog not in ("rmdir", "rd", "del", "erase"):
            findings.extend(check_powershell_remove(args, cwd))
        elif prog in ("rmdir", "rd", "del", "erase"):
            if any(a.lower() == "/s" for a in args):
                findings.extend(check_cmd_delete(args, cwd))
            else:
                findings.extend(check_powershell_remove(args, cwd))
        elif prog == "find":
            findings.extend(check_find(args, cwd))
        else:
            findings.extend(check_disk_destroyers(prog, args))
    return findings


# --------------------------------------------------------------------------
# Hook entry point
# --------------------------------------------------------------------------

def decide(payload: dict) -> Optional[dict]:
    """Return the hook JSON response for *payload*, or None for no decision."""
    if payload.get("tool_name") not in GUARDED_TOOLS:
        return None
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""

    findings = analyze_command(command, cwd)
    if not findings:
        return None
    deny = [f for f in findings if f.decision == DECISION_DENY]
    chosen = deny or findings
    decision = DECISION_DENY if deny else DECISION_ASK
    reasons = _dedupe(f.reason for f in chosen)
    prefix = "UE5 safety guard blocked this command" if deny else "UE5 safety guard flagged this command"
    reason = prefix + ":\n- " + "\n- ".join(reasons)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def _dedupe(items: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def main(stdin=None, stdout=None, stderr=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    fail_closed = os.environ.get("UE5_GUARD_FAIL_CLOSED", "") == "1"
    try:
        raw = stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
    except (ValueError, UnicodeDecodeError) as exc:
        stderr.write(f"ue5_safety_guard: could not parse hook input ({exc}).\n")
        # Exit 2 blocks the tool call; exit 1 is a non-blocking hook error.
        return 2 if fail_closed else 1

    try:
        response = decide(payload)
    except Exception as exc:  # defensive: a guard bug must not wedge the session
        stderr.write(f"ue5_safety_guard: internal error ({exc!r}).\n")
        return 2 if fail_closed else 1

    if response is not None:
        stdout.write(json.dumps(response))
        stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
