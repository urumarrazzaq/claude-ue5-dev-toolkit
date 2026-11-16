---
name: ue5-blueprints
description: Unreal Engine 5 Blueprint architecture and workflow - Blueprint/C++ split, Blueprint communication (direct references, casting, interfaces, Event Dispatchers), UMG widget integration, Blueprint debugging and Tick/performance optimisation. Use when designing Blueprint systems, exposing C++ to Blueprint, reviewing Blueprint-heavy features, or diagnosing Blueprint bugs and performance.
---

# UE5 Blueprint Development

Blueprints are binary `.uasset` files. Claude Code **cannot open or edit them
as text** - do not try to `Edit`, `Write`, `sed` or delete `.uasset`/`.umap`
files (this toolkit's settings deny `Edit` on them, and the safety hook asks
before shell deletion). Work through:

- C++ that Blueprints derive from or call (`BlueprintCallable`,
  `BlueprintImplementableEvent`, `BlueprintNativeEvent`, `BlueprintAssignable`).
- Step-by-step instructions for the user to perform in the editor.
- Text exports the user provides (copy-pasted nodes, `Asset Actions → Export`),
  or editor automation through Unreal's Python API if the project enables the
  *Python Editor Script Plugin*.

## 1. Architecture

- **C++ for systems, Blueprint for content.** Core rules, data structures,
  networking and anything hot goes in C++. Blueprints configure, compose and
  script per-asset behaviour.
- Create a C++ base class, then a Blueprint child (`BP_` prefix) that sets
  defaults, meshes and effects. Expose tunables with
  `UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category=...)`.
- Use Data Assets (`UPrimaryDataAsset`) or Data Tables for designer data
  instead of hard-coded Blueprint variables duplicated across children.
- Prefer Actor Components (`BlueprintSpawnableComponent`) for reusable
  behaviour over deep Blueprint inheritance.
- Naming: `BP_` Blueprint, `WBP_` widget, `ABP_` Anim Blueprint, `BPI_`
  interface, `E_`/`F_` user enums/structs, `DA_` data asset, `DT_` data table.

## 2. Communication between Blueprints

Choose the weakest coupling that works:

| Need | Mechanism | Notes |
|---|---|---|
| One known target, same system | Direct reference (exposed variable / `Get Owner`) | Cheapest; set reference once. |
| Ask "is this an X?" | Cast | Creates a hard reference to the target class (memory/size map). Avoid casting to heavy Blueprint classes; cast to a C++ base instead. |
| Many unrelated classes respond to the same message | Blueprint Interface (`BPI_Interactable`) | No hard reference; call with the *Message* node. Use `Does Implement Interface` when needed. |
| One broadcaster, unknown listeners | Event Dispatcher | Listeners bind/unbind; broadcaster has no knowledge of them. Unbind on `EndPlay`/widget destruct. |
| Global/game-wide event | Subsystem or GameState with a dispatcher | Avoid `Get All Actors Of Class` lookups. |

C++ equivalents: interfaces with `BlueprintNativeEvent` functions; dispatchers
are `DECLARE_DYNAMIC_MULTICAST_DELEGATE` + `UPROPERTY(BlueprintAssignable)`.

## 3. UMG integration

- Widget owns presentation only. Gameplay state lives in the
  PlayerController/PlayerState/Pawn; the widget reads it or listens to it.
- Create widgets on the **owning client**: `Create Widget` with the owning
  PlayerController, `Add to Viewport`, keep the reference to remove later.
- Avoid **property bindings** (the *Bind* dropdown) on values that rarely
  change - they evaluate every frame. Update via events/dispatchers instead
  (e.g. `OnHealthChanged` → `SetPercent`).
- C++ base widget: subclass `UUserWidget`, use `UPROPERTY(meta=(BindWidget))`
  for required child widgets (name must match), `BindWidgetOptional` otherwise.
  Requires the `UMG` module in Build.cs.
- Input mode: `Set Input Mode UI Only / Game And UI / Game Only` and
  `bShowMouseCursor` on the PlayerController; restore on close.
- Use `CommonUI` only if the project already uses it.

## 4. Debugging

- Select the instance in the Blueprint debug dropdown, set breakpoints (F9),
  use *Watch this value*, step with the Blueprint debugger.
- `Print String` with a key to avoid log spam; prefer `Print String` with
  `Print to Log` and check `Output Log` filters.
- Common runtime errors: *Accessed None* → reference not set or destroyed
  (validate with `Is Valid`); infinite loop detected → a loop or recursive event;
  casting failures → wrong class or not yet spawned.
- Compile all Blueprints after C++ changes (`File → Refresh All Nodes`, or
  run the editor commandlet `-run=CompileAllBlueprints` from the command line)
  to catch broken pins caused by renamed C++ functions/properties. Use
  `UFUNCTION(meta=(DeprecatedFunction))` or Core Redirects in
  `DefaultEngine.ini` when renaming exposed C++ members.

## 5. Performance and Tick

- Disable *Start with Tick Enabled* on Blueprints that don't need it; use
  `Set Actor Tick Enabled` to tick only while active, or set a Tick Interval.
- Replace Tick polling with timers (`Set Timer by Event`) or events.
- Avoid `Get All Actors Of Class`, heavy `For Each` loops and string
  operations per frame.
- Move hot Blueprint logic to C++ (profile first with Unreal Insights /
  `stat game` - the Blueprint VM cost shows under Blueprint time).
- Check the Size Map / Reference Viewer for cast-induced hard references that
  load large assets.

## 6. Output when helping with Blueprints

- Give node-by-node steps the user can follow in the editor, naming the exact
  node (e.g. *Bind Event to OnHealthChanged*).
- When providing C++ to support a Blueprint, state which Blueprint must be
  reparented or recompiled and which pins will change.
- State clearly that Blueprint changes were not verified by Claude.
