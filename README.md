# autoreviewer

> Per-repo automatic code review on every git commit, powered by **Claude Code** (`claude`).
> Works with **all** git clients (CLI, Fork, SourceTree, IDEs). Reviews run in the background; macOS notifications are clickable to open the report.

🌏 **中文文档**: [README.zh-CN.md](README.zh-CN.md)

## TL;DR

```bash
# 1. Install the tool itself (once per machine)
./install.sh

# 2. Opt in repos individually
cd /path/to/your/repo
autoreviewer install

# 3. From now on, every commit in this repo triggers a background review
git commit -m "feat: add login"
# → commit returns immediately
# → review runs in the background, driven by Claude Code
# → macOS notification pops with severity + click-to-open

# Other useful commands
autoreviewer status                  # see install state for current repo
autoreviewer run                     # manual sync review
autoreviewer log                     # browse history
autoreviewer show a3f8c21            # view one
autoreviewer install --uninstall     # opt this repo out
autoreviewer disable                 # global pause without uninstall
AUTOREVIEWER_SKIP=1 git commit ...   # skip review for one commit
```

## Why Claude Code as the engine?

Claude Code (`claude`) is Anthropic's official agentic coding CLI. autoreviewer
hands the commit diff to `claude -p` in non-interactive mode and asks for a
strict-JSON review. Because Claude Code can use its native `Read` / `Grep` /
`Glob` / `Bash` tools inside the repo, the review isn't limited to the diff —
it can pull in callers, related tests, and config files when judging impact.

If you'd rather use a different LLM CLI, the backend is a single line in
`~/.autoreviewer/config.json` (`command`). Anything that accepts a prompt on
stdin and prints a response to stdout will work.

## Why per-repo?

Git's design treats each repo as the owner of its hook configuration. Many
projects intentionally set their own `core.hooksPath` (Husky, Lefthook,
project `.githooks/`) to enforce team-wide commit rules. A "globally
hooked-into-everything" install conflicts with this and breaks those projects.

Per-repo opt-in is:

- **Predictable** — `autoreviewer status` tells you exactly what will happen
- **Respectful** — projects with their own hook setup keep working
- **Reversible** — `autoreviewer install --uninstall` restores the original state cleanly
- **Cheap** — one command per repo, only for repos you actually want reviewed

## How it works

**Zero modification to any tracked file in your project.** All install state
lives under `<repo>/.git/` (which is local to your machine, never committed).

`autoreviewer install` does one of two things based on the current repo:

### Vanilla repo (no local `core.hooksPath`)

```
git config --local core.hooksPath ~/.autoreviewer/hooks
```

Git for this repo will use our shared hook directory.

### Repo overrides `core.hooksPath` (Husky / Lefthook / project `.githooks/`)

This is "chain mode":

```
1. Save the project's current hooksPath (e.g. ".husky") into:
     <repo>/.git/autoreviewer.json   ← under .git/, NOT tracked
2. Override local core.hooksPath to ~/.autoreviewer/hooks/
3. Our hook directory contains shims for every standard git hook
   (commit-msg, pre-commit, pre-push, ...). Each shim runs _chain,
   which exec's the project's original hook from the saved path.
4. post-commit shim ALSO triggers an autoreviewer review first.
```

Result: your team's `commit-msg`, `pre-push`, etc. all still fire normally.
Plus you get a review on every commit. Other team members are completely
unaffected — they don't even see any new files in `git status`.

### On commit, regardless of mode

```
git commit (CLI / GUI / IDE — any client)
      │
      ▼
~/.autoreviewer/hooks/post-commit
      │
      ├─→ background: python3 runner.py HEAD
      │       ├─→ claude -p   (full prompt + diff fed via stdin)
      │       ├─→ parse JSON, write .git/reviews/<date>_<hash>.{json,md}
      │       ├─→ update index.json
      │       └─→ terminal-notifier (clickable → open .md)
      │
      └─→ exec _chain post-commit "$@"
              ├─→ chained_hooks_path/post-commit (Husky/Lefthook case)
              └─→ <repo>/.git/hooks/post-commit (vanilla fallback)

# For OTHER hook types (commit-msg etc), git invokes our shim directly:
git commit-msg "$1"
      │
      ▼
~/.autoreviewer/hooks/commit-msg
      │
      └─→ exec _chain commit-msg "$@"
              └─→ <chained-path>/commit-msg or <repo>/.git/hooks/commit-msg
```

## Install

### Prerequisites

