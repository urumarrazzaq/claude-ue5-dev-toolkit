---
name: ue5-build-debugging
description: Diagnose and fix Unreal Engine 5 build, compile, link, cook and packaging failures - Unreal Build Tool (UBT), Unreal Header Tool (UHT), Build.cs/Target.cs rules, plugin and module dependencies, Development vs Shipping configurations, RunUAT BuildCookRun and build log analysis. Use when a UE project fails to compile, link, generate project files, cook or package, or when reading UBT/UHT/UAT logs.
---

# UE5 Build Debugging

Work from evidence: get the **first** error from the log, identify which stage
produced it (UHT, compiler, linker, UBT rules, cook, staging), then fix the
cause. Later errors are usually cascades.

## 1. Locate the tools and logs

Paths below use `<UE>` for the engine root (e.g. `C:/Program Files/Epic Games/UE_5.4`
for launcher installs; source builds live wherever the user cloned them).
Read `EngineAssociation` from the `.uproject` to know which engine is expected.

| What | Windows | Linux / macOS |
|---|---|---|
| Build a target | `<UE>/Engine/Build/BatchFiles/Build.bat` | `<UE>/Engine/Build/BatchFiles/Linux/Build.sh` / `Mac/Build.sh` |
| Generate project files | `<UE>/Engine/Build/BatchFiles/GenerateProjectFiles.bat -project="<path>.uproject" -game` | `GenerateProjectFiles.sh` in the same platform folder |
| Automation (cook/package) | `<UE>/Engine/Build/BatchFiles/RunUAT.bat` | `RunUAT.sh` |
| UBT logs | `%LOCALAPPDATA%/UnrealBuildTool/Log.txt` | `~/.config/Epic/UnrealBuildTool/Log.txt` (Linux) |
| UAT logs | `<UE>/Engine/Programs/AutomationTool/Saved/Logs/` | same, relative to engine |
| Editor/game logs | `<Project>/Saved/Logs/<Project>.log` | same |

Typical editor build (Windows):

```
"<UE>/Engine/Build/BatchFiles/Build.bat" <Project>Editor Win64 Development -Project="<abs path>/<Project>.uproject" -WaitMutex
```

Build commands can take many minutes: run them in the background and read the
tail of the output, rather than blocking. Close the editor before building the
editor target, or use Live Coding (Ctrl+Alt+F11) for function-body changes only.

## 2. Classify the failure

**Unreal Header Tool (UHT)** - errors mention `.h` files and reflection:
- `Missing '*' in Expected a pointer type` / `Unrecognized type` → type used in
  a `UPROPERTY`/`UFUNCTION` is not reflected or not included/forward-declared.
- `#include found after .generated.h file` → the generated include must be last.
- `Class 'X' has no GENERATED_BODY` / `Unable to find 'class', 'delegate'...` →
  macro missing or misplaced.
- Specifier errors (e.g. `BlueprintReadWrite` on a private member without
  `AllowPrivateAccess`) → fix the specifier.

**Compiler (MSVC `error C####` / Clang `error:`)**
- `C2027 use of undefined type` / `incomplete type` → forward declaration only;
  add the include in the .cpp.
- `C2039 'X' is not a member of 'Y'` → API does not exist in this engine
  version; verify in engine source instead of guessing.
- `cannot open include file` → missing module dependency in Build.cs, or wrong
  include path (UE uses module-relative paths like `"GameFramework/Character.h"`).
- Deprecation warnings treated as errors → follow the replacement the warning
  names.

**Linker (`LNK2019`/`LNK2001 unresolved external symbol`, `undefined reference`)**
- Module containing the symbol is not in `Public/PrivateDependencyModuleNames`.
- Class/function not exported: missing `<MODULE>_API` on the class or function.
- Declared but never defined (e.g. `BlueprintNativeEvent` without
  `_Implementation`, RPC without `_Implementation`/`_Validate`).
- Static/inline function defined in a .cpp but used from another module.

