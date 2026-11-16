# Context management

Large Unreal projects exhaust context quickly: engine-scale headers, long
build logs, many modules. The orchestrator keeps each agent's context small
and keeps the coordinator's context durable across a long session.

## Principles

1. **Investigate once.** The architect explores; everyone else reads its
   conclusions.
2. **Each agent sees one task.** A subagent starts with a fresh context and
   receives only `orchestrator.py brief <id>` - not the conversation.
3. **Pass summaries, not transcripts.** Agents return a structured handoff;
   the coordinator stores it and forwards only summaries downstream.
4. **Write decisions down.** Settled decisions go into the plan, so they
   survive context compaction and are injected into every later brief.
5. **The plan file is the source of truth**, not the coordinator's memory.

## Giving agents only relevant context

The architect's task JSON carries the context for each agent:

- `files` - what the agent may change (and must read before changing).
- `context_files` - the few existing files that show the conventions or
  APIs to follow, e.g. a sibling ability class. This replaces "search the
  project for how abilities are done".
- `expected_output` and `validation` - so the agent knows when it is done and
  the reviewer knows what to check.

`brief` assembles these with upstream handoff summaries and plan decisions:

```
# Task T3 (ue5-cpp-developer)
Feature: Dash Strike ability
## Goal                       one task only
## Expected output
## Validation required
## Files you own (modify ONLY these)
## Read these first (do not re-explore the whole project)
## Upstream handoffs          T1's summary + files changed, not its transcript
## Decisions already made (do not reopen)
## Rules                      ownership, no git mutations, no binary assets
JSON handoff template
```

Unrelated tasks, other agents' files and the rest of the conversation are
not included.

### Keeping task descriptions focused

- One outcome per task; if the description needs "and also", split it.
- Name classes and files, not goals ("Create UDashStrikeDamageExecution in
  Abilities/" rather than "handle damage").
- Prefer 3-6 `context_files`. If an agent needs more, the task is too broad
  or the architect has not finished investigating.
- Put the *why* in `decisions`, not repeated in every description.

## Avoiding repeated investigations

- The coordinator passes facts it already has (engine version from
  `validate_project.py`, module list) to the architect.
- The architect's plan includes file:line evidence; agents reuse it through
  `context_files` instead of searching again.
- Reviewers get the exact changed files from upstream handoffs; they run
  `git diff` on those files rather than reviewing the whole tree.
- Build engineers receive the build command from `CLAUDE.md`; they read the
  first error, not the whole log.

## Structured handoff reports

Every orchestrated agent ends with this JSON (the template is in its brief):

```json
{
  "task": "T1",
  "status": "review",
  "summary": "UGA_DashStrike: LocalPredicted dash via root-motion task; server-only sweep applies DamageEffectClass once per actor.",
  "files_changed": ["Source/ShooterGame/Public/Abilities/GA_DashStrike.h",
                    "Source/ShooterGame/Private/Abilities/GA_DashStrike.cpp"],
  "decisions": ["Hit detection is a single end-of-dash sweep, not per-tick"],
  "issues": ["UAbilityTask_ApplyRootMotionConstantForce signature unverified for 5.4"],
  "validation_result": "Not compiled (build is scheduled as T6)",
  "followups": []
}
```

Reviewer tasks add `"verdict": "approve" | "approve_with_comments" |
"changes_requested"`. The coordinator saves it under
`.claude/orchestration/handoffs/<id>.json` and runs
`orchestrator.py handoff <id> --json-file ...`. The CLI:

- rejects a missing summary, non-list fields and invalid verdicts;
- appends `decisions` to the plan's decision log;
- flags `files_changed` outside the task's ownership;
- keeps earlier handoffs in `previous_handoffs` when a task is reworked, so
  the report still lists every file the task touched.

If an agent returns no valid JSON, the coordinator writes the handoff from the
agent's prose, marking anything it could not confirm - it never invents
results such as "compiled successfully".

## Long sessions

| Situation | Practice |
|---|---|
| A decision is settled | `orchestrator.py decide "..."` - it appears in every later brief |
| Context is compacted | Re-read state with `status` and `brief`; do not rely on memory of earlier turns |
| Resuming in a new session | The plan file holds tasks, statuses, handoffs, decisions and the git baseline; start with `status`, `validate`, `audit` |
| Many completed tasks | Downstream briefs include only direct prerequisites' summaries |
| Large build logs | Build engineer quotes the first error verbatim and summarises the rest |

The plan lives in `.claude/orchestration/`. Whether to commit it is your
choice; it contains task descriptions and summaries, no secrets by design -
but agent summaries may quote code, so review it before sharing.

## Limitations

- The brief controls what the coordinator *sends*; a subagent can still read
  other files with its tools. The instructions tell it not to re-explore.
- Summaries are only as accurate as the agent that wrote them; reviewers
  check the actual diff, not just the summary.
- Context sizes and compaction behaviour are Claude Code's; this toolkit
  does not measure tokens.