- macOS (the notification flow uses `terminal-notifier`)
- git ≥ 2.9
- python3 (any 3.x)
- Homebrew (the installer uses it for `jq` + `terminal-notifier`)
- **Claude Code** — `claude` available in PATH. Install via Anthropic's docs:
  <https://docs.claude.com/en/docs/claude-code>. After installation, run
  `claude` once interactively to complete authentication.

### Install the tool

```bash
git clone https://github.com/jsaddnf/auto_reviewer_local.git autoreviewer
cd autoreviewer
./install.sh
```

The installer:

1. Checks dependencies; offers to `brew install jq terminal-notifier` if missing
2. Installs files to `~/.autoreviewer/`
3. Installs the `autoreviewer` CLI to `~/.local/bin/`
4. **Does NOT touch git config** — per-repo opt-in is the canonical path

### Opt a repo in

```bash
cd /path/to/repo
autoreviewer install
```

Run this once in each repo where you want commits reviewed. `autoreviewer
install` is **idempotent** — running it again is safe and reports "already
installed".

To check the state at any time:

```bash
autoreviewer status
```

### Opt out

```bash
cd /path/to/repo
autoreviewer install --uninstall
```

Cleanly removes whatever `autoreviewer install` did:

- For vanilla repos: unsets the local `core.hooksPath`
- For Husky/Lefthook repos: removes only our marker block from `post-commit`
  (and deletes the file if it contained nothing else)

### Uninstall the tool entirely

```bash
./uninstall.sh    # or: autoreviewer uninstall
```

Removes `~/.autoreviewer/` and the CLI. Per-repo `.git/reviews/` data is
preserved, and per-repo install state is left intact (because each repo's
hooksPath setting is local to it).

## Commands

```
autoreviewer install [--repo PATH] [--uninstall|--upgrade]
                                             Opt this repo in (or out / upgrade)
autoreviewer enable [--repo PATH]            Resume reviews (counterpart to disable)
autoreviewer disable [--repo PATH]           Pause reviews without uninstalling
autoreviewer status                          Show install state + on/off + counts
autoreviewer run [<commit>] [options]        Manually trigger a review
                                               --async       run in background
                                               --no-notify   skip notification
                                               --open        open .md when done
autoreviewer log [-n N] [--severity LEVEL]   List recent reviews in current repo
autoreviewer show [<hash>] [--open] [--json] Show a review (default: latest)
autoreviewer clean --all | --older-than 30d  Clean up review files
autoreviewer cancel                          Kill the currently running review
autoreviewer uninstall                       Uninstall everything
```

### Sync vs async — by command

