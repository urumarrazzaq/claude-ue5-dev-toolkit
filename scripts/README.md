# Scripts

All scripts use only the Python standard library (Python 3.8+) and are
read-only: they never modify the files they inspect.

On Windows, replace `python` with `py -3` if `python` is not on your `PATH`.

## `validate_project.py` - Unreal project validator

Inspects an Unreal Engine project directory and prints a structural report.

```bash
python scripts/validate_project.py D:/Projects/ShooterGame
python scripts/validate_project.py D:/Projects/ShooterGame/ShooterGame.uproject
python scripts/validate_project.py . --json > report.json
```

What it reports:

| Area | Details |
|---|---|
| `.uproject` | name, `EngineAssociation` (version or source-build GUID), `FileVersion`, description, target platforms; invalid JSON; multiple `.uproject` files |
| Directories | presence of `Source`, `Content`, `Config`, `Plugins` and the generated folders |
| C++ modules | modules declared in the `.uproject` vs `Source/*/<Module>.Build.cs`; missing `*.Target.cs`; missing editor/game target; empty modules; unknown module `Type` |
| Plugins | plugins enabled/disabled in the `.uproject`; project plugins under `Plugins/` (descriptor validity, module `Build.cs` presence); plugins not listed in the `.uproject` |
| Content | count of `.uasset` / `.umap`; no levels |
| Config | `.ini` files present; missing `DefaultEngine.ini` / `DefaultGame.ini`; `GameDefaultMap` |
| Version control | `.gitignore` covers `Binaries`, `Intermediate`, `Saved`, `DerivedDataCache`; Git LFS rule for `.uasset` |

Engine plugins cannot be verified without the engine installation, so they
are labelled `engine/marketplace (not verified)`.

Exit codes: `0` no errors, `1` errors found, `2` no `.uproject` / bad path.

Example output - the synthetic project from `tests/test_validate_project.py`
plus an extra empty `Source/LegacyTools` module (only the directory prefix of
the paths was shortened):

```
================================================================
 Unreal Engine Project Validation Report
================================================================
Project directory : D:\Projects\ShooterGame
.uproject         : D:\Projects\ShooterGame\ShooterGame.uproject
Project name      : ShooterGame
Engine            : 5.4

Directories
  [x] Source/
  [x] Content/
  [x] Config/
  [x] Plugins/
  [ ] Binaries/
  [ ] Intermediate/
  [ ] Saved/
  [ ] DerivedDataCache/

C++ modules
  - ShooterGame (Runtime, Default) - found
  - LegacyTools (Build.cs only, not in .uproject)
  Targets: ShooterGame.Target.cs, ShooterGameEditor.Target.cs

Plugins
  - EnhancedInput                    enabled   engine/marketplace (not verified)
  - InventoryKit                     enabled   project
  - OnlineSubsystemSteam             disabled  engine/marketplace (not verified)

Content: 1 .uasset, 1 .umap
Config : DefaultEngine.ini, DefaultGame.ini
GameDefaultMap: /Game/Maps/L_Main.L_Main

Findings
  [WARNING] Source module 'LegacyTools' has a Build.cs but is not listed in the .uproject Modules array (it will only build if a target or another module references it).
  [WARNING] Module 'LegacyTools' has no Public/, Private/ or .cpp files.

Result: 0 error(s), 2 warning(s), 0 info
```

## `check_toolkit.py` - toolkit self-check

Validates this repository (or a project the toolkit was copied into):
required files, JSON syntax, Python syntax, `.claude/settings.json`
structure (hook events, handler types, hook script paths, permission rule
shape), skill frontmatter (`name` matches directory, `description` present and
≤ 1024 chars) and subagent frontmatter (`name` matches file, known tools and
model values, referenced skills exist).

```bash
python scripts/check_toolkit.py          # checks the repository it lives in
python scripts/check_toolkit.py D:/Projects/ShooterGame
```

When run against a UE project, the "required files" check expects the full
toolkit layout; the other checks apply to whatever `.claude/` content exists.
