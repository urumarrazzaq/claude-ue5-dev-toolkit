# claude-ue5-dev-toolkit

**Claude Code toolkit for Unreal Engine development featuring custom safety
hooks, specialized development skills, project instructions, and AI-assisted
workflows.**

[![tests](https://github.com/urumarrazzaq/claude-ue5-dev-toolkit/actions/workflows/tests.yml/badge.svg)](https://github.com/urumarrazzaq/claude-ue5-dev-toolkit/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

Large Unreal Engine 5 projects punish generic AI assistance: invented engine
APIs, raw `UObject*` members that get garbage-collected, gameplay state that
never replicates, an agent "cleaning up" with `git reset --hard`, or a
recursive delete aimed at `Content/`. This toolkit configures
[Claude Code](https://code.claude.com/docs) so it works the way an experienced
UE team expects:

- a **PreToolUse safety hook** that blocks destructive git and deletion
  commands before they run, with UE-aware rules;
- six **domain skills** (C++, Blueprints, AI, build debugging, multiplayer,
  code review) that load on demand;
- three **subagents** (architect, C++ developer, code reviewer) with scoped
  tools and workflows;
- a project-level **`CLAUDE.md`** with working rules for UE codebases;
- a read-only **project validator** that reports a UE project's structure and
  problems;
- **tests** for all of the above (61 unit tests, standard library only).

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Repository structure](#repository-structure)
- [Installation](#installation)
- [Usage examples](#usage-examples)
- [Hook behaviour](#hook-behaviour)
- [Skills and subagents](#skills-and-subagents)
- [Project validator](#project-validator)
- [Testing](#testing)
- [Configuration](#configuration)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [License](#license)

## Features

| Component | What it does | Status |
|---|---|---|
| `ue5_safety_guard.py` hook | Inspects every `Bash` / `PowerShell` command; **denies** history-destroying git commands and recursive deletion of the project, protected UE folders, home or drive roots; **asks** for risky-but-legitimate operations | Implemented, 48 tests |
| Skills | `ue5-cpp`, `ue5-blueprints`, `ue5-ai-systems`, `ue5-build-debugging`, `ue5-multiplayer`, `ue5-code-review` | Implemented, frontmatter validated |
| Subagents | `ue5-architect`, `ue5-cpp-developer`, `ue5-code-reviewer` | Implemented, frontmatter validated |
| `CLAUDE.md` | Project rules: inspect first, verify APIs, respect UObject lifecycle and authority, never overwrite user work, report validation | Implemented |
| `settings.json` | Hook registration, read-only git allow-list, `Edit` denied on `.uasset`/`.umap` and generated folders | Implemented, structure validated |
| `validate_project.py` | Read-only UE project report: `.uproject` metadata, modules vs `Build.cs`, targets, plugins, config, content counts, VCS hygiene; text or JSON | Implemented, 10 tests |
| `check_toolkit.py` | Self-check: required files, JSON, Python syntax, settings shape, skill/agent frontmatter | Implemented, runs in the test suite |

## Architecture

```
CLAUDE.md (always loaded)
   │
Claude Code session ──► skills (loaded on demand) ──► ue5-cpp, ue5-multiplayer, ...
   │                └─► subagents ──► architect (read-only) → developer → reviewer (read-only)
   │
   └── every Bash/PowerShell call ──► PreToolUse hook ──► deny | ask | (no output: normal permissions)
```

The hook never returns `allow`: it can only make Claude Code stricter, never
bypass your permission rules. Details and design decisions:
[docs/architecture.md](docs/architecture.md).

## Repository structure

```
claude-ue5-dev-toolkit/
├── README.md
├── LICENSE
├── CLAUDE.md                        # project instructions (copy to your UE project root)
├── .gitignore
├── .github/workflows/tests.yml      # CI: tests + self-check on Ubuntu and Windows
├── .claude/
│   ├── settings.json                # hook registration + permission rules
│   ├── hooks/
│   │   ├── ue5_safety_guard.py      # PreToolUse safety hook
│   │   └── README.md
│   ├── skills/
│   │   ├── ue5-cpp/SKILL.md
│   │   ├── ue5-blueprints/SKILL.md
│   │   ├── ue5-ai-systems/SKILL.md
│   │   ├── ue5-build-debugging/SKILL.md
│   │   ├── ue5-multiplayer/SKILL.md
│   │   └── ue5-code-review/SKILL.md
│   └── agents/
│       ├── ue5-architect.md
│       ├── ue5-cpp-developer.md
│       └── ue5-code-reviewer.md
├── scripts/
│   ├── validate_project.py          # UE project validator (read-only)
│   ├── check_toolkit.py             # toolkit self-check
│   └── README.md
├── tests/
│   ├── test_safety_guard.py
│   ├── test_validate_project.py
│   └── test_check_toolkit.py
└── docs/
    ├── installation.md
    ├── architecture.md
    └── usage-examples.md
```

## Installation

Requirements: Claude Code, Python 3.8+ (no packages), Git, a UE5 project.

Copy the toolkit into the folder that contains your `.uproject`:

```bash
git clone https://github.com/urumarrazzaq/claude-ue5-dev-toolkit.git
cd /path/to/MyGame
cp -r ../claude-ue5-dev-toolkit/.claude .
cp ../claude-ue5-dev-toolkit/CLAUDE.md .
mkdir -p Tools/claude && cp ../claude-ue5-dev-toolkit/scripts/*.py Tools/claude/
```

Windows PowerShell:

```powershell
Copy-Item -Recurse ..\claude-ue5-dev-toolkit\.claude .
Copy-Item ..\claude-ue5-dev-toolkit\CLAUDE.md .
```

> **Windows users - important:** the hook command uses `python3`. On many
> Windows machines `python3` is the Microsoft Store placeholder, so the hook
> fails (non-blocking) and **does not protect you**. If `python3 -c "print(1)"`
> doesn't print `1`, change the command in `.claude/settings.json` to
> `py -3 "${CLAUDE_PROJECT_DIR}/.claude/hooks/ue5_safety_guard.py"`.

Merge with existing `.claude/settings.json` / `CLAUDE.md` files rather than
overwriting them. Verification steps, configuration and uninstall:
[docs/installation.md](docs/installation.md).

Quick check that the hook is live:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git reset --hard"},"cwd":"."}' | python3 .claude/hooks/ue5_safety_guard.py
# -> {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", ...}}
```

## Usage examples

```text
> Run the project validator and summarise the modules, plugins and any problems.

> Use the ue5-architect agent to plan a stamina system for the player character
  that works on a dedicated server.

> Approved. Use the ue5-cpp-developer agent to implement it and build with <your Build.bat command>.

> Use the ue5-code-reviewer agent to review the uncommitted changes.

> /ue5-build-debugging  The editor build fails with LNK2019 - here is the log: ...

> /ue5-multiplayer  Ammo desyncs on clients after reloading. Look at UWeaponComponent.
```

When Claude attempts something destructive, the hook stops it and explains
why:

```
UE5 safety guard blocked this command:
- 'git reset --hard' discards all uncommitted work and can move the branch
  away from commits. Use 'git stash' or create a backup branch first, and ask
  the user to run the reset themselves if it is really needed.
```

More walkthroughs: [docs/usage-examples.md](docs/usage-examples.md).

## Hook behaviour

| Command | Decision |
|---|---|
| `git status`, `git log`, `git diff`, `git reset --soft`, `git clean -n` | no output (normal permissions) |
| `git reset --hard`, `git clean -fd`, `git push --force`, `git checkout -- .`, `git filter-branch`, `git reflog expire`, `git gc --prune=now`, `git stash clear` | **deny** |
| `rm -rf /`, `~`, `.`, `*`, `C:\`, `rm -rf Content`, `Remove-Item -Recurse Source`, `rmdir /s Config`, `rm -rf <project path>` | **deny** |
| `rm -rf Binaries Intermediate DerivedDataCache` | no output (regenerable) |
| `git push --force-with-lease`, `git branch -D`, `git restore <file>`, `rm -rf $VAR/`, `rm *.uasset`, `rm *.uproject`, `find ... -delete` | **ask** |
| `git commit -m "never run git reset --hard"` | no output (quoted text is not a command) |

It parses command chains (`;`, `&&`, `||`, `|`), wrappers (`sudo`, `env`,
`xargs`), nested shells (`bash -c`, `pwsh -Command`, `cmd /c`), PowerShell
aliases (`rm`, `ri`, `del`, `rd`) and Windows / Git Bash / WSL path forms.
Malformed input never crashes it. Full rule list and protocol:
[.claude/hooks/README.md](.claude/hooks/README.md).

## Skills and subagents

**Skills** load only when relevant (Claude matches the description, or you type
`/ue5-cpp` etc.):

| Skill | Covers |
|---|---|
| `ue5-cpp` | UObject lifecycle & GC, reflection macros, actor/component design, gameplay framework, interfaces & delegates, module dependencies, performance, Epic conventions |
| `ue5-blueprints` | Blueprint/C++ split, communication (refs, casts, interfaces, dispatchers), UMG, debugging, Tick optimisation; never edits `.uasset` |
| `ue5-ai-systems` | AIController, Behavior Trees, Blackboard, EQS, Perception, navigation, combat structure, Gameplay Debugger / Visual Logger |
| `ue5-build-debugging` | UBT, UHT, Build.cs/Target.cs, plugin deps, compiler/linker error classes, Development vs Shipping, BuildCookRun, log triage |
| `ue5-multiplayer` | Authority & roles, replication, conditions, RPCs, relevancy & dormancy, prediction, network debugging |
| `ue5-code-review` | Change-set collection, prioritised checklist, regression analysis, severity-ranked report |

**Subagents** run in their own context with limited tools:

| Agent | Role | Tools |
|---|---|---|
| `ue5-architect` | Maps architecture, modules and dependencies; produces an implementation plan with file:line evidence | Read, Grep, Glob, Bash (read-only use) |
| `ue5-cpp-developer` | Implements C++ with minimal diffs, updates Build.cs, builds if a command is known, reports honestly | Read, Grep, Glob, Edit, Write, Bash |
| `ue5-code-reviewer` | Reviews the diff for GC, replication, performance, architecture and regression issues | Read, Grep, Glob, Bash (read-only use) |

## Project validator

```bash
python scripts/validate_project.py D:/Projects/ShooterGame          # text report
python scripts/validate_project.py D:/Projects/ShooterGame --json   # machine-readable
```

Reports `.uproject` metadata (engine association, modules, plugins, target
platforms), Source/Content/Config/Plugins presence, declared modules without
`Build.cs`, `Build.cs` modules missing from the `.uproject`, missing targets,
project plugin descriptor problems, missing default config, content counts and
`.gitignore` / Git LFS hygiene. Exit code `0` (no errors), `1` (errors),
`2` (no project). It never writes to the project. See
[scripts/README.md](scripts/README.md).

## Testing

```bash
python -m unittest discover -s tests -v     # Windows: py -3 -m unittest discover -s tests -v
python scripts/check_toolkit.py
```

Results at the time of writing (Windows 11, Python 3.15.0 and 3.13.17):

```
Ran 61 tests ... OK
Toolkit check PASSED: 67 checks passed, 0 failed (6 skills validated).
```

The code avoids syntax newer than Python 3.8, but only 3.13 and 3.15 have
been run locally; the CI workflow tests 3.10 and 3.13 on Ubuntu and Windows.

| Suite | Tests | Covers |
|---|---|---|
| `test_safety_guard.py` | 48 | every example in `docs/usage-examples.md`, allowed commands (git status/log/diff, Python, regenerable folders, quoted text), blocked git, blocked deletion (POSIX, PowerShell, cmd, nested shells, find), ask cases, the real script via subprocess (stdout JSON shape, exit codes, invalid/empty/non-object JSON, fail-closed mode), tokenizer |
| `test_validate_project.py` | 10 | healthy project, BOM-encoded `.uproject`, missing module/targets, invalid JSON, no project, `.gitignore` gaps, plugin module without Build.cs, JSON output, read-only guarantee |
| `test_check_toolkit.py` | 3 | repository passes its own self-check; frontmatter parser |

`pytest tests` also works if you have pytest installed, but it is not required.

## Configuration

`.claude/settings.json` (excerpt):

```json
{
  "permissions": {
    "allow": ["Bash(git status)", "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)"],
    "deny":  ["Edit(**/*.uasset)", "Edit(**/*.umap)", "Edit(**/Intermediate/**)"]
  },
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash|PowerShell",
      "hooks": [{
        "type": "command",
        "command": "python3 \"${CLAUDE_PROJECT_DIR}/.claude/hooks/ue5_safety_guard.py\"",
        "timeout": 10
      }]
    }]
  }
}
```

| Option | Effect |
|---|---|
| `UE5_GUARD_FAIL_CLOSED=1` (env) | Block the command if the hook cannot parse its input (default: fail open with a visible non-blocking hook error) |
| `.claude/settings.local.json` | Personal, git-ignored overrides |
| `PROTECTED_DIR_NAMES` in the hook | Add studio-specific folders to protect (e.g. `RawAssets`) |

## Limitations

- **The hook is a guard rail, not a sandbox.** It analyses command text. It
  cannot see inside scripts it doesn't read (`./clean.sh`,
  `python -c "shutil.rmtree(...)"`), `eval`, shell aliases or functions, or
  arbitrary variable indirection. Keep version control and Claude Code's
  permission prompts as your primary safety net.
- **Windows `python3` placeholder.** See the installation note above; the
  default command must be changed to `py -3` on such machines.
- **Blueprints and other binary assets** cannot be read or edited by Claude
  Code. The Blueprint skill gives editor instructions; it does not manipulate
  `.uasset` files.
- **Skills and agents are guidance.** They improve behaviour but cannot
  guarantee that generated UE code compiles; always build and test.
  Read-only agents are restricted by tool list and instructions; their Bash
  access is not sandboxed.
- **The validator** checks structure, not code. It cannot verify engine or
  marketplace plugins without the engine installation, and it does not parse
  `Build.cs` C#.
- **Not tested inside a live Unreal Editor build pipeline** by this
  repository's automated tests; the hook and scripts are tested in isolation
  with synthetic inputs and projects.
- The CI workflow file is included but has not been run until the repository
  is pushed to GitHub.

## Roadmap

- [ ] Unreal Editor MCP integration (live editor queries: actors, Blueprints,
      assets) - **not implemented**; requires an editor plugin and MCP server.
- [ ] `PostToolUse` hook that runs a fast compile/UHT check after C++ edits.
- [ ] Build-log parser script that extracts and classifies the first UBT/UHT/linker error.
- [ ] Configurable protected-folder list via a JSON file instead of editing the hook.
- [ ] Skills for Gameplay Ability System, Enhanced Input, CommonUI and Niagara.
- [ ] Packaging as a Claude Code plugin for one-command installation.

## License

[MIT](LICENSE)
