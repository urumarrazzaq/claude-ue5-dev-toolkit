# UE5 Safety Guard hook

`ue5_safety_guard.py` is a Claude Code **PreToolUse** hook that inspects every
`Bash` and `PowerShell` command before it runs and stops the ones that can
destroy work in an Unreal Engine project.

## How it is wired

`.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PROJECT_DIR}/.claude/hooks/ue5_safety_guard.py\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

> **Windows:** on many Windows machines `python3` is the Microsoft Store
> placeholder, not a real interpreter. Use the Python launcher instead:
> `"command": "py -3 \"${CLAUDE_PROJECT_DIR}/.claude/hooks/ue5_safety_guard.py\""`.
> See [docs/installation.md](../../docs/installation.md#windows-python-command).

## Protocol

**Input** - Claude Code writes a JSON object to stdin. The fields the hook uses:

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "git reset --hard", "description": "..." },
  "cwd": "D:/Projects/ShooterGame"
}
```

**Output** - one of:

| Outcome | stdout | exit code | Effect in Claude Code |
|---|---|---|---|
| Nothing suspicious | *(empty)* | 0 | Normal permission rules apply |
| Risky | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "..."}}` | 0 | User is asked to confirm |
| Destructive | same shape with `"permissionDecision": "deny"` | 0 | Command is blocked; reason is shown to Claude |
| Unparseable input | *(empty)*, message on stderr | 1 | Non-blocking hook error; command proceeds |
| Unparseable input with `UE5_GUARD_FAIL_CLOSED=1` | *(empty)*, message on stderr | 2 | Command is blocked |

The hook **never returns `allow`**, so it cannot bypass your permission rules -
it can only make Claude Code stricter.

## Rules

### Denied

| Category | Examples |
|---|---|
| Git history / work destruction | `git reset --hard`, `git clean -f[d][x]`, `git push --force` / `-f` / `+ref`, `git push --mirror`, `git checkout -- .`, `git checkout -f`, `git restore .`, `git stash clear`, `git filter-branch`, `git filter-repo`, `git reflog expire`, `git gc --prune=now` |
| Recursive deletion of everything | `rm -rf /`, `~`, `$HOME`, `.`, `..`, `*`, a drive root (`C:\`, `/c`, `/mnt/c`), `--no-preserve-root` |
| Recursive deletion of protected UE folders | `Source`, `Content`, `Config`, `Plugins`, `.git` (also `Content/*`, nested plugin `Source`) |
| Recursive deletion of the project itself | any path that resolves to the session `cwd` or one of its parents |
| Disk destruction | `mkfs*`, `dd of=/dev/...`, `Format-Volume`, `Clear-Disk`, `format C:` |

### Asked

- `git push --force-with-lease`, deleting remote branches (`--delete`, `:branch`)
- `git branch -D`, `git stash drop`, `git update-ref -d`
- `git restore <file>` / `git checkout -- <file>` (discards edits to specific files)
- Recursive deletion of a path built from a variable (`rm -rf $BUILD_DIR/`) -
  an empty variable could expand to `/`
- Deleting `.uasset`/`.umap` (bypasses editor redirectors), `.uproject`,
  `.uplugin`, `*.Build.cs`, `*.Target.cs`
- `find ... -delete` outside protected folders, `shred`

### Not flagged (by design)

- Read-only git (`status`, `log`, `diff`, `show`), `git reset --soft`, unstaging.
- `git clean -n` (dry run).
- Recursive deletion of regenerable folders: `Binaries`, `Intermediate`,
  `DerivedDataCache`, `Saved`, or any other ordinary path.
- Text inside quotes, e.g. `git commit -m "never run git reset --hard"`.

## Command analysis

The hook understands:

- Command chains: `;`, `&&`, `||`, `|`, `&`, newlines (quote-aware).
- Wrappers: `sudo`, `env VAR=1`, `xargs`, `nohup`, `time`, `command`.
- Nested shells: `bash -c "..."`, `sh -c`, `pwsh -Command "..."`,
  `powershell -Command`, `cmd /c`.
- POSIX `rm`, PowerShell `Remove-Item` and its aliases (`rm`, `ri`, `del`,
  `rmdir`, `rd`, `erase`) including `-Recurse`, `-Path`, `-LiteralPath`, and
  cmd's `rmdir /s` / `del /s`.
- Windows, Git Bash (`/d/...`) and WSL (`/mnt/d/...`) path forms.

## Limitations

This is pattern-based static analysis, not a sandbox. It cannot see through
scripts it does not read (`./cleanup.sh`, `python -c "shutil.rmtree(...)"`),
arbitrary variable indirection, aliases defined in the user's profile, or
`eval`. Conversely, because newlines separate commands, a heredoc *body*
that contains a destructive command line (e.g. writing a cleanup script with
`cat <<EOF`) can be flagged even though it is only text. Treat it as a guard rail against accidents, combined with Claude
Code's permission system and version control - not as a security boundary.

## Testing

```bash
python -m unittest tests.test_safety_guard -v
```

Manual check:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git reset --hard"},"cwd":"."}' | python3 .claude/hooks/ue5_safety_guard.py
```
