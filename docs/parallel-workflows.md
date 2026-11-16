# Parallel workflows

How the orchestrator decides what may run at the same time, and how the
coordinator actually runs it in Claude Code.

## What "parallel" means here

Claude Code runs several subagents concurrently when the main session sends
several **Agent** tool calls in the **same assistant message**. That is the
only mechanism used. Consequences:

- A batch from `ready` is delegated by putting one Agent call per task into a
  single message, each with the output of `orchestrator.py brief <id>` as its
  prompt.
- Calls sent in separate messages run one after another. The final report
  must not describe them as parallel.
- How many agents Claude Code actually runs at once is up to Claude Code;
  `ready --max N` (default 3) only limits how many the coordinator starts.
- Subagents cannot start their own subagents, so there is no nested fan-out.

## Safety rules enforced by `orchestrator.py`

| Rule | Where | How |
|---|---|---|
| Two tasks never modify the same file concurrently | `validate`, `ready`, `start` | Any two tasks whose `files` overlap (same path, or a directory prefix ending in `/`; case- and separator-insensitive) must be ordered by `depends_on`, otherwise the plan is rejected. At run time a task cannot start while another in-progress task owns an overlapping file. |
| Dependencies are respected | `ready`, `start`, `set-status` | A task starts only when each prerequisite is `completed` or (for non-manual tasks) in `review`. Cycles and unknown ids are rejected. |
| No build while code is changing | `ready`, `start` | `ue5-build-engineer` tasks never run alongside a running writer, and writers never start while a build runs. |
| No overwriting of uncommitted user changes | `preflight`, `ready`, `start` | `preflight` records `git status` before agents work. Tasks owning those files wait until the user approves (`allow-dirty`, run by the coordinator only after a yes in chat). |
| No conflicting git operations | agent prompts, safety hook | Agents may only run read-only git commands; the hook denies history-destroying ones; nobody commits, stashes, checks out or merges during orchestration. |
| Review before finalising | `set-status` | Code tasks reach `completed` only via `review` and an approving reviewer task completed after the latest change. |
| No automatic merging | skill, worktree policy | Conflicting implementations are presented to the user. Worktree branches are never merged by the coordinator. |
| Start is all-or-nothing | `start` | If any task in `start T1 T2 T3` fails a check, none is started. |

## Designing tasks that can run in parallel

The architect decides how much parallelism is possible. Good splits:

- **By file set.** New ability class (T1) and new damage calculation (T2) own
  different files → same batch.
- **Shared hotspots get one owner.** `Build.cs`, `DefaultGame.ini`,
  `*.uproject`, the character class and other frequently-touched files belong
  to exactly one task, or the tasks touching them are chained with
  `depends_on`.
- **Directory ownership for new subsystems.** `files: ["Source/Game/Inventory/"]`
  reserves a whole folder; anything else touching that folder must wait.
- **Read-only work runs freely.** Blueprint specialist and reviewer tasks own
  no files, so they never conflict with each other.

Example from the combat-ability plan:

```
Batch 1 (parallel):  T1 GA_DashStrike.h/.cpp        T2 DashStrikeDamageExecution.h/.cpp
Batch 2 (parallel):  T3 ShooterCharacter.h/.cpp     T4 editor instructions (read-only)
Batch 3 (parallel):  T5 review T1-T3                T6 build validation (no writer active)
Manual:              T7 user creates assets and play-tests
```

## Worktree isolation (opt-in)

The Agent tool accepts `isolation: "worktree"`, running a subagent in a
temporary git worktree. It gives hard isolation (an agent cannot touch the
main checkout) at a cost:

- the agent's changes end up on a separate branch;
- combining branches is a merge the **user** decides on - the coordinator
  never merges them automatically;
- Unreal builds in a fresh worktree need their own `Intermediate/` and can be
  slow.

Use it for risky experiments or when the user asks. The default is the shared
checkout plus file ownership, which keeps one build tree and needs no merges.

## When not to parallelise

- The feature touches one or two files: use `ue5-cpp-developer` directly.
- Tasks need a design decision from another task's result: chain them.
- The project has no git repository: ownership audits are unavailable; prefer
  sequential execution and say so to the user.
- The user wants to watch each step: run batches of one.

## Reading `ready` output

```
Ready to start together (no shared files, prerequisites done):
  T3  ue5-cpp-developer         medium  Grant GA_DashStrike ...
  T4  ue5-blueprint-specialist  medium  Specify the editor work ...
  waiting  T5: waiting for ['T3']
  waiting  T7: waiting for ['T3', 'T4', 'T6']
```

Possible waiting reasons: `waiting for [...]` (prerequisites),
`prerequisite failed: [...]`, `needs user approval: uncommitted changes in [...]`,
`<id> is modifying [...]` (file lock), `build validation must not run while
<id> is editing code`, `<id> is compiling`, `parallel limit N reached`.
`ready --json` returns the same data for scripting.
