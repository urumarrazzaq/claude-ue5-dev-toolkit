# Unreal Engine 5 Project Instructions

These instructions apply to every Claude Code session in this Unreal Engine
project. Copy this file to the project root (next to the `.uproject`) and edit
the **Project facts** section.

## Project facts (edit for your project)

- Engine version: see `EngineAssociation` in the `.uproject`
  (`python scripts/validate_project.py .` prints it).
- Primary game module: `Source/<ProjectName>/`
- Build command (Windows example):
  `"<UE>/Engine/Build/BatchFiles/Build.bat" <ProjectName>Editor Win64 Development -Project="<abs path>/<ProjectName>.uproject" -WaitMutex`
- Networking: <single-player | listen server | dedicated server>
- Major systems in use: <e.g. Enhanced Input, Gameplay Ability System, CommonUI>

## Working rules

1. **Inspect before implementing.** Read the files you will change and search
   for existing systems (`rg`, Grep/Glob) before writing new code. Reuse the
   project's base classes, subsystems, components, interfaces and utilities
   instead of creating parallel ones.
2. **Respect the engine version.** Check the version before using any API.
   Never assume an engine function, class, macro, specifier, module or console
   variable exists - verify it in the engine source or in existing project
   usage. If you cannot verify it, say so explicitly in your answer.
3. **Keep changes minimal and in scope.** No unrelated refactors, renames or
   reformatting. Match the surrounding style.
4. **Avoid unnecessary Tick.** New actors/components default to
   `bCanEverTick = false`. Prefer events, delegates and timers.
5. **Respect the UObject lifecycle.** Every UObject pointer member is a
   `UPROPERTY()` (`TObjectPtr`) or a weak pointer. No world access in
   constructors. Unbind delegates and clear timers in `EndPlay`.
6. **Consider multiplayer.** For gameplay state, state who has authority,
   what replicates, and which RPCs are needed - even if the answer is
   "single-player only".
7. **Avoid new plugin or module dependencies.** Prefer what the project
   already uses. Any new `Build.cs` dependency or plugin must be justified and
   reported. Runtime modules must never depend on editor modules.
8. **Never overwrite user changes.** Check `git status` before editing. Do
   not revert, reset, checkout, stash or clean the user's work. Do not commit
   or push unless asked.
9. **Explain destructive operations first.** Before deleting files, rewriting
   history, regenerating project files, or running long builds/cooks, say what
   will happen and why, and wait for confirmation. The safety hook blocks the
   most dangerous commands, but it is a backstop, not permission.
10. **Binary assets are off-limits to text tools.** Never edit `.uasset` or
    `.umap` files. Describe the required editor steps instead, and do not
    delete assets from the shell (it bypasses redirectors and reference checks).
11. **Generated folders.** `Binaries/`, `Intermediate/`, `DerivedDataCache/`
    and `Saved/` are generated; never edit files there. Deleting
    `Binaries/` and `Intermediate/` to force a clean rebuild is acceptable
    after telling the user.
12. **Report what you did.** End each task with: files modified/created,
    validation performed (exact build/test command and its result, or
    "not compiled"/"not tested"), and follow-up steps for the user (editor
    restart, Blueprint fixes, regenerate project files).

## Toolkit components

- Skills (`.claude/skills/`): `ue5-cpp`, `ue5-blueprints`, `ue5-ai-systems`,
  `ue5-build-debugging`, `ue5-multiplayer`, `ue5-code-review`,
  `ue5-orchestrator`. Load the one that matches the task.
- Subagents (`.claude/agents/`): `ue5-architect` (read-only planning),
  `ue5-cpp-developer` (implementation), `ue5-code-reviewer` (read-only review),
  `ue5-blueprint-specialist` (read-only asset/editor instructions),
  `ue5-build-engineer` (build logs and validation; read-only).
  For non-trivial features: architect → developer → reviewer.
- Multi-agent orchestration: for features spanning several classes, follow
  the `ue5-orchestrator` skill. Track tasks with `scripts/orchestrator.py`;
  never start tasks it refuses, never let two agents edit the same file at
  once, and never finalise code without an approving review.
- Safety hook (`.claude/hooks/ue5_safety_guard.py`): blocks destructive git and
  deletion commands. If it denies a command, do not try to work around it -
  explain the situation to the user.
- Project validator: `python scripts/validate_project.py <project-dir>`.
