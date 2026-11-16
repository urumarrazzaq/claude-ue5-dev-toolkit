# Installation

## Requirements

| Requirement | Notes |
|---|---|
| [Claude Code](https://code.claude.com/docs) | CLI, desktop app or IDE extension |
| Python 3.8+ | Standard library only - no `pip install` needed |
| Git | For cloning; the hook also protects git operations |
| Unreal Engine 5.x project | Launcher or source-built engine |

The toolkit is plain files: there is nothing to build or register. Claude Code
discovers `.claude/settings.json`, `.claude/skills/` and `.claude/agents/`
from the project directory, and `CLAUDE.md` from the project root.

## Option A - install into an existing UE project (recommended)

Copy the toolkit files next to your `.uproject`.

**Linux / macOS / Git Bash**

```bash
git clone https://github.com/urumarrazzaq/claude-ue5-dev-toolkit.git
cd /path/to/MyGame                       # folder that contains MyGame.uproject
cp -r ../claude-ue5-dev-toolkit/.claude .
cp ../claude-ue5-dev-toolkit/CLAUDE.md .
mkdir -p Tools/claude && cp ../claude-ue5-dev-toolkit/scripts/*.py Tools/claude/
```

**Windows PowerShell**

```powershell
git clone https://github.com/urumarrazzaq/claude-ue5-dev-toolkit.git
Set-Location D:\Projects\MyGame
Copy-Item -Recurse ..\claude-ue5-dev-toolkit\.claude .
Copy-Item ..\claude-ue5-dev-toolkit\CLAUDE.md .
New-Item -ItemType Directory -Force Tools\claude | Out-Null
Copy-Item ..\claude-ue5-dev-toolkit\scripts\*.py Tools\claude\
```

If your project already has a `.claude/settings.json` or `CLAUDE.md`, **merge**
instead of overwriting: add the `hooks.PreToolUse` entry and the permission
rules to your existing settings, and append the working rules to your
`CLAUDE.md`.

Then edit the *Project facts* section at the top of `CLAUDE.md` and update the
script path in it if you copied the scripts somewhere other than `scripts/`.

## Option B - try the toolkit on its own

```bash
git clone https://github.com/urumarrazzaq/claude-ue5-dev-toolkit.git
cd claude-ue5-dev-toolkit
python -m unittest discover -s tests -v
claude
```

## Windows Python command

The hook command in `.claude/settings.json` is:

```json
"command": "python3 \"${CLAUDE_PROJECT_DIR}/.claude/hooks/ue5_safety_guard.py\""
```

On Windows, Claude Code runs hook commands through Git Bash when it is
installed (otherwise PowerShell). On many Windows machines `python3` resolves
to the **Microsoft Store placeholder**, which prints *"Python was not found"*
and exits with an error. Claude Code treats that as a non-blocking hook error,
so **the guard would not run**. Check which applies to you:

```powershell
python3 -c "print('ok')"
py -3 -c "print('ok')"
```

If only `py -3` prints `ok`, change the command to:

```json
"command": "py -3 \"${CLAUDE_PROJECT_DIR}/.claude/hooks/ue5_safety_guard.py\""
```

Alternatively disable the `python3.exe` alias under *Settings → Apps → Advanced
app settings → App execution aliases* and make sure a real `python3` is on
`PATH`.

## Verify the installation

1. **Hook responds** (run from the project root; use `py -3` on Windows if needed):

   ```bash
   echo '{"tool_name":"Bash","tool_input":{"command":"git reset --hard"},"cwd":"."}' | python3 .claude/hooks/ue5_safety_guard.py
   ```

   Expected: a JSON object containing `"permissionDecision": "deny"`.

2. **Structure is valid:**

   ```bash
   python3 Tools/claude/check_toolkit.py .     # or scripts/check_toolkit.py in the toolkit repo
   ```

3. **Inside Claude Code:**
   - Run `/hooks` (CLI) to see the `PreToolUse` hook listed.
   - Run `/agents` to see `ue5-architect`, `ue5-cpp-developer` and `ue5-code-reviewer`.
   - Ask: *"What skills are available?"* - the six `ue5-*` skills should be listed.
   - Ask Claude to run `git reset --hard`; it should report that the UE5 safety
     guard blocked the command.

## Configuration

| Setting | Where | Purpose |
|---|---|---|
| Hook command / timeout | `.claude/settings.json` → `hooks.PreToolUse` | Python launcher, 10 s timeout |
| Fail-closed mode | environment variable `UE5_GUARD_FAIL_CLOSED=1` | Block the command if the hook cannot parse its input (default: fail open with a visible hook error) |
| Allowed read-only git | `.claude/settings.json` → `permissions.allow` | Skip prompts for `git status/diff/log/show` |
| Binary asset protection | `.claude/settings.json` → `permissions.deny` | Deny `Edit` on `.uasset`, `.umap` and generated folders |
| Personal overrides | `.claude/settings.local.json` (git-ignored) | Machine-specific settings |

To set the environment variable for Claude Code sessions in this project, add
an `env` block to `.claude/settings.json` or `.claude/settings.local.json`:

```json
{ "env": { "UE5_GUARD_FAIL_CLOSED": "1" } }
```

## Uninstall

Delete `.claude/hooks/ue5_safety_guard.py`, the `ue5-*` folders under
`.claude/skills/`, the three `ue5-*.md` files under `.claude/agents/`, the
`PreToolUse` entry in `.claude/settings.json`, and the toolkit section of
`CLAUDE.md`.
