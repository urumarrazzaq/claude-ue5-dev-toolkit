# Architecture

The toolkit is a set of Claude Code configuration files plus two small Python
tools. Each layer has one job, and they are independent: you can adopt any
subset.

```
                       ┌──────────────────────────────┐
                       │          CLAUDE.md           │  always-loaded project rules
                       └──────────────┬───────────────┘
                                      │
 user request ──► Claude Code main session ──────────────────────────────┐
                    │          │                 │                       │
                    │   loads on demand    delegates to                  │ every Bash /
                    │          ▼                 ▼                       │ PowerShell call
                    │   .claude/skills/    .claude/agents/               ▼
                    │   ue5-cpp            ue5-architect (read-only) ┌──────────────────────┐
                    │   ue5-blueprints     ue5-cpp-developer         │ PreToolUse hook      │
                    │   ue5-ai-systems     ue5-code-reviewer (r/o)   │ ue5_safety_guard.py  │
                    │   ue5-build-debugging        │                 │ deny / ask / no-op   │
                    │   ue5-multiplayer            │ preloaded skills└──────────────────────┘
                    │   ue5-code-review ◄──────────┘
                    │
                    └──► scripts/validate_project.py  (read-only project report)
                         scripts/check_toolkit.py     (toolkit self-check)
```

## Layers

### 1. Project instructions - `CLAUDE.md`

Loaded into every session. Holds rules that should never be forgotten
(inspect before writing, verify APIs, respect UObject lifecycle and authority,
never overwrite user work, report validation). Kept short because it costs
context in every turn; detail lives in skills.

### 2. Skills - `.claude/skills/<name>/SKILL.md`

Domain knowledge loaded **on demand**. Claude Code puts each skill's `name`
and `description` in its skill listing; the body loads only when the skill is
invoked (by Claude because the description matches, or by the user via
`/ue5-cpp` etc.). Descriptions therefore name concrete triggers (file types,
classes, symptoms).

| Skill | Scope |
|---|---|
| `ue5-cpp` | UObject/GC, reflection, actors/components, framework, delegates, modules, perf, conventions |
| `ue5-blueprints` | BP architecture, communication, UMG, debugging, Tick - and the rule that `.uasset` is not text |
| `ue5-ai-systems` | AIController, BT/Blackboard, EQS, Perception, navigation, combat, AI debugging |
| `ue5-build-debugging` | UBT/UHT/linker/cook failure classification, Build.cs/Target.cs, plugins, configurations, log triage |
| `ue5-multiplayer` | authority, replication, conditions, RPCs, relevancy, prediction, net debugging |
| `ue5-code-review` | review checklist, regression analysis, severity-ranked report format |

### 3. Subagents - `.claude/agents/*.md`

Separate context windows with restricted tools and a focused system prompt.

| Agent | Tools | Writes? | Preloaded skills |
|---|---|---|---|
| `ue5-architect` | Read, Grep, Glob, Bash | No (instructed read-only) | - |
| `ue5-cpp-developer` | Read, Grep, Glob, Edit, Write, Bash | Yes | `ue5-cpp`, `ue5-multiplayer`, `ue5-build-debugging` |
| `ue5-code-reviewer` | Read, Grep, Glob, Bash | No (instructed read-only) | `ue5-code-review` |

The architect and reviewer have no `Edit`/`Write` tools. They keep `Bash` for
`git diff`/`git log`/`rg`; their prompts restrict Bash to read-only commands,
and the safety hook applies to subagents as well. Bash is not technically
sandboxed, so this is a policy plus backstop, not an enforcement boundary.

Typical flow for a feature: **architect** (map + plan) → user approves →
**developer** (implement, build) → **reviewer** (independent review of the
diff) → main session reports to the user.

Two more read-only agents support larger work: `ue5-blueprint-specialist`
(asset dependencies and editor instructions) and `ue5-build-engineer`
(UBT/UHT log analysis and build validation). For multi-class features the
`ue5-orchestrator` skill coordinates all five with a validated task board;
see [multi-agent-architecture.md](multi-agent-architecture.md).

### 4. Safety hook - `.claude/hooks/ue5_safety_guard.py`

A `PreToolUse` command hook matched to `Bash|PowerShell`.

```
stdin JSON ─► parse ─► tool_name in {Bash, PowerShell}? ─► split command
              (invalid → exit 1, or 2 if fail-closed)       (quote-aware: ; && || | & \n)
                                                                │
                                         per segment: tokenize ─► strip wrappers (sudo, env, xargs)
                                                                │
                         ┌───────────────┬──────────────┬──────┴───────┬───────────────┐
                         ▼               ▼              ▼              ▼               ▼
                      git rules     rm / Remove-Item  cmd rmdir/del   find -delete   nested shell
                                    path classifier                                  (bash -c, pwsh -Command,
                                                                                      cmd /c) → recurse
                                                                │
                                     findings ─► any deny? → deny : any ask? → ask : print nothing
```

Design decisions:

- **Never `allow`.** Emitting no output keeps Claude Code's own permission
  rules in charge; the hook can only tighten them.
- **Deny vs ask.** Deny is reserved for operations that are almost never
  correct for an agent and hard to undo. Legitimate-but-risky operations ask,
  so the hook does not get in the way of normal work.
- **UE-aware paths.** `Source`, `Content`, `Config`, `Plugins`, `.git` are
  protected; `Binaries`, `Intermediate`, `DerivedDataCache`, `Saved` are left
  alone because deleting them is the standard clean-rebuild step.
- **No file-system access.** Paths are resolved textually against the
  payload's `cwd`, so the hook is fast, deterministic and testable.
- **Standard library only**, Python 3.8+, Windows/Linux/macOS path forms.

### 5. Python tools - `scripts/`

- `validate_project.py` reads a UE project (`.uproject`, `Source`, `Plugins`,
  `Config`, `Content`, VCS files) and prints a text or JSON report. Used by
  humans, by Claude in a session, and by the architect subagent.
- `check_toolkit.py` validates the toolkit's own files (JSON, Python syntax,
  settings shape, skill/agent frontmatter, required layout). It also runs as
  part of the unit-test suite.
- `orchestrator.py` is the multi-agent task board: plan validation,
  scheduling, status transitions, handoffs, git audit and reports. It writes
  only its plan file and runs only read-only `git status`.

## What is intentionally not included

- **No MCP server.** Live editor control (spawning actors, editing Blueprints)
  needs an Unreal Editor plugin plus an MCP server; that is listed on the
  roadmap rather than faked here.
- **No automatic builds.** Builds are slow and environment-specific; the
  developer agent runs them only when it knows the command.
