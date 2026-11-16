#!/usr/bin/env python3
"""End-to-end demonstration of the orchestration workflow.

Creates a throwaway git repository with a tiny synthetic Unreal project, then
drives ``scripts/orchestrator.py`` through the full lifecycle of the
"multiplayer combat ability" example: conflict detection, protection of the
user's uncommitted work, parallel batches, an ownership audit, a review that
requests changes, re-review, and the final report.

SUBAGENT OUTPUT IS SIMULATED: this script writes the files and handoffs an
agent would produce so the coordinator-side mechanics can be shown and
tested without Claude Code or Unreal Engine. In a real session the
coordinator sends each brief to a subagent with the Agent tool.

Usage:
    python examples/orchestration/demo_session.py [--keep]

Nothing outside the temporary directory is modified. Exit code 0 = the
workflow behaved as expected.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "scripts" / "orchestrator.py"
TASKS = Path(__file__).resolve().parent / "combat-ability.tasks.json"
GIT_ID = ["-c", "user.name=demo", "-c", "user.email=demo@example.invalid"]


class Demo:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.plan = project / ".claude" / "orchestration" / "plan.json"

    def step(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def orch(self, *args: str, expect: int = 0) -> str:
        cmd = [sys.executable, str(ORCH), "--plan", str(self.plan),
               "--agents-dir", str(ROOT / ".claude" / "agents"), *args]
        shown = " ".join(a if " " not in a else f'"{a}"' for a in args)
        print(f"$ orchestrator {shown}")
        proc = subprocess.run(cmd, cwd=self.project, capture_output=True, text=True)
        out = (proc.stdout + proc.stderr).rstrip()
        if out:
            print("  " + out.replace("\n", "\n  "))
        if proc.returncode != expect:
            raise SystemExit(f"DEMO FAILED: expected exit {expect}, got {proc.returncode}")
        return out

    def write(self, rel: str, text: str) -> None:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def agent(self, task_id: str, note: str) -> None:
        print(f"[simulated subagent for {task_id}] {note}")

    def handoff(self, task_id: str, data: dict) -> None:
        path = self.project / ".claude" / "orchestration" / "handoffs" / f"{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.orch("handoff", task_id, "--json-file", str(path))


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_ID, "-C", str(project), *args], check=True,
                   capture_output=True)


def make_project(project: Path) -> None:
    files = {
        "ShooterGame.uproject": json.dumps({"FileVersion": 3, "EngineAssociation": "5.4",
                                            "Modules": [{"Name": "ShooterGame", "Type": "Runtime"}]}),
        ".gitignore": ".claude/orchestration/\nBinaries/\nIntermediate/\nSaved/\n",
        "Source/ShooterGame/ShooterGame.Build.cs": "// PublicDependencyModuleNames: Core, GameplayAbilities\n",
        "Source/ShooterGame/Public/Abilities/ShooterGameplayAbility.h": "// base ability\n",
        "Source/ShooterGame/Public/Character/ShooterCharacter.h": "// character\n",
        "Source/ShooterGame/Private/Character/ShooterCharacter.cpp": "// character impl\n",
    }
    for rel, text in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "initial")
    # The user has uncommitted work in a file the feature will need.
    (project / "Source/ShooterGame/Private/Character/ShooterCharacter.cpp").write_text(
        "// character impl\n// user's work in progress\n", encoding="utf-8")


def run(project: Path) -> None:
    d = Demo(project)
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))

    d.step("1. Coordinator receives the task and creates the plan")
    d.orch("init", "--title", "Dash Strike ability",
           "--request", "Implement a new multiplayer combat ability in Unreal Engine.")

    d.step("2. Safety baseline: record the user's uncommitted changes")
    d.orch("preflight", "--project-dir", ".")

    d.step("3. Architect's first draft is rejected: T2 and T3 both own ShooterCharacter.cpp")
    draft = json.loads(json.dumps(tasks))
    draft["tasks"][1]["files"].append("Source/ShooterGame/Private/Character/ShooterCharacter.cpp")
    draft_path = project / ".claude" / "orchestration" / "draft.tasks.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    d.orch("import-tasks", str(draft_path), expect=1)

    d.step("4. Corrected plan imported and validated")
    d.orch("import-tasks", str(TASKS))
    d.orch("validate")
    d.orch("preflight", "--project-dir", ".")

    d.step("5. Batch 1: independent tasks T1 and T2 (disjoint files)")
    d.orch("ready")
    d.orch("start", "T1", "T2")
    d.orch("brief", "T1")
    d.agent("T1", "writes GA_DashStrike.h/.cpp")
    d.write("Source/ShooterGame/Public/Abilities/GA_DashStrike.h", "// UGA_DashStrike\n")
    d.write("Source/ShooterGame/Private/Abilities/GA_DashStrike.cpp", "// dash + server sweep\n")
    d.agent("T2", "writes DashStrikeDamageExecution.h/.cpp and (wrongly) touches Build.cs")
    d.write("Source/ShooterGame/Public/Abilities/DashStrikeDamageExecution.h", "// exec calc\n")
    d.write("Source/ShooterGame/Private/Abilities/DashStrikeDamageExecution.cpp", "// formula\n")
    build_cs = project / "Source/ShooterGame/ShooterGame.Build.cs"
    original = build_cs.read_text(encoding="utf-8")
    build_cs.write_text(original + "// GameplayTags\n", encoding="utf-8")

    d.step("6. Ownership audit catches the unowned Build.cs edit")
    d.orch("audit", "--project-dir", ".", expect=1)
    print("[coordinator] shows the diff to the user; user says: undo the Build.cs edit, "
          "GameplayTags is already a dependency")
    build_cs.write_text(original, encoding="utf-8")
    d.orch("audit", "--project-dir", ".")

    d.handoff("T1", {"summary": "UGA_DashStrike: LocalPredicted dash via root-motion task; "
                     "server-only sweep applies DamageEffectClass once per actor.",
                     "files_changed": tasks["tasks"][0]["files"],
                     "validation_result": "Not compiled (build is scheduled as T6)",
                     "issues": ["UAbilityTask_ApplyRootMotionConstantForce signature unverified for 5.4"]})
    d.handoff("T2", {"summary": "Execution captures AttackPower (source) and Armor (target).",
                     "files_changed": tasks["tasks"][1]["files"],
                     "validation_result": "Not compiled"})
    d.orch("set-status", "T1", "review")
    d.orch("set-status", "T2", "review")
    d.orch("set-status", "T1", "completed", expect=1)  # no review yet

    d.step("7. Batch 2: T3 needs approval for the user's dirty file; T4 runs meanwhile")
    d.orch("ready")
    print("[coordinator] asks the user; user agrees T3 may edit ShooterCharacter.cpp "
          "on top of their uncommitted change")
    d.orch("allow-dirty", "T3", "--note", "user approved in chat")
    d.orch("ready")
    d.orch("start", "T3", "T4")
    d.agent("T3", "grants the ability in PossessedBy")
    d.write("Source/ShooterGame/Private/Character/ShooterCharacter.cpp",
            "// character impl\n// user's work in progress\n// GiveAbility(GA_DashStrike) on server\n")
    d.write("Source/ShooterGame/Public/Character/ShooterCharacter.h", "// character + DashStrike\n")
    d.handoff("T3", {"summary": "Granted on server in PossessedBy; guarded against re-grant.",
                     "files_changed": tasks["tasks"][2]["files"], "validation_result": "Not compiled"})
    d.orch("set-status", "T3", "review")
    d.handoff("T4", {"summary": "Editor steps for GE_DashStrike_Damage, GE_DashStrike_Cooldown, "
                     "IA_DashStrike and the montage notify.",
                     "followups": ["Create GE_DashStrike_Damage (Executions: UDashStrikeDamageExecution)",
                                   "Add InputTag.Ability.Secondary to IMC_Default"]})
    d.orch("set-status", "T4", "completed")

    d.step("8. Batch 3: review and build validation (no writer active)")
    d.orch("ready")
    d.orch("start", "T5", "T6")
    d.handoff("T5", {"summary": "Damage applied on server only; one blocking issue.",
                     "verdict": "changes_requested",
                     "issues": ["[MAJOR] Source/ShooterGame/Private/Abilities/GA_DashStrike.cpp:42 - "
                                "hit actors not de-duplicated across sweep results"]})
    d.orch("set-status", "T5", "completed")
    d.handoff("T6", {"summary": "No engine installation in the demo environment.",
                     "validation_result": "not run: build command unknown"})
    d.orch("set-status", "T6", "completed")

    d.step("9. Rework T1, then re-review with a new reviewer task")
    d.orch("set-status", "T1", "in_progress", "--note", "T5: de-duplicate hit actors")
    d.agent("T1", "adds a TSet of already-hit actors")
    d.write("Source/ShooterGame/Private/Abilities/GA_DashStrike.cpp", "// dash + server sweep + TSet dedupe\n")
    d.handoff("T1", {"summary": "Hit actors de-duplicated with a TSet.",
                     "files_changed": [tasks["tasks"][0]["files"][1]], "validation_result": "Not compiled"})
    d.orch("set-status", "T1", "review")
    d.orch("add", "--id", "T5b", "--agent", "ue5-code-reviewer", "--priority", "high",
           "--depends-on", "T1", "T2", "T3", "--description", "Re-review after T5 findings",
           "--expected-output", "Verdict on the rework", "--validation", "T5 MAJOR finding resolved")
    d.orch("start", "T5b")
    d.handoff("T5b", {"summary": "Finding resolved; no further blocking issues.", "verdict": "approve"})
    d.orch("set-status", "T5b", "completed")
    for tid in ("T1", "T2", "T3"):
        d.orch("set-status", tid, "completed")

    d.step("10. Final audit, status and report")
    d.orch("audit", "--project-dir", ".")
    d.orch("status")
    d.orch("report")


def main(argv: list) -> int:
    if shutil.which("git") is None:
        print("git is required for the demo")
        return 2
    keep = "--keep" in argv
    tmp = Path(tempfile.mkdtemp(prefix="ue5-orchestrator-demo-"))
    try:
        project = tmp / "ShooterGame"
        project.mkdir()
        make_project(project)
        run(project)
        print(f"\nDemo completed successfully.{' Files kept in ' + str(project) if keep else ''}")
        return 0
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
