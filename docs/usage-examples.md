# Usage examples

All examples assume the toolkit is installed in a UE project (see
[installation.md](installation.md)) and Claude Code is started from the folder
containing the `.uproject`. Prompts are what you type into Claude Code.

## 1. Get oriented in a large project

```text
Run the project validator and summarise the modules, plugins and any problems.
```

Claude runs `python scripts/validate_project.py .` (adjust the path to where
you copied the script) and summarises the report. For a deeper map:

```text
Use the ue5-architect agent to explain how damage flows from weapon fire to
health changes in this project. Cite files and lines.
```

The architect searches for the relevant classes, traces the call path and
returns an evidence-based map without touching any file.

## 2. Plan, implement and review a feature

```text
Use the ue5-architect agent to plan a stamina system for the player character:
sprinting drains stamina, it regenerates after 2 seconds, and it must work on
a dedicated server.
```

Review the plan, then:

```text
Approved. Use the ue5-cpp-developer agent to implement it. Build with:
"C:/Program Files/Epic Games/UE_5.4/Engine/Build/BatchFiles/Build.bat" ShooterGameEditor Win64 Development -Project="D:/Projects/ShooterGame/ShooterGame.uproject" -WaitMutex
```

The developer follows the `ue5-cpp` and `ue5-multiplayer` skills (component
with `ReplicatedUsing`, server-side drain, timer instead of Tick), runs the
build and reports changed files and the build result. Then:

```text
Use the ue5-code-reviewer agent to review the uncommitted changes.
```

## 3. Fix a build failure

```text
/ue5-build-debugging
The editor build fails. Here is the end of the log: <paste>
```

or point Claude at the log file:

```text
The build failed; read Saved/Logs/ShooterGame.log and %LOCALAPPDATA%/UnrealBuildTool/Log.txt
and fix the first real error.
```

The skill makes Claude find the *first* error, classify it (UHT, compiler,
linker, UBT rules, cook) and check `Build.cs` dependencies before editing.

## 4. Debug AI that won't move

```text
/ue5-ai-systems
My BP_EnemyGrunt stands still in PIE. Its AIController runs BT_Grunt.
Walk me through diagnosing it, and check our C++ AIController for problems.
```

Claude reads the C++ controller, then uses the skill's checklist
(possession → tree running → blackboard keys → decorators → NavMesh/agent
size) and gives in-editor steps for the parts it can't see (the BT asset is
binary).

## 5. Multiplayer correctness

```text
/ue5-multiplayer
Ammo desyncs on clients after reloading. Look at UWeaponComponent.
```

Claude checks `GetLifetimeReplicatedProps`, replication conditions, whether the
reload RPC is called from an owned actor, and whether `OnRep_` logic also runs
on the server.

## 6. Review before committing

```text
/ue5-code-review
Review my staged and unstaged changes before I commit.
```

Output is a severity-ranked list with `file:line`, evidence and suggested
fixes, plus a *Not verified* section (Blueprint call sites, PIE testing).

## 7. What the safety hook looks like in practice

```text
The build is broken, just reset everything to origin/main.
```

Claude tries `git reset --hard origin/main`; the hook denies it and Claude
sees:

```
UE5 safety guard blocked this command:
- 'git reset --hard' discards all uncommitted work and can move the branch
  away from commits. Use 'git stash' or create a backup branch first, and ask
  the user to run the reset themselves if it is really needed.
```

Claude then proposes a safe alternative (stash or backup branch) instead.

Other examples:

| Command Claude attempts | Result |
|---|---|
| `rm -rf Intermediate Binaries` | runs (regenerable folders) |
| `rm -rf Content/Characters` | runs normal permission flow (subfolder, not the protected root) |
| `rm -rf Content` | **denied** |
| `Remove-Item -Recurse -Force .\Source` | **denied** |
| `rm Content/Maps/L_Old.umap` | **ask** - delete in the editor instead |
| `rm -rf $STAGING_DIR/` | **ask** - variable could be empty |
| `git push --force-with-lease` | **ask** |
| `git push --force` | **denied** |

## 8. Blueprint work

```text
/ue5-blueprints
I need BP_Door to open when any player with a key interacts. What's the cleanest setup?
```

Claude recommends a Blueprint Interface or a C++ `UINTERFACE`, writes any C++
needed, and gives node-by-node editor steps. It will not try to edit the
`.uasset` (the settings deny `Edit` on binary assets).
