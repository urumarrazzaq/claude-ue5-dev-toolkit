---
name: ue5-ai-systems
description: Unreal Engine 5 AI - AIController, Behavior Trees (tasks, decorators, services), Blackboard, EQS, AI Perception, navigation/NavMesh, combat behaviour and debugging AI decisions. Use when building or fixing NPC/enemy AI, writing BTTask/BTService/BTDecorator C++ classes, configuring perception or navigation, or diagnosing why an AI does not move or choose the expected action.
---

# UE5 AI Systems

Required modules in Build.cs for C++ AI work: `AIModule`, `GameplayTasks`,
and `NavigationSystem` when using navigation queries. Verify they are not
already present before adding them.

## 1. AIController

- The `AAIController` possesses a pawn on the **server only**; AI logic never
  runs on clients. Set the pawn's `AIControllerClass` and
  `AutoPossessAI = EAutoPossessAI::PlacedInWorldOrSpawned` for spawned pawns.
- Start logic in `OnPossess(APawn*)`: `RunBehaviorTree(BTAsset)` (also
  initialises the blackboard from the tree's blackboard asset), or
  `UseBlackboard` first if you need to seed values.
- Keep pawn-independent decision state in the controller/blackboard; keep
  physical capabilities (weapons, movement speed) on the pawn/components.
- Team affiliation: implement `IGenericTeamAgentInterface` (`GetGenericTeamId`)
  on the controller so perception can classify hostile/neutral/friendly.

## 2. Behavior Trees and Blackboard

- Blackboard keys are the tree's shared memory. Name keys clearly
  (`TargetActor`, `MoveToLocation`, `bHasLineOfSight`) and reference them in C++
  through `FBlackboardKeySelector` properties, not hard-coded strings.
  Filter selectors in the constructor (`AddObjectFilter`, `AddVectorFilter`).
- Node types:
  - **Composite**: Selector (first success wins), Sequence (all must succeed),
    Simple Parallel.
  - **Task** (`UBTTaskNode`): override `ExecuteTask`; return `Succeeded`,
    `Failed` or `InProgress`. Latent tasks call `FinishLatentTask(OwnerComp, Result)`
    later and should handle `AbortTask`.
  - **Decorator** (`UBTDecorator`): conditions; configure *Observer Aborts*
    (`Self`, `Lower Priority`, `Both`) so the tree reacts to blackboard changes.
  - **Service** (`UBTService`): periodic updates (`TickNode` with `Interval`
    and `RandomDeviation`) - use them for target selection, not tasks.
- Per-instance task state: either set `bCreateNodeInstance = true` or use node
  memory (`GetInstanceMemorySize`); never store per-AI state in a shared
  non-instanced node's member variables.
- Prefer event-driven trees: let perception updates write the blackboard and
  decorators abort, rather than services polling every frame.
- StateTree is an alternative in UE 5.1+; use it only if the project already does.

## 3. EQS (Environment Query System)

- Queries = Generator (points on grid/circle, actors of class, pathing grid) +
  Tests (distance, trace/visibility, dot, pathfinding, overlap) with scoring/filtering.
- Run from a BT with the *Run EQS Query* task writing to a blackboard key, or
  from C++ with `FEnvQueryRequest(Query, Querier).Execute(...)`.
- Keep generators small (point counts grow quickly); filter cheap tests first,
  expensive traces/pathfinding last.
- Contexts (`UEnvQueryContext`) supply the querier/target/etc.

## 4. AI Perception

- Add `UAIPerceptionComponent` to the controller and configure senses
  (`UAISenseConfig_Sight`, `_Hearing`, `_Damage`). Set sight radius, lose-sight
  radius, peripheral angle and *Detection by Affiliation*.
- Stimuli sources: pawns are auto-registered for sight by default
  (`bAutoRegisterAllPawnsAsSources` in the AI system config); otherwise add
  `UAIPerceptionStimuliSourceComponent`.
- Bind `OnTargetPerceptionUpdated(AActor*, FAIStimulus)`; check
  `Stimulus.WasSuccessfullySensed()` and update the blackboard.
- Hearing requires reporting: `UAISense_Hearing::ReportNoiseEvent(...)`.
  Damage: `UAISense_Damage::ReportDamageEvent(...)`.

## 5. Navigation

- Needs a `NavMeshBoundsVolume` in the level; press **P** in the editor viewport
  to visualise the NavMesh. Runtime generation (`Dynamic`) is configured in
  Project Settings → Navigation Mesh.
- Move with `MoveToActor` / `MoveToLocation` (returns `EPathFollowingRequestResult`)
  or the BT *Move To* task; check acceptance radius and that the target is
  projected onto the NavMesh (`UNavigationSystemV1::ProjectPointToNavigation`).
- Nav Modifiers and Nav Areas control costs; Nav Links connect gaps/jumps.
- Agent radius/height in Project Settings must match the pawn capsule or the
  agent will not use the generated mesh.

## 6. Combat behaviour patterns

- Structure: Selector → [Dead/Stunned branch] → [Combat (decorator: has target)]
  → [Investigate (last known location)] → [Patrol/Idle].
- Combat sub-tree: choose position (EQS: cover/visible/at range) → move →
  face target → attack (task calls a pawn/ability function, waits for montage
  or ability end) → cooldown.
- Coordinate groups with a token/slot system (subsystem granting attack tokens)
  to avoid every enemy attacking at once.
- Keep damage and attack resolution in gameplay code (or GAS if the project
  uses it); the BT only decides *when*.

## 7. Debugging AI decisions

- **Gameplay Debugger**: press the apostrophe key (`'`) in PIE, numpad to
  toggle categories (AI, Behavior Tree, EQS, Perception, NavMesh).
- Open the Behavior Tree asset during PIE and pick the AI instance to watch
  active nodes and blackboard values live.
- **Visual Logger** (`Tools → Debug → Visual Logger`, or console `vislog`):
  records BT, perception and path history; add custom entries with
  `UE_VLOG` / `UE_VLOG_LOCATION`.
- Console: `show Navigation` toggles NavMesh drawing in a running game.
- Checklist when an AI "does nothing": pawn possessed? (`AutoPossessAI`) → tree
  running? → blackboard key set? → decorator aborting? → NavMesh present and
  agent size matching? → movement component able to move (MaxWalkSpeed > 0)?