- `git commit` → **async** (so you're never blocked)
- `autoreviewer run` → **sync** by default (you asked, you wait)
- Override with `--async` or `--sync` flags

### Install vs enable/disable — different concepts

- **install / install --uninstall**: physical wiring. Sets up or removes the hook trigger.
- **enable / disable**: temporary on/off. Hook still runs but exits early when disabled.

Use `disable` for short pauses (e.g., a series of WIP commits). Use
`install --uninstall` to cleanly remove autoreviewer from a repo.

## Configuration

### Global: `~/.autoreviewer/config.json`

```json
{
  "enabled": true,
  "command": "claude",
  "command_args": ["-p"],
  "prompt_file": "~/.autoreviewer/prompts/default.txt",
  "notification": "terminal-notifier",
  "notify_threshold": "low",
  "auto_open": "on_high",
  "timeout_seconds": 180,
  "disabled_repos": []
}
```

| Key | Values | Description |
|---|---|---|
| `enabled` | `true`/`false` | Master switch |
| `command` | string | Binary to invoke (default `claude` — Claude Code) |
| `command_args` | string[] | Extra args appended to the command. Default `["-p"]` (Claude Code's non-interactive print mode) |
| `prompt_file` | path | Review prompt template |
| `notification` | `terminal-notifier`/`osascript`/`none` | Notification mode |
| `notify_threshold` | `ok`/`low`/`medium`/`high` | Skip notifications below this severity |
| `auto_open` | `true`/`false`/`on_high` | Auto-open the .md when review completes |
| `timeout_seconds` | number | Review timeout. Claude Code finishes small commits in 30–60s; raise for big diffs |
| `disabled_repos` | string[] | Absolute paths to exclude |

### Per-repo: `<repo>/.git/autoreviewer.json`

Same shape. Per-repo overrides global. Useful for project-specific prompts:

```json
{
  "enabled": true,
  "prompt_file": "./.autoreviewer-prompt.md"
}
```

## Output

Each review writes two files plus an index:

```
<repo>/.git/reviews/
├── 2026-04-27_143205_a3f8c21.json   # structured data
├── 2026-04-27_143205_a3f8c21.md     # human-readable
├── index.json                         # for `autoreviewer log`
├── .running.pid                       # currently-running review PID
└── .last-run.log                      # stderr of the last background run
```

The Markdown contains: summary, severity, issue list (with file:line), impact
analysis (modified files + affected callers), and improvement suggestions.

## Compatibility with existing hooks

`autoreviewer install` **never modifies tracked files** in your project. All
state lives under `<repo>/.git/`:

| What we change | Where | Tracked? |
|---|---|---|
| Local `core.hooksPath` | `<repo>/.git/config` | ❌ no |
| Saved `chained_hooks_path` | `<repo>/.git/autoreviewer.json` | ❌ no |
| Review outputs | `<repo>/.git/reviews/` | ❌ no |

For repos using Husky / Lefthook / `.githooks/`, **all of your team's hooks
still fire**. Our shims simply delegate to them via the chain helper. Your
teammates can clone the repo without any trace of autoreviewer.

### Migration from old (pre-chain) versions

If you installed autoreviewer before the chain refactor, you may have a
manual `post-commit` shim sitting in `<repo>/.husky/` or `<repo>/.githooks/`.
Plain `autoreviewer install` will detect it and refuse — run:

```bash
autoreviewer install --upgrade
```

This:
1. Backs up the legacy shim (`*.bak.<timestamp>`)
2. Strips the autoreviewer trigger from it (deletes the file if nothing else was there)
3. Sets up chain mode cleanly
4. **Warns you if the shim file is tracked in git** so you can `git rm` it
   and commit the removal — otherwise your teammates would still have the
   stale autoreviewer reference

## Works with GUI git clients

Any tool that ultimately calls `git commit` triggers the hook:

- ✅ Command line `git commit`
- ✅ Fork
- ✅ SourceTree
- ✅ GitKraken
- ✅ GitHub Desktop
- ✅ Tower
- ✅ VS Code Source Control
- ✅ JetBrains IDEs (IntelliJ / PyCharm / WebStorm / GoLand)
- ✅ Lazygit / Magit / other TUIs

The review runs detached via `nohup ... & disown`, so GUI tools never freeze
or show review output in their panels.

## Troubleshooting

**Reviews aren't running after commit.**
Run `autoreviewer status` first. The "Current repo" block tells you the
install state. If it says "not installed ❌", run `autoreviewer install`. If
the repo overrides `core.hooksPath` and isn't installed, the message will
say so explicitly.

**`claude: command not found` in `.last-run.log`.**
Claude Code isn't installed or isn't on the PATH that git hooks use. Install
it from <https://docs.claude.com/en/docs/claude-code> and confirm `which claude`
prints a path. If your shell finds `claude` but the hook doesn't, point
`command` in `~/.autoreviewer/config.json` at the absolute path
(e.g. `/opt/homebrew/bin/claude`).

**Claude Code prompts for auth on first run.**
Run `claude` once in a normal terminal to complete sign-in before relying on
the hook — the hook runs non-interactively and can't answer prompts.

**Notification appeared but clicking does nothing.**
Verify `terminal-notifier --help` works. If missing,
`brew install terminal-notifier`. Click-to-open relies on terminal-notifier's
`-execute` flag which native `osascript` doesn't support.

**JSON parse failure in the log.**
The runner saves the raw output to `<reviews>/<...>.raw.txt` for debugging.
The model occasionally adds a sentence before the JSON — tighten the prompt
or set `command_args` to `["-p", "--output-format", "json"]` so Claude Code
wraps the response in a structured envelope (the parser unwraps it).

**A review is stuck.**
`autoreviewer cancel` (or `kill $(cat .git/reviews/.running.pid)`).

**I want a different LLM tool.**
Edit `command` (and `command_args` if needed) in `~/.autoreviewer/config.json`
to any binary that takes a prompt on stdin and prints the response on stdout.

**I previously installed with the global model — what now?**
You'll see a notice when you run `./install.sh`. Both work. To migrate to
per-repo:

```bash
git config --global --unset core.hooksPath
# then in each repo you want autoreviewer in:
autoreviewer install
```

## Not yet implemented (future)

- `autoreviewer scan PATH` — bulk-install across all repos under a directory
- CI mode (`autoreviewer run --ci` for pipelines)
- `git notes` integration so reviews can be pushed to remote
- Web dashboard from `index.json`

## License

MIT
