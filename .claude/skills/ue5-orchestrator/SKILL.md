---
name: ue5-orchestrator
description: Coordinate a multi-step Unreal Engine 5 feature across the toolkit's subagents (ue5-architect, ue5-cpp-developer, ue5-blueprint-specialist, ue5-code-reviewer, ue5-build-engineer) with a validated task plan, dependency ordering, file-ownership locks, parallel delegation where safe, mandatory review and a final report. Use for features that touch several classes or modules, or when the user asks for an orchestrated or multi-agent workflow. Not for one-file fixes.
argument-hint: <development task>
---

# UE5 multi-agent orchestrator

You - the main Claude Code session - are the **coordinator**. Claude Code
subagents cannot spawn other subagents, so delegation happens only from here,
with the Agent tool (`subagent_type` = the agent's name). The task board is a
JSON file kept by `scripts/orchestrator.py`; you are the only one who runs it.

Below, `O` means `python scripts/orchestrator.py` run from the project root
(`py -3` instead of `python` on Windows; `Tools/claude/orchestrator.py` if the
scripts were installed there - check with Glob first). The plan defaults to
`.claude/orchestration/plan.json`; pass `--plan` for another.

## Ground rules

- Never claim tasks ran in parallel unless you sent their Agent calls in the
  **same message**. Say "sequential" otherwise.
- Never modify the user's project without their authorization; the request
  itself authorizes the code changes it describes, nothing more (no commits,
  no plugin additions, no asset edits, no builds unless asked).
- Never run git commands that change the repository; never overwrite
  uncommitted user changes; never merge conflicting implementations yourself.
- Each subagent gets the output of `O brief <id>`, not the conversation.

## Workflow

1. **Intake.** Restate the request in one paragraph. Ask only for decisions
   that change the plan (networking model, whether to compile). Then:
   `O init --title "<short name>" --request "<request>"`.
2. **Safety baseline.** `O preflight --project-dir .` records the files that
   already have uncommitted user changes. Not a git repo → tell the user that
   ownership audits are unavailable, and continue only if they agree.
3. **Investigate.** Delegate to `ue5-architect` with the request and the
   facts you already know (engine version, modules) so it does not rediscover
   them. Ask for its normal plan **plus** the orchestrated-mode JSON task list.
4. **Load the plan.** Save the JSON block to
   `.claude/orchestration/tasks.json`, then `O import-tasks
   .claude/orchestration/tasks.json`. The import refuses invalid plans
   (unknown agent, missing fields, cycles, unordered tasks owning the same
   file, binary assets as owned files). Fix the JSON yourself for trivial
   issues or send the errors back to the architect; never weaken a check.
   Show the user `O status` and the decisions; get approval if the plan
   deviates from what they asked.
5. **Schedule.** `O ready` lists pending tasks whose prerequisites are done
   and whose owned files do not overlap each other or any running task.
   Build tasks never run alongside code writers. Tasks touching files with
   uncommitted user changes wait for `O allow-dirty <id> --note "..."`, which
   you run **only** after the user agrees in chat.
6. **Delegate.** `O start <ids...>` (all-or-nothing safety check), then for
   each id send an Agent call with `O brief <id>` as the prompt. Put all the
   calls of one ready batch in a single message so Claude Code runs them
   concurrently. A batch of one runs alone.
7. **Collect.** For each result, save the subagent's JSON handoff to
   `.claude/orchestration/handoffs/<id>.json` and run
   `O handoff <id> --json-file <that file>`. A warning about files outside
   ownership means: stop, inspect with `git diff`, and tell the user before
   anything else. Then move the task: writers → `O set-status <id> review`,
   read-only agents → `O set-status <id> completed`.
   After each batch run `O audit --project-dir .`.
8. **Review.** When all code tasks a reviewer depends on are in `review`, the
   reviewer task becomes ready. Record its verdict with the handoff.
   - `approve` / `approve_with_comments`: complete the reviewer task, then
     `O set-status <code-task> completed` (refused without an approving review).
   - `changes_requested`: `O set-status <code-task> in_progress --note
     "<findings>"`, re-delegate with the brief plus the findings, then
     `O add` a new reviewer task depending on it (e.g. `T5b`).
9. **Build** (only if requested). The `ue5-build-engineer` task becomes ready
   once code tasks are in review/completed and no writer is active. A failed
   build: record the handoff, `O set-status <build-id> failed --note "..."`,
   add a fix task for `ue5-cpp-developer` owning the files named in the
   root cause, then a new review and build task.
10. **Report.** `O report --output .claude/orchestration/report.md` and give
    the user: files changed, review verdicts, build result (or "not
    compiled"), open items and editor steps (`user` tasks).

## Failure handling

| Situation | Action |
|---|---|
| Subagent returns no handoff / malformed JSON | Write the handoff yourself from its output, marking unknowns; never invent results |
| Subagent needs a file it does not own | `O set-status <id> blocked --note ...`; add or re-scope a task; never let two agents share a file concurrently |
| Agent error or timeout | `O set-status <id> failed --note ...`; `O ready` shows dependents waiting on the failure; retry with `O set-status <id> pending` (attempts are counted) or re-plan; stop after 2 failed attempts and ask the user |
| `audit` reports unowned changes | Stop delegating, show `git diff` of those files, ask the user |
| Conflicting implementations | Do not merge them; present both and let the user choose |
| Context getting long | `O decide "..."` for settled decisions, rely on handoff summaries, re-read only via `O brief` / `O status` |

## Optional: worktree isolation

The Agent tool accepts `isolation: "worktree"`, which gives a writer its own
temporary git worktree. Use it only when the user asks for it or for risky
experiments: its changes land on a separate branch that the user must merge,
and you must not merge it yourself. The default is the shared checkout with
disjoint file ownership.

See `docs/multi-agent-architecture.md`, `docs/parallel-workflows.md` and
`docs/context-management.md` for details and limitations.
