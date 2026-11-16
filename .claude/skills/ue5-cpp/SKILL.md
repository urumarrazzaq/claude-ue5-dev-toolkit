---
name: ue5-cpp
description: Unreal Engine 5 C++ gameplay programming - UObject lifecycle and garbage collection, reflection macros (UCLASS/UPROPERTY/UFUNCTION), actors and components, the gameplay framework, interfaces, delegates, module dependencies, performance and Epic coding conventions. Use when writing, modifying or explaining UE5 C++ code (.h/.cpp files, Build.cs modules, AActor/UObject subclasses).
---

# UE5 C++ Development

Use this skill whenever you write or change Unreal Engine C++. The goal is code
that compiles on the first try, survives garbage collection, matches the
project's existing patterns and does not add avoidable per-frame cost.

## 0. Before writing code

1. **Find the engine version.** Read `EngineAssociation` in the `.uproject`
   (`python scripts/validate_project.py <project>` prints it). APIs differ
   between 5.0 and 5.5 (e.g. Enhanced Input is the default from 5.1, `TObjectPtr`
   is preferred for member pointers from 5.0).
2. **Find the pattern already used.** Search before inventing:
   `rg "class \w+_API A\w+Character" Source/`, `rg "UCLASS\(" Source/<Module>`.
   Reuse the project's base classes, subsystems, interfaces and naming.
3. **Verify every engine API you call.** If the engine source is available,
   `rg "FunctionName" "<EngineDir>/Engine/Source/Runtime"`. If it is not, say the
   call is unverified instead of guessing. Never invent functions, macros,
   specifiers or modules.
4. **Locate the owning module** (`Source/<Module>/<Module>.Build.cs`) - new
   includes may require a new module dependency.

## 1. UObject lifecycle and garbage collection

- `UObject`s are created with `NewObject<T>(Outer)`; actors with
  `GetWorld()->SpawnActor<T>(...)`; default subobjects only in constructors with
  `CreateDefaultSubobject<T>(TEXT("Name"))`. Never `new`/`delete` a UObject.
- GC only sees references it can reach through reflection. A raw `UObject*`
  member **without** `UPROPERTY()` is invisible to GC and becomes dangling.
  - Strong member reference: `UPROPERTY() TObjectPtr<UFoo> Foo;`
  - Non-owning reference that must not keep the object alive: `TWeakObjectPtr<UFoo>`.
  - Containers of UObjects must also be `UPROPERTY()`: `UPROPERTY() TArray<TObjectPtr<UFoo>> Items;`
  - Non-UObject classes holding UObjects: derive from `FGCObject` and implement
    `AddReferencedObjects` and `GetReferencerName`.
  - Soft references to assets: `TSoftObjectPtr<T>` / `TSoftClassPtr<T>` and load
    asynchronously through `UAssetManager::GetStreamableManager()`.
- Validity: use `IsValid(Obj)` (null *and* pending-kill/garbage check). After a
  weak pointer, call `.Get()` once and test the result.
- Actor lifecycle order to respect: constructor (CDO, no world) →
  `PostInitializeComponents` → `BeginPlay` → `Tick` → `EndPlay(EEndPlayReason)`
  → destruction. Do not access the world, other actors or game mode in the
  constructor. Clear timers and unbind delegates in `EndPlay`.
- `Destroy()` on an actor marks it for destruction; references still exist
  until GC runs, so null-check or use weak pointers.

## 2. Reflection macros

- Every reflected type needs `#include "FileName.generated.h"` as the **last**
  include in the header and `GENERATED_BODY()` in the class/struct body.
- Class prefixes are mandatory: `A` actors, `U` UObjects/components, `F`
  structs/plain classes, `E` enums, `I` interfaces, `T` templates.
- Common specifiers (verify the combination is legal):
  - `UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Combat")`
  - `UPROPERTY(VisibleAnywhere, BlueprintReadOnly)` for component pointers.
  - `UPROPERTY(Replicated)` / `UPROPERTY(ReplicatedUsing=OnRep_Health)`.
  - `UFUNCTION(BlueprintCallable, Category="...")`, `BlueprintPure`,
    `BlueprintImplementableEvent`, `BlueprintNativeEvent` (implement `Foo_Implementation`).
  - `UFUNCTION(Server, Reliable)` etc. - see the `ue5-multiplayer` skill.
- `meta=(ClampMin="0")`, `meta=(AllowPrivateAccess="true")` for private
  Blueprint-visible members.
- `USTRUCT(BlueprintType)` + `GENERATED_BODY()`; `UENUM(BlueprintType)` with
  `enum class EFoo : uint8`.
- Editing a `.generated.h`-producing header changes reflection data; Live
  Coding cannot apply reflection changes reliably - recommend an editor restart.

## 3. Actor and component architecture

- Prefer composition: put reusable behaviour in `UActorComponent` (logic) or
  `USceneComponent` (needs a transform). Keep actors thin.
