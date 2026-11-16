#!/usr/bin/env python3
"""Read-only structural validator for Unreal Engine projects.

Usage:
    python scripts/validate_project.py [PROJECT_DIR_OR_UPROJECT] [--json]

The script inspects an Unreal Engine project and prints a validation report:

* locates the ``.uproject`` file and reads its metadata
  (engine association, modules, plugins, target platforms);
* identifies the Source, Content, Config and Plugins directories;
* discovers C++ modules (``*.Build.cs``) and targets (``*.Target.cs``);
* lists plugins enabled/disabled in the ``.uproject`` and project plugins
  found under ``Plugins/``;
* reports common structural problems (declared modules without source,
  missing targets, missing default config, VCS hygiene, invalid JSON...).

The script never writes to, moves or deletes anything in the project.
Only the Python standard library (3.8+) is used.

Exit codes: 0 = no errors, 1 = errors found, 2 = no project / bad arguments.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ERROR, WARNING, INFO = "error", "warning", "info"

# Directories UBT/the editor recreate; they should not be committed.
GENERATED_DIRS = ("Binaries", "Intermediate", "Saved", "DerivedDataCache")
EXPECTED_CONFIG_FILES = ("DefaultEngine.ini", "DefaultGame.ini")
VALID_MODULE_TYPES = {
    "Runtime", "RuntimeNoCommandlet", "RuntimeAndProgram", "CookedOnly",
    "UncookedOnly", "Developer", "DeveloperTool", "Editor", "EditorNoCommandlet",
    "EditorAndProgram", "Program", "ServerOnly", "ClientOnly", "ClientOnlyNoCommandlet",
}
# Hard cap so huge Content folders do not make the script slow.
MAX_ASSET_SCAN = 200_000


@dataclass
class Issue:
    severity: str
    message: str


@dataclass
class PluginInfo:
    name: str
    enabled: Optional[bool]
    source: str  # "project", "engine/marketplace (not verified)"
    descriptor: Optional[str] = None


@dataclass
class Report:
    project_dir: str
    uproject: Optional[str] = None
    project_name: Optional[str] = None
    engine_association: Optional[str] = None
    file_version: Optional[int] = None
    description: Optional[str] = None
    target_platforms: List[str] = field(default_factory=list)
    directories: Dict[str, bool] = field(default_factory=dict)
    declared_modules: List[Dict[str, str]] = field(default_factory=list)
    source_modules: List[str] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    plugins: List[PluginInfo] = field(default_factory=list)
    project_plugins: List[str] = field(default_factory=list)
    asset_counts: Dict[str, int] = field(default_factory=dict)
    config_files: List[str] = field(default_factory=list)
    game_default_map: Optional[str] = None
    issues: List[Issue] = field(default_factory=list)

    def add(self, severity: str, message: str) -> None:
        self.issues.append(Issue(severity, message))

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == ERROR)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    """Load a UE descriptor. UE files may start with a UTF-8 BOM."""
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value is not an object")
    return data


def find_uproject(target: Path) -> List[Path]:
    if target.is_file() and target.suffix.lower() == ".uproject":
        return [target]
    if not target.is_dir():
        return []
    return sorted(p for p in target.iterdir() if p.is_file() and p.suffix.lower() == ".uproject")


def is_engine_version(association: str) -> bool:
    """'5.4' style association (launcher engine) vs a GUID/path (source build)."""
    parts = association.split(".")
    return len(parts) in (2, 3) and all(p.isdigit() for p in parts)


def read_ini_value(path: Path, section: str, key: str) -> Optional[str]:
    """Read a single key from a UE ini file, tolerating UE-specific syntax."""
    parser = configparser.RawConfigParser(strict=False, allow_no_value=True,
                                          interpolation=None)
    parser.optionxform = str  # keep key case
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            parser.read_file(handle)
    except (configparser.Error, OSError):
        return None
    if parser.has_option(section, key):
        return parser.get(section, key)
    return None


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def inspect_uproject(report: Report, uproject: Path) -> dict:
    report.uproject = str(uproject)
    report.project_name = uproject.stem
    try:
        data = load_json(uproject)
    except (ValueError, OSError) as exc:
        report.add(ERROR, f"{uproject.name} is not valid JSON: {exc}")
        return {}

    report.file_version = data.get("FileVersion")
    report.description = data.get("Description") or None
    report.target_platforms = list(data.get("TargetPlatforms") or [])
    association = data.get("EngineAssociation")
    report.engine_association = association
    if not association:
        report.add(WARNING, "EngineAssociation is empty: the project is not bound to a "
                            "specific engine version (common for engine-tree projects).")
    elif not is_engine_version(association):
        report.add(INFO, f"EngineAssociation '{association}' is not a version number; "
                         "the project targets a source-built engine.")
    if report.file_version not in (None, 3):
        report.add(WARNING, f"Unexpected .uproject FileVersion {report.file_version} (UE5 uses 3).")

    for module in data.get("Modules") or []:
        if not isinstance(module, dict) or "Name" not in module:
            report.add(ERROR, "A Modules entry in the .uproject has no Name.")
            continue
        entry = {"Name": module["Name"], "Type": module.get("Type", ""),
                 "LoadingPhase": module.get("LoadingPhase", "Default")}
        report.declared_modules.append(entry)
        if entry["Type"] and entry["Type"] not in VALID_MODULE_TYPES:
            report.add(WARNING, f"Module '{entry['Name']}' has unrecognised Type '{entry['Type']}'.")

    for plugin in data.get("Plugins") or []:
        if isinstance(plugin, dict) and plugin.get("Name"):
            report.plugins.append(PluginInfo(plugin["Name"], plugin.get("Enabled"),
                                             "engine/marketplace (not verified)"))
    return data


def inspect_directories(report: Report, root: Path) -> None:
    for name in ("Source", "Content", "Config", "Plugins", *GENERATED_DIRS):
        report.directories[name] = (root / name).is_dir()
    if not report.directories["Content"]:
        report.add(WARNING, "No Content/ directory: the project has no assets yet.")
    if not report.directories["Config"]:
        report.add(ERROR, "No Config/ directory: project settings are missing.")


def inspect_source(report: Report, root: Path) -> None:
    source = root / "Source"
    if not source.is_dir():
        if report.declared_modules:
            report.add(ERROR, "The .uproject declares C++ modules but there is no Source/ directory.")
        return

    report.targets = sorted(p.name for p in source.glob("*.Target.cs"))
    build_files = {p.parent.name: p for p in source.glob("*/*.Build.cs")
                   if p.name[:-len(".Build.cs")] == p.parent.name}
    # Modules may also be nested one level (Source/Group/Module/Module.Build.cs).
    for p in source.glob("*/*/*.Build.cs"):
        if p.name[:-len(".Build.cs")] == p.parent.name:
            build_files.setdefault(p.parent.name, p)
    report.source_modules = sorted(build_files)

    declared = {m["Name"] for m in report.declared_modules}
    for name in sorted(declared - set(build_files)):
        report.add(ERROR, f"Module '{name}' is declared in the .uproject but "
                          f"Source/{name}/{name}.Build.cs was not found.")
    for name in sorted(set(build_files) - declared):
        report.add(WARNING, f"Source module '{name}' has a Build.cs but is not listed in the "
                            ".uproject Modules array (it will only build if a target or "
                            "another module references it).")
    if build_files and not report.targets:
        report.add(ERROR, "C++ modules exist but no *.Target.cs files were found in Source/.")
    if report.targets:
        if not any(t.endswith("Editor.Target.cs") for t in report.targets):
            report.add(WARNING, "No *Editor.Target.cs found: the editor cannot build this project's code.")
        if not any(not t.endswith("Editor.Target.cs") and not t.endswith("Server.Target.cs")
                   and not t.endswith("Client.Target.cs") for t in report.targets):
            report.add(WARNING, "No game target (*.Target.cs) found: the project cannot be packaged.")
    for name, path in build_files.items():
        if not ((path.parent / "Public").is_dir() or (path.parent / "Private").is_dir()
                or any(path.parent.glob("*.cpp"))):
            report.add(WARNING, f"Module '{name}' has no Public/, Private/ or .cpp files.")


def inspect_plugins(report: Report, root: Path) -> None:
    plugins_dir = root / "Plugins"
    local: Dict[str, Path] = {}
    if plugins_dir.is_dir():
        for descriptor in plugins_dir.rglob("*.uplugin"):
            local[descriptor.stem] = descriptor
            try:
                data = load_json(descriptor)
            except (ValueError, OSError) as exc:
                report.add(ERROR, f"Plugin descriptor {descriptor.relative_to(root)} is invalid JSON: {exc}")
                continue
            for module in data.get("Modules") or []:
                name = module.get("Name") if isinstance(module, dict) else None
                if not name:
                    continue
                build_cs = descriptor.parent / "Source" / name / f"{name}.Build.cs"
                if not build_cs.is_file() and not list((descriptor.parent / "Source").glob(f"**/{name}.Build.cs")):
                    report.add(ERROR, f"Plugin '{descriptor.stem}' declares module '{name}' but "
                                      f"{name}.Build.cs was not found under its Source/ folder.")
    report.project_plugins = sorted(local)

    listed = {p.name for p in report.plugins}
    for plugin in report.plugins:
        if plugin.name in local:
            plugin.source = "project"
            plugin.descriptor = str(local[plugin.name].relative_to(root))
    for name in sorted(set(local) - listed):
        report.plugins.append(PluginInfo(name, None, "project",
                                         str(local[name].relative_to(root))))
        report.add(INFO, f"Project plugin '{name}' is not listed in the .uproject; it uses its "
                         "EnabledByDefault setting.")


def inspect_content(report: Report, root: Path) -> None:
    content = root / "Content"
    if not content.is_dir():
        return
    counts = {"uasset": 0, "umap": 0}
    scanned = 0
    for _dirpath, _dirs, files in os.walk(content):
        for filename in files:
            lower = filename.lower()
            if lower.endswith(".uasset"):
                counts["uasset"] += 1
            elif lower.endswith(".umap"):
                counts["umap"] += 1
            scanned += 1
        if scanned >= MAX_ASSET_SCAN:
            report.add(INFO, f"Content scan stopped after {MAX_ASSET_SCAN} files; counts are partial.")
            break
    report.asset_counts = counts
    if counts["umap"] == 0:
        report.add(WARNING, "No .umap levels found in Content/.")


def inspect_config(report: Report, root: Path) -> None:
    config = root / "Config"
    if not config.is_dir():
        return
    report.config_files = sorted(p.name for p in config.glob("*.ini"))
    for expected in EXPECTED_CONFIG_FILES:
        if expected not in report.config_files:
            report.add(WARNING, f"Config/{expected} is missing.")
    engine_ini = config / "DefaultEngine.ini"
    if engine_ini.is_file():
        report.game_default_map = read_ini_value(
            engine_ini, "/Script/EngineSettings.GameMapsSettings", "GameDefaultMap")
        if not report.game_default_map:
            report.add(INFO, "GameDefaultMap is not set in DefaultEngine.ini (engine default map is used).")


def inspect_vcs(report: Report, root: Path) -> None:
    gitignore = root / ".gitignore"
    if (root / ".git").exists():
        if not gitignore.is_file():
            report.add(WARNING, "Git repository without a .gitignore: generated folders may be committed.")
        attributes = root / ".gitattributes"
        attr_text = attributes.read_text(encoding="utf-8", errors="replace") if attributes.is_file() else ""
        if "uasset" not in attr_text or "lfs" not in attr_text:
            report.add(INFO, "No Git LFS rule for *.uasset in .gitattributes; large binary assets will "
                             "bloat the repository.")
    if gitignore.is_file():
        text = gitignore.read_text(encoding="utf-8", errors="replace").lower()
        missing = [d for d in GENERATED_DIRS if d.lower() not in text]
        if missing:
            report.add(WARNING, ".gitignore does not mention: " + ", ".join(missing))


def validate(target: Path) -> Report:
    target = target.resolve()
    candidates = find_uproject(target)
    root = target.parent if target.is_file() else target
    report = Report(project_dir=str(root))
    if not candidates:
        report.add(ERROR, f"No .uproject file found in {root}.")
        return report
    if len(candidates) > 1:
        names = ", ".join(c.name for c in candidates)
        report.add(WARNING, f"Multiple .uproject files found ({names}); using {candidates[0].name}.")

    inspect_uproject(report, candidates[0])
    inspect_directories(report, root)
    inspect_source(report, root)
    inspect_plugins(report, root)
    inspect_content(report, root)
    inspect_config(report, root)
    inspect_vcs(report, root)
    return report


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def format_report(report: Report) -> str:
    lines: List[str] = []
    add = lines.append
    add("=" * 64)
    add(" Unreal Engine Project Validation Report")
    add("=" * 64)
    add(f"Project directory : {report.project_dir}")
    add(f".uproject         : {report.uproject or '(not found)'}")
    if report.uproject:
        add(f"Project name      : {report.project_name}")
        add(f"Engine            : {report.engine_association or '(unset)'}")
        if report.description:
            add(f"Description       : {report.description}")
        if report.target_platforms:
            add(f"Target platforms  : {', '.join(report.target_platforms)}")

        add("")
        add("Directories")
        for name, present in report.directories.items():
            add(f"  [{'x' if present else ' '}] {name}/")

        add("")
        add("C++ modules")
        if not report.declared_modules and not report.source_modules:
            add("  (none - Blueprint-only project)")
        for module in report.declared_modules:
            status = "found" if module["Name"] in report.source_modules else "MISSING SOURCE"
            add(f"  - {module['Name']} ({module['Type'] or '?'}, {module['LoadingPhase']}) - {status}")
        for name in report.source_modules:
            if name not in {m["Name"] for m in report.declared_modules}:
                add(f"  - {name} (Build.cs only, not in .uproject)")
        if report.targets:
            add(f"  Targets: {', '.join(report.targets)}")

        add("")
        add("Plugins")
        if not report.plugins:
            add("  (no plugins listed in the .uproject and none in Plugins/)")
        for plugin in sorted(report.plugins, key=lambda p: p.name.lower()):
            state = {True: "enabled", False: "disabled", None: "default"}[plugin.enabled]
            add(f"  - {plugin.name:<32} {state:<9} {plugin.source}")

        if report.asset_counts:
            add("")
            add(f"Content: {report.asset_counts.get('uasset', 0)} .uasset, "
                f"{report.asset_counts.get('umap', 0)} .umap")
        if report.config_files:
            add(f"Config : {', '.join(report.config_files)}")
        if report.game_default_map:
            add(f"GameDefaultMap: {report.game_default_map}")

    add("")
    add("Findings")
    if not report.issues:
        add("  No issues found.")
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    for issue in sorted(report.issues, key=lambda i: order[i.severity]):
        add(f"  [{issue.severity.upper():<7}] {issue.message}")

    counts = {s: sum(1 for i in report.issues if i.severity == s) for s in (ERROR, WARNING, INFO)}
    add("")
    add(f"Result: {counts[ERROR]} error(s), {counts[WARNING]} warning(s), {counts[INFO]} info")
    return "\n".join(lines)


def report_to_dict(report: Report) -> dict:
    data = asdict(report)
    data["error_count"] = report.error_count
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect an Unreal Engine project directory (read-only) and report "
                    "its structure and common problems.")
    parser.add_argument("project", nargs="?", default=".",
                        help="Project directory or path to the .uproject (default: current directory)")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args(argv)

    target = Path(args.project).expanduser()
    if not target.exists():
        print(f"error: path does not exist: {target}", file=sys.stderr)
        return 2

    report = validate(target)
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print(format_report(report))
    if report.uproject is None:
        return 2
    return 1 if report.error_count else 0


if __name__ == "__main__":
    sys.exit(main())
