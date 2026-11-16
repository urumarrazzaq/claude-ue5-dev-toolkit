---
name: ue5-multiplayer
description: Unreal Engine 5 multiplayer and networking - server authority, actor and property replication, RPCs (Server/Client/NetMulticast, reliability), replication conditions, RepNotify, network relevancy and dormancy, ownership and multiplayer debugging. Use when implementing or reviewing networked gameplay, replicated state, RPCs, or diagnosing desync, "works in standalone but not in multiplayer" and client prediction issues.
---

# UE5 Multiplayer

Unreal uses an authoritative client-server model. The server owns the truth;
clients send *requests* and receive replicated *state*.

## 1. Authority and roles

- `HasAuthority()` → true on the server (and for actors spawned locally on a
  client that the server doesn't know about). Gate state changes with it.
- `GetLocalRole()` / `GetRemoteRole()`: `ROLE_Authority`,
  `ROLE_AutonomousProxy` (owning client's pawn), `ROLE_SimulatedProxy`.
- `IsLocallyControlled()` (pawns) → this machine's player controls it; use for
  input, camera and UI.
- `GetNetMode()`: `NM_Standalone`, `NM_DedicatedServer`, `NM_ListenServer`,
  `NM_Client`. Listen servers are both server *and* a local player - test both
  code paths.
- Where classes exist: GameMode server only; GameState/PlayerState everyone;
  PlayerController server + owner only; widgets owner only.

## 2. Actor replication

- Enable: `bReplicates = true` in the constructor (or `SetReplicates(true)` at
  runtime on the server). Movement: `SetReplicateMovement(true)`.
- Spawn replicated actors **on the server**; clients spawning them creates
  local-only copies.
- Components replicate with `SetIsReplicatedByDefault(true)` (constructor) and
  their own `GetLifetimeReplicatedProps`.

## 3. Replicated properties

```cpp
// Header
UPROPERTY(ReplicatedUsing = OnRep_Health)
float Health;

UFUNCTION()
void OnRep_Health(float OldHealth);

virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

// .cpp
#include "Net/UnrealNetwork.h"

void AMyCharacter::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME_CONDITION_NOTIFY(AMyCharacter, Health, COND_None, REPNOTIFY_OnChanged);
}
```

- Forgetting `Super::GetLifetimeReplicatedProps` or the `DOREPLIFETIME` entry
  is the most common "property doesn't replicate" bug.
- RepNotify (`OnRep_`) runs on **clients only** in C++. If the server needs
  the same reaction, call the handler explicitly after setting the value.
- Replication is state-based, not event-based: intermediate values can be
  skipped and only the latest value arrives. Use RPCs or counters for events.
- Prefer `FFastArraySerializer` for large arrays with frequent element changes.

## 4. Replication conditions

`DOREPLIFETIME_CONDITION(Class, Prop, Cond)` with common conditions:

| Condition | Sends to |
|---|---|
| `COND_None` | everyone (when changed) |
| `COND_OwnerOnly` | owning connection only (ammo, inventory) |
| `COND_SkipOwner` | everyone except owner (owner predicts locally) |
| `COND_SimulatedOnly` / `COND_AutonomousOnly` | by role |
| `COND_InitialOnly` | only in the initial bunch |
| `COND_Custom` | toggled with `DOREPLIFETIME_ACTIVE_OVERRIDE` in `PreReplication` |

## 5. RPCs

```cpp
UFUNCTION(Server, Reliable, WithValidation)
void ServerFire(FVector_NetQuantize AimLocation);
// .cpp: bool ServerFire_Validate(...) and void ServerFire_Implementation(...)

UFUNCTION(NetMulticast, Unreliable)
void MulticastPlayImpactFX(FVector_NetQuantize Location);

UFUNCTION(Client, Reliable)
void ClientShowHitMarker();
```

- Server RPCs only work from the **owning client** of the actor (its
  PlayerController, possessed pawn, or actors whose Owner chain leads to the
  connection). Calling from a non-owned actor is silently dropped - route the
  request through the PlayerController or pawn.
- `WithValidation` → `_Validate` returning false disconnects the client; use it
  for impossible values, not gameplay rules.
- Reliable = guaranteed and ordered, but saturating the reliable buffer
  disconnects players. Never send reliable RPCs every tick; use Unreliable for
  cosmetic or high-frequency data.
- Multicast must be called on the server; it reaches relevant clients only.
- Validate inputs on the server (rate, range, cooldown) - never trust the client.

## 6. Relevancy, priority and dormancy

- An actor replicates to a connection only when relevant: `bAlwaysRelevant`,
  `bOnlyRelevantToOwner`, `bNetUseOwnerRelevancy`, `NetCullDistanceSquared`,
  or overridden `IsNetRelevantFor`.
- `NetUpdateFrequency` / `MinNetUpdateFrequency` and `NetPriority` control
  bandwidth. Lower the frequency for rarely-changing actors.
- Dormancy (`NetDormancy = DORM_DormantAll`, `FlushNetDormancy()` after
  changes) stops replication checks for idle actors.
- Iris (the newer replication system) changes some of these mechanics in
  recent engine versions; check whether the project enables it before advising.

## 7. Prediction and movement

- `UCharacterMovementComponent` already predicts and corrects movement. Custom
  movement modes must go through its saved-move system; teleporting or
  changing speed on the client only causes corrections ("rubber-banding").
- For abilities, the project's Gameplay Ability System (if used) provides
  prediction keys - follow its patterns rather than hand-rolling.

## 8. Debugging multiplayer

- PIE: *Net Mode → Play As Listen Server / Play As Client*, number of players
  ≥ 2. Test dedicated-server mode explicitly.
- Network emulation: Editor Preferences → Level Editor → Play → *Enable Network
  Emulation* (latency/packet loss), or console `Net PktLag=100`, `Net PktLoss=5`.
- Log on which machine code runs: prefix logs with
  `GetNetMode()` / `HasAuthority()`; in PIE, logs are prefixed with
  `Server:`/`Client N:`.
- Console: `stat net` for bandwidth/packet overview. Unreal Insights
  *Networking Insights* (launch with `-NetTrace=1 -trace=net`) shows
  per-actor and per-property bandwidth.
- Checklist for "works offline, not online": spawned on server? `bReplicates`?
  property registered in `GetLifetimeReplicatedProps`? RPC called from an owned
  actor? Multicast called on server? Actor relevant to that client? Code gated
  by `HasAuthority()` correctly?
