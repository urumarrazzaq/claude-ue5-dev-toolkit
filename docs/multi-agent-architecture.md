# Multi-agent architecture

The orchestrator splits a large Unreal Engine task across specialised Claude
Code subagents while a single coordinator keeps the work ordered, conflict-free
and reviewed. It is built only from features Claude Code actually provides:

| Need | Claude Code feature used | Toolkit file |
|---|---|---|
| Specialised workers with limited tools | Subagents (`.claude/agents/*.md`, YAML frontmatter `name`, `description`, `tools`, `model`, `skills`) | `.claude/agents/ue5-*.md` |
| A coordinator that delegates | The main session calling the **Agent** tool with `subagent_type: "<agent name>"` | `.claude/skills/ue5-orchestrator/SKILL.md` |
| Parallel work | Several Agent tool calls sent in **one** assistant message run concurrently | same |
| Optional isolation | Agent tool `isolation: "worktree"` (temporary git worktree) | same, opt-in only |
| Shared, durable task state | A JSON file the coordinator updates through a stdlib Python CLI | `scripts/orchestrator.py` |
| Guard rails on shell commands | `PreToolUse` hook | `.claude/hooks/ue5_safety_guard.py` |

There is no custom runtime, daemon or agent framework. Nothing here
"simulates" concurrency: if the coordinator sends calls one per message, they
run sequentially, and the coordinator must say so.

## Components

```
                   user request
                        │
        ┌───────────────▼────────────────┐
        │ Coordinator = main session     │  skill: ue5-orchestrator
        │  - runs scripts/orchestrator.py│  (only process that writes plan.json)
        │  - calls the Agent tool        │
        └──┬────────┬────────┬────────┬──┘
   Agent   │        │        │        │       one message = one parallel batch
           ▼        ▼        ▼        ▼
     architect  cpp-dev ×N  blueprint  reviewer / build engineer
     (read)     (writes     (read)     (read; build runs the compiler
                owned files                 only when requested)
                only)
           │        │        │        │
           └────────┴── JSON handoff ─┘──► orchestrator.py handoff ──► plan.json
```

### Coordinator

The main Claude Code session, following the `ue5-orchestrator` skill
(`/ue5-orchestrator <task>` or automatic when the description matches).
Claude Code subagents cannot spawn subagents, so the coordinator *must* be the
main session. It owns:

- the plan file (`.claude/orchestration/plan.json`), changed only through
  `scripts/orchestrator.py`;
- all communication with the user (approvals, decisions, final report);
- scheduling: what starts, what waits, what is re-planned.

### Agents

| Agent | File | Role | Writes files? | Tools |
|---|---|---|---|---|
| Project Architect | `ue5-architect.md` | Investigates, plans, produces the task list JSON | No | Read, Grep, Glob, Bash |
| C++ Developer | `ue5-cpp-developer.md` | Implements C++ in the files its task owns | **Yes** (owned files only) | Read, Grep, Glob, Edit, Write, Bash |
| Blueprint Specialist | `ue5-blueprint-specialist.md` | Finds related assets, writes editor instructions; uses an editor MCP integration only if configured and allowed | No | Read, Grep, Glob, Bash (+ MCP tools you add) |
| Build Engineer | `ue5-build-engineer.md` | Analyses UBT/UHT/linker logs, runs the build when asked, proposes Build.cs/plugin fixes | No (compiles) | Read, Grep, Glob, Bash |
| Code Reviewer | `ue5-code-reviewer.md` | Reviews changes: bugs, regressions, GC/memory, replication, conventions; verdict | No | Read, Grep, Glob, Bash |
| `user` (pseudo-agent) | - | Editor work on binary assets, play-testing, approvals | - | - |

The script's registry (`AGENTS` in `orchestrator.py`) records each agent's
role and whether it writes. `validate` cross-checks that against the agent
file's `tools:` line, so a reviewer that silently gained `Edit` is reported.

## Task model

A plan is a JSON object with `title`, `request`, `decisions`, `git_baseline`
and `tasks`. Each task:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `^[A-Za-z][A-Za-z0-9_.-]*$`, unique |
| `description` | yes | One focused change |
| `agent` | yes | A registered agent |
| `priority` | yes | `critical`, `high`, `medium`, `low` (scheduling order) |
| `depends_on` | yes | Task ids that must deliver first |
| `status` | yes | `pending`, `in_progress`, `blocked`, `review`, `completed`, `failed` |
| `expected_output` | yes | What the agent must return |
| `validation` | yes | Non-empty list of checks the reviewer verifies |
| `files` | writers | Files/directories (`/`-terminated) the task may create or modify |
| `context_files` | no | Files the agent should read instead of exploring |
| `allow_dirty` | no | Set only via `allow-dirty` after the user agrees |
| `history`, `handoff`, `previous_handoffs`, `attempts` | managed | Written by the CLI |

