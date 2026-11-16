# Example development session

**Request:** "Implement a new multiplayer combat ability in Unreal Engine."

This walk-through has two parts:

1. **What the coordinator does in a real Claude Code session** - which Agent
   calls it sends, what each agent receives and returns.
2. **Actual output of the task board** for the same plan, produced by
   `python examples/orchestration/demo_session.py`. That script runs the real
   `scripts/orchestrator.py` against a throwaway git repository with a tiny
   synthetic UE project. **Agent outputs in the demo are simulated** (the
   script writes the files and handoffs an agent would produce), because
   automated tests cannot run Claude subagents or Unreal Engine. Everything
   printed by `orchestrator` is genuine.

The project in the example ("ShooterGame", UE 5.4, listen server) already
uses the Gameplay Ability System with a `UShooterGameplayAbility` base class
and a `UShooterAttributeSet` containing `AttackPower` and `Armor`. The user has
uncommitted work in `ShooterCharacter.cpp`.

## Part 1 - the session

### 1. Intake

```
User: /ue5-orchestrator Implement a new multiplayer combat ability: a dash
      that damages enemies it passes. Compile it when done.
```

The coordinator restates the task, confirms the networking model (listen
server, from `CLAUDE.md`) and that compilation was requested, then runs:

```bash
python scripts/orchestrator.py init --title "Dash Strike ability" --request "Implement a new multiplayer combat ability..."
python scripts/orchestrator.py preflight --project-dir .
```

`preflight` records that `ShooterCharacter.cpp` has uncommitted user changes.

### 2. Architect investigates

One Agent call (`subagent_type: "ue5-architect"`), with the facts already
known (engine 5.4, module `ShooterGame`, GAS in use) so it does not rediscover
them. The architect reads `ShooterGameplayAbility.h`, `GA_Sprint.cpp`,
`ShooterAttributeSet.h` and `ShooterCharacter.cpp`, and returns its
normal plan (context, current architecture, design, risks) plus the task
JSON. Its first draft gave `ShooterCharacter.cpp` to both the damage task
(T2) and the grant task (T3); `import-tasks` rejected it (Part 2, step 3).
The coordinator sent the error back; the corrected plan is
[`examples/orchestration/combat-ability.tasks.json`](../examples/orchestration/combat-ability.tasks.json):

| ID | Agent | Depends on | Owns |
|---|---|---|---|
| T1 | ue5-cpp-developer | - | `GA_DashStrike.h/.cpp` |
| T2 | ue5-cpp-developer | - | `DashStrikeDamageExecution.h/.cpp` |
| T3 | ue5-cpp-developer | T1 | `ShooterCharacter.h/.cpp` |
| T4 | ue5-blueprint-specialist | T1, T2 | - (editor instructions) |
| T5 | ue5-code-reviewer | T1, T2, T3 | - |
| T6 | ue5-build-engineer | T1, T2, T3 | - (compiles) |
| T7 | user | T1, T2, T3, T4, T6 | - (create assets, 2-client PIE test) |

Plan decisions recorded: reuse the existing GAS setup; server-authoritative
damage through a GameplayEffect.

### 3. Batch 1 - T1 and T2 in parallel

`ready` lists T1 and T2 (disjoint files, no prerequisites). The coordinator
runs `start T1 T2`, then sends **one message containing two Agent calls**,
both `subagent_type: "ue5-cpp-developer"`, with prompts from `brief T1` and
`brief T2`. Because both calls are in the same message, Claude Code runs them
concurrently.

The T1 brief contains only: the goal, expected output, validation checks
(`HasAuthority()` for damage, `UPROPERTY` on UObject members,
`CommitAbility`), its two owned files, two context files, the plan
decisions, the rules and the JSON handoff template.

T2's developer edits `ShooterGame.Build.cs`, which it does not own. The
post-batch `audit` reports it; the coordinator stops, shows the user the
diff and asks. The user says the dependency already exists, so the edit is
undone. Both handoffs are recorded and both tasks move to `review`.
Completing T1 now is refused - no review yet.

### 4. Batch 2 - T3 and T4

T3 owns `ShooterCharacter.cpp`, which has the user's uncommitted work, so
`ready` holds it: *needs user approval*. The coordinator asks:

```
Coordinator: T3 needs to add the ability grant to ShooterCharacter.cpp,
             which has your uncommitted changes. The agent will edit on top
             of them and not revert anything. Proceed?
User:        Yes.
```

Only then `allow-dirty T3 --note "user approved in chat"`. T3 (developer)
and T4 (Blueprint specialist) are delegated in one message. T4 returns
editor steps (GE_DashStrike_Damage, GE_DashStrike_Cooldown, IA_DashStrike,
montage notify) as `followups`.

### 5. Batch 3 - review and build

With no writer active, T5 (reviewer) and T6 (build engineer) are delegated
together. The reviewer reads the files listed in the upstream handoffs and
their `git diff`, checks every task's `validation` list, and returns:

```json
{"task": "T5", "status": "completed", "verdict": "changes_requested",
 "summary": "Damage applied on server only; one blocking issue.",
 "issues": ["[MAJOR] Source/ShooterGame/Private/Abilities/GA_DashStrike.cpp:42 - hit actors not de-duplicated across sweep results"]}
```

