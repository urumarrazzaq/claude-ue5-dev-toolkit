---
name: ue5-code-review
description: Structured review of Unreal Engine 5 C++ and project changes - architecture, performance, UObject memory management and GC safety, replication correctness, code quality, regressions and maintainability. Use when asked to review a diff, branch, pull request or set of UE source files, or before committing gameplay changes.
---

# UE5 Code Review

Review what actually changed, prove each finding, and rank by severity. A
short list of verified problems beats a long list of guesses.

## 1. Collect the change set

```bash
git status --short
git diff --stat <base>...HEAD     # or: git diff --stat (working tree)
git diff <base>...HEAD -- Source/ Plugins/ Config/ *.uproject
```

Read every changed hunk **and** enough surrounding code to understand it
(callers, the class header, overrides of the same virtual, the module's
Build.cs). Binary assets (`.uasset`, `.umap`) cannot be reviewed as text - list
them and note that they need an in-editor review.

## 2. Review checklist (in priority order)

### Correctness and memory (blockers)
- UObject pointers in members/containers not marked `UPROPERTY()` and not
  weak → GC dangling pointer.
- Missing `IsValid()` before using actors/components that can be destroyed;
  use-after-`Destroy()`.
- Delegates/timers bound but never removed in `EndPlay`/`Deinitialize`;
  lambdas capturing `this` without `AddWeakLambda`/weak pointer.
- World/GameMode access in constructors; logic relying on `BeginPlay` order
  between actors.
- `NewObject` with wrong Outer; `CreateDefaultSubobject` outside the constructor.
- `check()` on conditions that can legitimately fail at runtime: it crashes
  Development builds, and is compiled out of Shipping so the bad state then
  continues unchecked. Use `ensure()` plus graceful handling instead.

### Replication (blockers in networked code)
- State mutated without `HasAuthority()` gating, or client-only state assumed
  on server.
- Replicated property missing from `GetLifetimeReplicatedProps` or `Super` call
  missing.
- Server RPC callable from a non-owned actor (silently dropped).
- Reliable RPCs in Tick or high-frequency paths.
- Server RPCs that trust client values (damage, position, cooldowns) without
  validation.
- `OnRep_` logic that the server also needs but never executes.

### Performance (major)
- New `Tick` without need; `bCanEverTick = true` by default on new classes.
- `GetAllActorsOfClass`, `FindComponentByClass`, `LoadObject`/synchronous
  loads, string building or allocations in per-frame paths.
- Hard references to large assets (`TSubclassOf`/object pointers on CDOs) where
  soft references are appropriate.
- Copying large structs/arrays by value; missing `Reserve`.

### Architecture (major/minor)
- Logic in the wrong framework class (UI in pawn, rules in PlayerController,
  gameplay state in widgets).
- New dependency on a module/plugin that the project could avoid; runtime
  module depending on editor modules.
- Duplicating an existing system/subsystem/utility instead of reusing it.
- Public API (`BlueprintCallable`, exported classes, config/ini keys)
  renamed or removed without redirects → breaks Blueprints and saved data.

### Code quality (minor/nit)
- Epic naming and prefixes, `TEXT()` macros, const-correctness, forward
  declarations, generated include last, log categories.
- Dead code, commented-out code, magic numbers that should be `UPROPERTY`
  config.

## 3. Regression analysis

For each changed function/property ask:
- Who calls it? (`rg -n "FunctionName" Source/ Plugins/`)
- Is it exposed to Blueprint or config? Blueprint callers are invisible to
  grep - flag renames/signature changes as **needs editor verification**.
- Did default values change (constructor or `Config/*.ini`)? Existing
  placed actors and saved games keep old serialized values.
- Does behaviour differ between Standalone, Listen Server, Dedicated Server
  and clients?

## 4. Report format

```
## Summary
<one paragraph: what the change does, overall risk>

## Findings
1. [BLOCKER] Source/Game/Weapon.cpp:142 - <problem>.
   Why: <evidence / failure scenario>. Fix: <concrete suggestion>.
2. [MAJOR] ...
3. [MINOR] ...

## Not verified
- <things that need a build, PIE test or in-editor Blueprint check>
```

Severity: **BLOCKER** (crash, data loss, broken multiplayer, build break),
**MAJOR** (likely bug, significant perf/arch problem), **MINOR** (quality),
**NIT** (style). Include file:line for every finding. Do not report
speculative issues as facts - mark them as questions.