- Create components in the constructor; attach with `SetupAttachment` there.
  At runtime use `NewObject` + `RegisterComponent()` + `AttachToComponent`.
- Find sibling components with `FindComponentByClass<T>()` once (e.g. in
  `BeginPlay`) and cache the result in a `UPROPERTY()`.
- Tick is opt-in: set `PrimaryActorTick.bCanEverTick = false` (or
  `PrimaryComponentTick.bCanEverTick = false`) unless the class really needs it.

## 4. Gameplay framework

| Class | Lives on | Responsibility |
|---|---|---|
| `AGameModeBase` / `AGameMode` | server only | rules, spawning, match flow |
| `AGameStateBase` | server + all clients | replicated match state |
| `APlayerController` | server + owning client | input, UI ownership, player intent |
| `APlayerState` | server + all clients | replicated per-player data |
| `APawn` / `ACharacter` | server + relevant clients | physical representation |
| `AHUD` / UMG widgets | owning client only | presentation |
| `UGameInstance` / `UGameInstanceSubsystem` | per process, persists across maps | sessions, save, services |
| `UWorldSubsystem` | per world | world-scoped managers |

Put code where it lives: never read `GetGameMode()` on a client (it is null).
Prefer subsystems over singletons or static globals.

## 5. Interfaces and delegates

- Interfaces: `UINTERFACE(MinimalAPI, Blueprintable) class UInteractable : public UInterface`
  plus `class IInteractable`. For Blueprint-implementable functions call through
  `IInteractable::Execute_Interact(Target, Instigator)` after
  `Target->Implements<UInteractable>()`. A plain `Cast<IInteractable>` misses
  Blueprint-only implementations.
- Delegates:
  - `DECLARE_DELEGATE_*` single-bound C++ only.
  - `DECLARE_MULTICAST_DELEGATE_*` many C++ listeners.
  - `DECLARE_DYNAMIC_MULTICAST_DELEGATE_*Param(s)` for `UPROPERTY(BlueprintAssignable)`
    event dispatchers; bound functions must be `UFUNCTION()`.
- Bind with `AddUObject` / `AddDynamic` and remove in `EndPlay`/`Deinitialize`.
  Lambdas capturing `this` must use `AddWeakLambda(this, ...)` or a weak pointer.

## 6. Module dependencies

- Including a header from another module requires that module in
  `PublicDependencyModuleNames` (if the type appears in your public headers) or
  `PrivateDependencyModuleNames` (only used in .cpp). Common ones:
  `Core`, `CoreUObject`, `Engine`, `InputCore`, `EnhancedInput`, `UMG`,
  `Slate`, `SlateCore`, `AIModule`, `GameplayTasks`, `NavigationSystem`,
  `NetCore`, `GameplayTags`.
- Exported classes need the module API macro: `class MYGAME_API AMyActor`.
- Editor-only code goes in a separate `Editor` module or behind
  `#if WITH_EDITOR`; never make a runtime module depend on `UnrealEd`.
- Before adding a plugin dependency, check whether the project already has an
  equivalent and whether the plugin is enabled in the `.uproject`.

## 7. Performance

- Avoid Tick; prefer events, delegates, `FTimerHandle` timers
  (`GetWorldTimerManager().SetTimer`) or a lower `PrimaryActorTick.TickInterval`.
- No `GetAllActorsOfClass`, `FindComponentByClass`, string lookups or asset
  loads in hot paths - cache or use a registry/subsystem.
- Reserve arrays (`Reserve`), pass large types by `const&`, use `MoveTemp`.
- Use `TRACE_CPUPROFILER_EVENT_SCOPE(Name)` / `SCOPE_CYCLE_COUNTER` to measure
  before optimising; inspect with Unreal Insights or `stat game`.
- Hard references (`TSubclassOf`, raw asset pointers in CDOs) pull assets into
  memory; prefer soft references for large optional assets.

## 8. Coding conventions (Epic standard unless the project differs)

- PascalCase for types, functions and variables; `b` prefix for booleans.
- `TEXT("...")` for string literals; `FName` for identifiers, `FText` for
  user-facing text, `FString` for manipulation.
- `check()` for programmer errors, `ensure()` for recoverable ones, `UE_LOG`
  with a declared log category (`DECLARE_LOG_CATEGORY_EXTERN`) for diagnostics.
- Forward-declare in headers; include what you use in .cpp. Keep the
  `.generated.h` include last.
- `nullptr`, `override`, `const` correctness, `TArray`/`TMap`/`TSet` over STL.

## 9. Deliverable checklist

- [ ] Headers compile in isolation (generated include last, forward decls OK).
- [ ] Every UObject pointer member is `UPROPERTY()` or weak.
- [ ] Tick disabled unless justified.
- [ ] New module dependencies added to `Build.cs` and explained.
- [ ] Server/client implications noted for gameplay state.
- [ ] Report modified files and how it was validated (build command and result,
      or explicitly "not compiled").