The build engineer runs the build command from `CLAUDE.md` and reports the
exact command and result. (In the demo there is no engine, so it reports
`not run: build command unknown` - the report never turns that into
"compiled".)

### 6. Rework and re-review

`set-status T1 in_progress --note "T5: de-duplicate hit actors"`, a new
developer call with the T1 brief plus the finding, handoff, `review`, then a
new reviewer task T5b (`add --id T5b --agent ue5-code-reviewer --depends-on
T1 T2 T3 ...`). T5b approves; T1, T2, T3 can now be completed. T5's earlier
verdict would not have counted even if it had been an approval, because T1
changed after it.

Because T1 changed after the build, the report marks T6 as **STALE**; in a
real session the coordinator adds a new build task (T6b) before finishing.

### 7. Report

`report --output .claude/orchestration/report.md`; the coordinator tells the
user: files changed, review verdicts, build result, decisions, and the
remaining manual task T7 (create the assets, play-test with two clients).

## Part 2 - actual task-board output

Excerpts from `python examples/orchestration/demo_session.py` (temporary
paths shortened to `<tmp>`; long lines trimmed with `...`):

```
=== 3. Architect's first draft is rejected: T2 and T3 both own ShooterCharacter.cpp ===
$ orchestrator import-tasks <tmp>\ShooterGame\.claude\orchestration\draft.tasks.json
  REFUSED refusing to save an invalid plan:
    - file ownership conflict: tasks T2 and T3 both modify ['Source/ShooterGame/Private/Character/ShooterCharacter.cpp'] but neither depends on the other, so they could run concurrently

=== 4. Corrected plan imported and validated ===
$ orchestrator import-tasks "<toolkit>\examples\orchestration\combat-ability.tasks.json"
  Imported 7 task(s) into <tmp>\ShooterGame\.claude\orchestration\plan.json
$ orchestrator validate
  Plan VALID: 7 task(s), 0 error(s), 0 warning(s)
$ orchestrator preflight --project-dir .
  Baseline already recorded at 2026-11-16T18:52:51+00:00 (1 dirty file(s)); use --refresh to replace it
  ATTENTION T3 owns files with uncommitted user changes: ['Source/ShooterGame/Private/Character/ShooterCharacter.cpp']

=== 5. Batch 1: independent tasks T1 and T2 (disjoint files) ===
$ orchestrator ready
  Ready to start together (no shared files, prerequisites done):
    T1       ue5-cpp-developer          high     Create UGA_DashStrike ...
    T2       ue5-cpp-developer          high     Create UDashStrikeDamageExecution ...
    waiting  T5: waiting for ['T1', 'T2', 'T3']
    waiting  T3: waiting for ['T1']
    ...
$ orchestrator start T1 T2
  In progress: T1, T2

=== 6. Ownership audit catches the unowned Build.cs edit ===
$ orchestrator audit --project-dir .
  PROBLEM Source/ShooterGame/ShooterGame.Build.cs: changed but no task owns it
  Audit found 1 problem(s)
...
$ orchestrator set-status T1 completed
  REFUSED task T1: no completed ue5-code-reviewer task with verdict approve/approve_with_comments since it entered review

=== 7. Batch 2: T3 needs approval for the user's dirty file; T4 runs meanwhile ===
$ orchestrator ready
  Ready to start together (no shared files, prerequisites done):
    T4       ue5-blueprint-specialist   medium   Specify the editor work ...
    waiting  T5: waiting for ['T3']
    waiting  T3: needs user approval: uncommitted changes in ['Source/ShooterGame/Private/Character/ShooterCharacter.cpp']
    waiting  T6: waiting for ['T3']
    waiting  T7: waiting for ['T1', 'T2', 'T3', 'T4', 'T6']

=== 8. Batch 3: review and build validation (no writer active) ===
$ orchestrator ready
  Ready to start together (no shared files, prerequisites done):
    T5       ue5-code-reviewer          high     Review T1-T3 ...
    T6       ue5-build-engineer         medium   Compile ShooterGameEditor Win64 Development ...
    waiting  T7: waiting for ['T1', 'T2', 'T3', 'T6']

=== 10. Final audit, status and report ===
$ orchestrator audit --project-dir .
  Audit clean
$ orchestrator report
  ...
  ## Files changed
  - Source/ShooterGame/Private/Abilities/DashStrikeDamageExecution.cpp
  - Source/ShooterGame/Private/Abilities/GA_DashStrike.cpp
  - Source/ShooterGame/Private/Character/ShooterCharacter.cpp
  - Source/ShooterGame/Public/Abilities/DashStrikeDamageExecution.h
  - Source/ShooterGame/Public/Abilities/GA_DashStrike.h
  - Source/ShooterGame/Public/Character/ShooterCharacter.h

  ## Open items
  - Follow-up from T4: Create GE_DashStrike_Damage (Executions: UDashStrikeDamageExecution)
  - Follow-up from T4: Add InputTag.Ability.Secondary to IMC_Default
  - T7 is pending

  ## Build validation
  - T6 (completed): not run: build command unknown
    - STALE: ['T1'] changed after this build validation; re-run it

Demo completed successfully.
```

Run it yourself (add `--keep` to inspect the generated project and plan):

```bash
python examples/orchestration/demo_session.py
```