**UBT rules / project setup**
- `Unable to find plugin 'X'` → plugin referenced in `.uproject`/`.uplugin`
  but missing from `Plugins/` or the engine; or disabled.
- `Module 'X' could not be found` / circular dependency warnings → fix
  `Build.cs` dependencies; break cycles with interfaces or by moving code.
- `Building would modify the engine` / `BuildEnvironment` conflicts → installed
  engines require `BuildEnvironment = TargetBuildEnvironment.Shared`; unique
  build environments need a source engine.
- Stale state: delete `Binaries/` and `Intermediate/` (regenerable; the
  safety hook allows this) and regenerate project files. Never delete
  `Source/`, `Content/`, `Config/` or `Plugins/`.

## 3. Build.cs and Target.cs

```csharp
// Source/MyGame/MyGame.Build.cs
public class MyGame : ModuleRules
{
    public MyGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "InputCore" });
        PrivateDependencyModuleNames.AddRange(new string[] { });
        if (Target.bBuildEditor)
        {
            PrivateDependencyModuleNames.Add("UnrealEd"); // only for editor-only code paths
        }
    }
}
```

- Public deps: types appear in your public headers. Private deps: .cpp only.
- Do not add `UnrealEd` (or any editor module) to a runtime module without the
  `bBuildEditor` guard - it breaks packaged builds.
- `Target.cs` (`<Project>.Target.cs`, `<Project>Editor.Target.cs`) sets
  `Type`, `DefaultBuildSettings`, `IncludeOrderVersion` and
  `ExtraModuleNames`. A new game module must be listed there (and in the
  `.uproject` `Modules` array) to be built.

## 4. Plugin dependencies

- `.uplugin` `Modules` entries declare `Type` (`Runtime`, `Editor`,
  `UncookedOnly`, `DeveloperTool`...) and `LoadingPhase`.
- If plugin A's code uses plugin B, list B in A's `.uplugin` `Plugins` array
  **and** B's module in A's `Build.cs`.
- Runtime modules must not depend on Editor modules - packaging will fail.
- Content-only plugins need `"CanContainContent": true`.

## 5. Development vs Shipping

| | Debug/DebugGame | Development | Shipping |
|---|---|---|---|
| Optimised | engine yes (DebugGame), game no | yes | yes |
| `check`/`ensure` | on | on | `check` compiled out |
| Console/logs | yes | yes | disabled by default |
| `UE_BUILD_SHIPPING` | 0 | 0 | 1 |

Shipping-only failures usually come from code inside `#if !UE_BUILD_SHIPPING`
blocks, missing includes hidden by unity builds, or editor-only symbols.
Reproduce unity issues with `-DisableUnity` (UBT argument) or
`bUseUnityBuild = false` temporarily in the Target.cs.

## 6. Cooking and packaging

```
"<UE>/Engine/Build/BatchFiles/RunUAT.bat" BuildCookRun -project="<abs>.uproject" -platform=Win64 -clientconfig=Shipping -build -cook -stage -pak -archive -archivedirectory="<out>" -noP4 -utf8output
```

Cook failures to look for in the UAT log:
- `LogCook: Error:` and `LogLinker: Warning: ... Failed to load` → broken/missing
  asset references; fix redirectors (`Fix Up Redirectors` in the editor) and
  missing assets.
- `Error: ... is referenced by ... but is editor only` → runtime asset
  depends on editor-only content/module.
- Maps not cooked → add to *Project Settings → Packaging → List of maps to include*.
- A non-zero `ExitCode=` at the end of the UAT log is only a summary - search
  upwards for the first `Error:` line.

## 7. Log analysis procedure

1. Search the log for the first error marker: `rg -n "error|Error:|LNK|fatal" <log> | head -20`.
2. Read ~20 lines around it.
3. Identify the stage and the file/module.
4. Read the referenced source and Build.cs; verify the fix hypothesis.
5. Apply the smallest fix; rebuild; report the exact command and result.
Never claim a build succeeded without having run it and seen `Succeeded`/exit code 0.