### Status transitions

```
pending ──► in_progress ──► review ──► completed
   │  ▲          │  ▲          │
   │  │          │  └──────────┘ (changes requested)
   ▼  │          ▼
 blocked ◄──── (any active)        failed ──► pending (retry, attempts+1)
```

| From | Allowed to |
|---|---|
| pending | in_progress, blocked, failed |
| in_progress | review, completed*, blocked, failed |
| blocked | pending, in_progress, failed |
| review | completed**, in_progress, failed |
| failed | pending |
| completed | - (terminal; add a new task instead) |

\* only read-only tasks; code tasks must pass through `review`.
\*\* code tasks need a completed `ue5-code-reviewer` task that depends on them,
with verdict `approve`/`approve_with_comments`, finished **after** the task
last entered `review` (an approval of an older version does not count).
Reviewer tasks need a recorded verdict before completing. `blocked` and
`failed` require a `--note`.

### Dependency semantics

A prerequisite is satisfied when it is `completed`, or when it is in `review`
(its output exists on disk) for every agent except `user`. This lets T3 build
on T1's delivered code so one review can cover the whole chain - otherwise a
reviewer that depends on both would deadlock. Manual `user` tasks wait for
fully completed (approved) prerequisites. If a reviewed prerequisite is sent
back for rework, `validate` warns that its dependents must be re-checked.

## Orchestration workflow

1. **Intake** - coordinator restates the task, asks only plan-changing
   questions, `init`.
2. **Baseline** - `preflight` records files with uncommitted user changes.
3. **Investigate** - `ue5-architect` maps the project and returns a plan plus
   the task JSON.
4. **Load** - `import-tasks` validates and refuses unsafe plans.
5. **Schedule** - `ready` lists the next conflict-free batch.
6. **Delegate** - `start` the batch, send each `brief` to its agent in one
   message.
7. **Collect** - record each JSON `handoff`, move writers to `review`,
   read-only tasks to `completed`, `audit` the working tree.
8. **Review** - the reviewer task becomes ready; verdict decides completion
   or rework.
9. **Build** - only when requested; never alongside code writers.
10. **Report** - `report` summarises tasks, files, verdicts, build result,
    decisions and open items.

The step-by-step commands are in the skill; a full run is in
[example-development-session.md](example-development-session.md).

## Failure handling

| Failure | Detection | Response |
|---|---|---|
| Invalid plan (cycle, unknown agent, overlapping owners, binary asset) | `import-tasks` / `validate` exit 1 | Plan not saved; fix the JSON or return errors to the architect |
| Starting a task too early or into a conflict | `start` refuses, all-or-nothing | Wait; `ready` explains each waiting task |
| Agent edits a file it does not own | handoff `ownership_violations`, `audit` | Stop, show `git diff`, ask the user |
| Agent fails / times out | coordinator | `set-status failed --note`; dependents shown as blocked by the failure; retry (attempts counted) or re-plan; ask the user after 2 failures |
| Review requests changes | verdict `changes_requested` | Code task back to `in_progress`, new reviewer task |
| Build fails | build engineer handoff | build task `failed`, fix task + new review + new build |
| Build result outdated by later rework | `report` | Flagged `STALE` |
| Two plausible implementations conflict | coordinator | Never merged automatically; the user chooses |

## Limitations

- **Ownership is enforced by schedule, not by the file system.** Claude Code
  cannot restrict a subagent's `Edit` tool to a path list. The orchestrator
  prevents conflicting tasks from being *scheduled* together, instructs agents
  to touch only owned files, flags handoffs that report other files, and
  `audit` compares `git status` with ownership after each batch. A
  misbehaving agent can still edit an unowned file; it will be detected, not
  prevented. Use `isolation: "worktree"` when you need hard isolation.
- **`audit` needs git.** Without a git repository there is no baseline, so
  uncommitted-change protection and audits are unavailable.
- **Audit granularity is per file.** If an approved agent edits a file the
  user had already modified, `audit` cannot separate the two sets of changes;
  that is why such tasks need explicit user approval first.
- **The coordinator is an LLM following a skill.** The CLI refuses unsafe
  transitions, but it cannot force the coordinator to call it. The skill and
  `CLAUDE.md` instruct it to; review the final report.
- **Concurrency depends on Claude Code.** Calls in one message run
  concurrently in current Claude Code versions; the number that actually run
  at once, and background execution, are controlled by Claude Code, not this
  toolkit.
- **No automated end-to-end test with live agents.** Tests cover the CLI and
  the demo uses simulated agent output; real agent quality depends on the
  model and the project.
- **Compilation is never implied.** If no build task ran, the report says the
  code is not compiled.
