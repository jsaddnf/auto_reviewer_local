#!/usr/bin/env python3
"""
autoreviewer runner

Invokes the configured review command (default: ``claude`` — Anthropic's
Claude Code CLI) on a git commit, parses the JSON response, writes
``<date>_<hash>.{json,md}`` into ``<repo>/.git/reviews/``, updates
``index.json``, and sends a macOS notification.

Usage:
    runner.py <commit>             # default async (forked, returns immediately)
    runner.py <commit> --sync      # foreground, prints result
    runner.py <commit> --no-notify # skip notification
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

AUTOREVIEWER_HOME = Path(os.environ.get("AUTOREVIEWER_HOME", str(Path.home() / ".autoreviewer")))
GLOBAL_CONFIG = AUTOREVIEWER_HOME / "config.json"
DEFAULT_PROMPT = AUTOREVIEWER_HOME / "prompts" / "default.txt"

SEVERITY_RANK = {"ok": 0, "low": 1, "medium": 2, "high": 3}
SEVERITY_EMOJI = {"ok": "🟢", "low": "🟡", "medium": "🟡", "high": "🔴"}

# Localized strings for the rendered report and notifications. Selected by
# config["language"] (default "zh"). Header metadata (Code Review/Commit/
# Author/Date/Files/Severity) stays English in both — those read fine in any
# CJK document and avoid pointless translation overhead.
LABELS = {
    "zh": {
        # markdown sections
        "summary": "总结",
        "issues": "问题清单",
        "no_issues": "无",
        "issue_suggestion_label": "建议",
        "impact": "影响范围",
        "modified_files": "直接修改的文件",
        "modified_symbols": "修改的调用栈",
        "affected_callers": "可能受影响的调用方",
        "behavioral_changes": "行为/逻辑变化",
        "risk_level": "风险等级",
        "tests": "测试建议",
        "test_case_label": "用例",
        "test_why_label": "理由",
        "suggestions": "改进建议",
        # parse-failure report
        "parse_failed_status": "解析失败",
        "parse_failed_body": "无法从模型输出中提取合法 JSON。下面是各阶段错误：",
        "raw_output_pointer": "原始输出",
        "raw_output_preview": "原始输出预览",
        "raw_output_truncated": "（已截断，完整内容见 raw 文件）",
        # notifications
        "started_message": "⏳ 后台审核中",
        "click_to_open": "点击查看",
        "issue_word": "个问题",         # singular
        "issues_word": "个问题",        # plural — Chinese has no count agreement
        # prompt directive
        "prompt_directive": (
            "Use Chinese (简体中文) for all human-readable text in "
            "summary/message/suggestion/reason/case/why fields. "
            "Keep file paths, symbol names, and code snippets in their original form."
        ),
    },
    "en": {
        "summary": "Summary",
        "issues": "Issues",
        "no_issues": "None",
        "issue_suggestion_label": "Fix",
        "impact": "Impact",
        "modified_files": "Modified files",
        "modified_symbols": "Modified call stack",
        "affected_callers": "Affected callers",
        "behavioral_changes": "Behavioral / logic changes",
        "risk_level": "Risk level",
        "tests": "Test suggestions",
        "test_case_label": "Case",
        "test_why_label": "Why",
        "suggestions": "General suggestions",
        "parse_failed_status": "Parse failed",
        "parse_failed_body": "Could not extract valid JSON from the model output. Errors per stage:",
        "raw_output_pointer": "Raw output",
        "raw_output_preview": "Raw output preview",
        "raw_output_truncated": "(truncated, see raw file for full output)",
        "started_message": "⏳ Running review in background",
        "click_to_open": "click to open",
        "issue_word": "issue",
        "issues_word": "issues",
        "prompt_directive": (
            "Use English for all human-readable text in "
            "summary/message/suggestion/reason/case/why fields. "
            "Keep file paths, symbol names, and code snippets in their original form."
        ),
    },
}


def get_labels(lang: str) -> dict:
    """Return the LABELS map for the configured language, with zh as fallback
    for any unsupported value (e.g. legacy configs predating this feature)."""
    return LABELS.get(lang, LABELS["zh"])


# ---------- config ----------

def load_config(repo_root: Path) -> dict:
    """Merge global config with per-repo override."""
    cfg = {
        "enabled": True,
        # Backend: Claude Code (`claude`) by default. Any drop-in replacement
        # accepting `-p <prompt>` and emitting JSON (or text containing JSON)
        # on stdout will work.
        "command": "claude",
        # Extra args appended to the command. Default: text output (the
        # extractor below tolerates Claude Code's JSON wrapper too).
        "command_args": ["-p"],
        "prompt_file": str(DEFAULT_PROMPT),
        "notification": "terminal-notifier",
        "notify_threshold": "low",
        "auto_open": "on_high",
        "timeout_seconds": 180,
        "disabled_repos": [],
    }
    if GLOBAL_CONFIG.exists():
        try:
            cfg.update(json.loads(GLOBAL_CONFIG.read_text()))
        except Exception as e:
            print(f"[autoreviewer] WARN: bad global config: {e}", file=sys.stderr)
    repo_cfg = repo_root / ".git" / "autoreviewer.json"
    if repo_cfg.exists():
        try:
            cfg.update(json.loads(repo_cfg.read_text()))
        except Exception as e:
            print(f"[autoreviewer] WARN: bad repo config: {e}", file=sys.stderr)
    return cfg


# ---------- git helpers ----------

def git(*args, cwd=None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def get_commit_info(commit: str, repo_root: Path) -> dict:
    full_hash = git("rev-parse", commit, cwd=repo_root)
    short_hash = git("rev-parse", "--short", full_hash, cwd=repo_root)
    message = git("log", "-1", "--format=%s", full_hash, cwd=repo_root)
    author = git("log", "-1", "--format=%an", full_hash, cwd=repo_root)
    date = git("log", "-1", "--format=%aI", full_hash, cwd=repo_root)  # ISO 8601
    stats = git("show", "--shortstat", "--format=", full_hash, cwd=repo_root)
    additions = sum(int(m) for m in re.findall(r"(\d+) insertion", stats))
    deletions = sum(int(m) for m in re.findall(r"(\d+) deletion", stats))
    files_changed = sum(int(m) for m in re.findall(r"(\d+) file", stats))
    return {
        "full_hash": full_hash,
        "short_hash": short_hash,
        "message": message,
        "author": author,
        "date": date,
        "files_changed": files_changed,
        "additions": additions,
        "deletions": deletions,
    }


def get_diff(commit: str, repo_root: Path) -> str:
    return git("show", "--no-color", commit, cwd=repo_root)


# ---------- review invocation ----------

def _balance_brackets(text: str):
    """Append missing closing braces/brackets at the end based on a string-aware
    paren stack. Returns repaired text, or None if nothing to repair."""
    in_string = False
    escape = False
    stack = []
    for ch in text:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch == "}" or ch == "]":
            if stack and stack[-1] == ch:
                stack.pop()
            else:
                # Mismatched closer — give up, can't safely repair
                return None
    if in_string:
        # Unterminated string — close it before brackets
        return text + '"' + "".join(reversed(stack)) if stack else None
    if not stack:
        return None
    return text + "".join(reversed(stack))


def extract_json(text: str) -> dict:
    """Try multiple strategies to extract JSON object from raw output."""
    # Strategy 1: parse directly
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    # Strategy 2: Claude Code wrapper output, e.g.
    #   {"type":"result","subtype":"success","result":"<json string>", ...}
    # or simpler { "result": "..." } wrappers.
    try:
        wrapped = json.loads(text)
        if isinstance(wrapped, dict) and "result" in wrapped:
            return extract_json(wrapped["result"])
    except Exception:
        pass

    # Strategy 3: strip markdown fences
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except Exception:
            pass

    # Strategy 4: greedy find first { ... last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except Exception:
            pass

    # Strategy 5: structural repair — append missing closers based on bracket stack.
    # LLMs frequently truncate or miscount the trailing braces; this fixes those
    # cases without re-prompting.
    if first != -1:
        candidate = text[first:].rstrip()
        # Strip trailing markdown fence noise if present
        candidate = re.sub(r"\s*```\s*$", "", candidate)
        # Try raw_decode first in case there's only trailing garbage
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(candidate)
            return obj
        except Exception:
            pass
        repaired = _balance_brackets(candidate)
        if repaired and repaired != candidate:
            try:
                return json.loads(repaired)
            except Exception:
                pass

    raise ValueError("Could not extract valid JSON from output")


def run_review_command(cmd: str, prompt: str, timeout: int, extra_args=None) -> str:
    """Invoke the review backend (Claude Code by default).

    The full prompt — schema instructions + commit metadata + diff — is
    delivered via stdin to avoid OS command-line length limits. Claude Code
    invoked as ``claude -p`` reads stdin as the prompt, runs once
    non-interactively, and prints the response to stdout.
    """
    extra_args = list(extra_args or [])
    # Default extra args: just `-p` (non-interactive print mode for Claude Code).
    if not extra_args:
        extra_args = ["-p"]
    proc = subprocess.run(
        [cmd, *extra_args],
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd} failed (exit {proc.returncode}): {proc.stderr}")
    return proc.stdout


def repair_with_llm(cmd: str, raw: str, timeout: int, extra_args=None) -> dict:
    """Strategy D: open a fresh conversation asking the model to fix its own
    malformed JSON. We pass only the broken output (no diff) so the second pass
    is cheap and focused purely on structural repair.

    Honors the same `command_args` config as the main review call so the
    backend is invoked consistently (default `-p` for Claude Code).
    """
    extra_args = list(extra_args or [])
    if not extra_args:
        extra_args = ["-p"]
    repair_prompt = (
        "下面是上一轮模型输出，本应是符合 schema 的 JSON，但解析失败"
        "（通常是括号不平衡、字段层级错误，或字符串未正确转义）。\n\n"
        "请你只做结构修复：\n"
        "1. 不要改动任何文本内容（summary / message / suggestion 等字段值保持原样）；\n"
        "2. 不要新增、不要删除字段；\n"
        "3. 修正层级错误（例如 `suggestions` 必须是顶层字段，与 `impact` 同级，不应嵌套在 `impact` 内）；\n"
        "4. 补全缺失的括号，转义未转义的引号；\n"
        "5. 直接输出修复后的 JSON 对象，不要 markdown 围栏，不要解释，不要前后多余文字。\n\n"
        "--- BROKEN OUTPUT ---\n" + raw
    )
    proc = subprocess.run(
        [cmd, *extra_args],
        input=repair_prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"repair {cmd} failed (exit {proc.returncode}): {proc.stderr}")
    return extract_json(proc.stdout)


# ---------- rendering ----------

def render_markdown(review: dict, info: dict, labels=None) -> str:
    if labels is None:
        labels = LABELS["zh"]
    sev = (review.get("severity") or "ok").lower()
    sev_emoji = SEVERITY_EMOJI.get(sev, "⚪")

    out = [
        f"# Code Review · {info['short_hash']}",
        "",
        f"**Commit**: {info['message']}",
        f"**Author**: {info['author']}",
        f"**Date**: {info['date']}",
        f"**Files**: {info['files_changed']} changed (+{info['additions']} -{info['deletions']})",
        f"**Severity**: {sev_emoji} {sev.upper()}",
        "",
    ]

    summary = review.get("summary", "").strip()
    if summary:
        out += [f"## {labels['summary']}", summary, ""]

    issues = review.get("issues", []) or []
    if issues:
        out += [f"## {labels['issues']}", ""]
        for it in issues:
            lvl = (it.get("level") or "medium").lower()
            emoji = SEVERITY_EMOJI.get(lvl, "⚪")
            file = it.get("file", "?")
            line = it.get("line", "?")
            out.append(f"### {emoji} [{lvl.upper()}] {file}:{line}")
            out.append(it.get("message", ""))
            sug = it.get("suggestion", "")
            if sug:
                out.append(f"**{labels['issue_suggestion_label']}**: {sug}")
            out.append("")
    else:
        out += [f"## {labels['issues']}", labels["no_issues"], ""]

    impact = review.get("impact", {}) or {}
    if impact:
        out += [f"## {labels['impact']}", ""]
        modified = impact.get("modified_files", []) or []
        if modified:
            out.append(f"**{labels['modified_files']}**:")
            for f in modified:
                out.append(f"- {f}")
            out.append("")
        symbols = impact.get("modified_symbols", []) or []
        if symbols:
            out.append(f"**{labels['modified_symbols']}**:")
            for s in symbols:
                file = s.get("file", "?")
                sym = s.get("symbol", "?")
                kind = s.get("kind", "")
                change = s.get("change", "")
                sym_summary = s.get("summary", "")
                tag_parts = [p for p in [kind, change] if p]
                tag = f" *({', '.join(tag_parts)})*" if tag_parts else ""
                out.append(f"- `{sym}`{tag} — {file}")
                if sym_summary:
                    out.append(f"  - {sym_summary}")
            out.append("")
        callers = impact.get("affected_callers", []) or []
        if callers:
            out.append(f"**{labels['affected_callers']}**:")
            for c in callers:
                file = c.get("file", "?")
                line = c.get("line", "?")
                caller = c.get("caller", "")
                reason = c.get("reason", "")
                caller_part = f" ({caller})" if caller and caller != "?" else ""
                out.append(f"- {file}:{line}{caller_part} — {reason}")
            out.append("")
        behavior = impact.get("behavioral_changes", []) or []
        if behavior:
            out.append(f"**{labels['behavioral_changes']}**:")
            for b in behavior:
                out.append(f"- {b}")
            out.append("")
        risk = impact.get("risk_level", "")
        if risk:
            out.append(f"**{labels['risk_level']}**: {risk}")
            out.append("")

    tests = review.get("tests", []) or []
    if tests:
        out += [f"## {labels['tests']}", ""]
        for t in tests:
            ttype = t.get("type", "test")
            target = t.get("target", "?")
            case = t.get("case", "")
            why = t.get("why", "")
            out.append(f"### [{ttype.upper()}] {target}")
            if case:
                out.append(f"**{labels['test_case_label']}**: {case}")
            if why:
                out.append(f"**{labels['test_why_label']}**: {why}")
            out.append("")

    suggestions = review.get("suggestions", []) or []
    if suggestions:
        out += [f"## {labels['suggestions']}", ""]
        for s in suggestions:
            out.append(f"- {s}")
        out.append("")

    return "\n".join(out)


def render_failure_markdown(info: dict, errors, raw_path: Path, raw: str, labels=None) -> str:
    """Degraded report written when JSON parsing (and LLM repair) all fail.
    Ensures `--open` and the click-to-open notification still land somewhere."""
    if labels is None:
        labels = LABELS["zh"]
    snippet = raw[:5000]
    if len(raw) > 5000:
        snippet += "\n\n... " + labels["raw_output_truncated"]
    out = [
        f"# Code Review · {info['short_hash']} · ⚠️ PARSE FAILED",
        "",
        f"**Commit**: {info['message']}",
        f"**Author**: {info['author']}",
        f"**Date**: {info['date']}",
        f"**Files**: {info['files_changed']} changed (+{info['additions']} -{info['deletions']})",
        "",
        f"## {labels['parse_failed_status']}",
        "",
        labels["parse_failed_body"],
        "",
    ]
    for i, e in enumerate(errors, 1):
        out.append(f"{i}. {e}")
    out += [
        "",
        f"{labels['raw_output_pointer']}: `{raw_path}`",
        "",
        f"## {labels['raw_output_preview']}",
        "",
        "```",
        snippet,
        "```",
        "",
    ]
    return "\n".join(out)


# ---------- notifications ----------

def _send_notification(title: str, subtitle: str, message: str, mode: str, click_target=None):
    """Internal: dispatch a desktop notification across supported backends.

    Args:
        click_target: if given, clicking the notification opens this path.
            Only the terminal-notifier backend honors this; osascript has no
            click-action support and silently ignores it.
    """
    if mode == "none":
        return
    if mode == "terminal-notifier" and shutil.which("terminal-notifier"):
        # IMPORTANT: do NOT pass -sender. macOS routes click events to the
        # sender app instead of running -execute, breaking click-to-open.
        # terminal-notifier's icon/name will show, which is fine.
        cmd = [
            "terminal-notifier",
            "-title", title,
            "-subtitle", subtitle,
            "-message", message,
        ]
        if click_target is not None:
            cmd += ["-execute", f"open {str(click_target)!r}"]
        subprocess.run(cmd, capture_output=True)
        return
    # Fallback: osascript (no click-action support)
    safe_msg = message.replace('"', '\\"')
    safe_title = title.replace('"', '\\"')
    safe_sub = subtitle.replace('"', '\\"')
    subprocess.run([
        "osascript", "-e",
        f'display notification "{safe_msg}" with title "{safe_title}" subtitle "{safe_sub}"',
    ], capture_output=True)


def notify(title: str, subtitle: str, message: str, file_path: Path, mode: str):
    """Fire the "review done" notification — clicking opens the .md report."""
    _send_notification(title, subtitle, message, mode, click_target=file_path)


def notify_started(title: str, subtitle: str, mode: str, message=None):
    """Fire a "review started" notification — no click target (the .md doesn't
    exist yet). Used by manual `autoreviewer run` and any non-hook code path.
    The post-commit hook fires its own shell-side notification synchronously
    before forking us (avoiding ~1-2s of Python boot lag), then passes
    --no-start-notify so we don't double-fire.

    `message` defaults to the English string for backwards compatibility; pass
    a localized string from LABELS to honor the configured report language.
    """
    if message is None:
        message = "⏳ Running review in background"
    _send_notification(title, subtitle, message, mode, click_target=None)


# ---------- index ----------

def update_index(index_path: Path, entry: dict):
    data = []
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text())
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []
    data.append(entry)
    index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("commit", nargs="?", default="HEAD")
    ap.add_argument("--sync", action="store_true", help="Print result to stdout when done")
    ap.add_argument("--no-notify", action="store_true",
                    help="Skip BOTH the start and completion notifications")
    ap.add_argument("--no-start-notify", action="store_true",
                    help="Skip only the 'review started' notification (used by "
                         "the post-commit hook, which fires its own start "
                         "notification synchronously to avoid Python boot lag)")
    ap.add_argument("--open", action="store_true", help="Open the .md file when done")
    args = ap.parse_args()

    try:
        repo_root = Path(git("rev-parse", "--show-toplevel"))
        git_dir = Path(git("rev-parse", "--git-dir"))
    except subprocess.CalledProcessError:
        print("[autoreviewer] Not a git repository", file=sys.stderr)
        sys.exit(1)

    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir

    cfg = load_config(repo_root)
    labels = get_labels(cfg.get("language", "zh"))

    # Resolve commit info
    info = get_commit_info(args.commit, repo_root)
    short = info["short_hash"]
    repo_name = repo_root.name  # used in notification titles to disambiguate
                                 # autoreviewer notifications across multiple repos

    reviews_dir = git_dir / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    pid_file = reviews_dir / ".running.pid"
    lock_file = reviews_dir / ".lock"

    # Acquire lock (file-based, simple)
    import fcntl
    lock_fd = open(lock_file, "w")
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"[autoreviewer] Another review is running, waiting...", file=sys.stderr)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

        pid_file.write_text(str(os.getpid()))

        # Build file paths
        date_tag = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        base = f"{date_tag}_{short}"
        json_path = reviews_dir / f"{base}.json"
        md_path = reviews_dir / f"{base}.md"
        raw_path = reviews_dir / f"{base}.raw.txt"

        # Build prompt
        prompt_file = Path(os.path.expanduser(cfg["prompt_file"]))
        if not prompt_file.exists():
            prompt_file = DEFAULT_PROMPT
        prompt_template = prompt_file.read_text()
        diff = get_diff(info["full_hash"], repo_root)
        # Inject the language directive at the very top so it acts as a global
        # rule for the rest of the prompt. Putting it first (rather than last)
        # makes it part of the model's "system context" framing instead of
        # competing with the diff for recency attention.
        full_prompt = (
            f"OUTPUT LANGUAGE:\n- {labels['prompt_directive']}\n\n"
            f"{prompt_template}\n"
            f"--- COMMIT META ---\n"
            f"hash: {info['full_hash']}\n"
            f"message: {info['message']}\n"
            f"author: {info['author']}\n"
            f"date: {info['date']}\n\n"
            f"--- DIFF ---\n{diff}\n"
        )

        # Run command
        cmd = cfg["command"]
        if not shutil.which(cmd):
            print(f"[autoreviewer] Command not found: {cmd}", file=sys.stderr)
            sys.exit(2)

        print(f"[autoreviewer] Reviewing {short}: {info['message']}", file=sys.stderr)

        # "Review started" desktop notification (gated by config + flags).
        # The post-commit hook sets --no-start-notify because it fires its own
        # shell-side notification before forking us (instant, no Python boot lag).
        # Manual `autoreviewer run` doesn't pass that flag, so we fire it here.
        if (not args.no_notify) and (not args.no_start_notify) and cfg.get("notify_start", True):
            notify_started(
                title=f"autoreviewer · {repo_name}",
                subtitle=f"{short} · {info['message'][:75]}",
                mode=cfg.get("notification", "terminal-notifier"),
                message=labels["started_message"],
            )

        t0 = time.time()
        try:
            raw = run_review_command(
                cmd,
                full_prompt,
                cfg["timeout_seconds"],
                extra_args=cfg.get("command_args"),
            )
        except subprocess.TimeoutExpired:
            print(f"[autoreviewer] Review timed out after {cfg['timeout_seconds']}s", file=sys.stderr)
            sys.exit(3)
        elapsed = time.time() - t0
        print(f"[autoreviewer] Command done in {elapsed:.1f}s, parsing...", file=sys.stderr)

        review = None
        errors = []
        try:
            review = extract_json(raw)
        except Exception as e:
            errors.append(f"local parse: {e}")
            print(f"[autoreviewer] Failed to parse JSON locally: {e}", file=sys.stderr)
            print(f"[autoreviewer] Asking model to repair its output...", file=sys.stderr)
            try:
                review = repair_with_llm(
                    cmd, raw, cfg["timeout_seconds"],
                    extra_args=cfg.get("command_args"),
                )
                print(f"[autoreviewer] LLM repair succeeded", file=sys.stderr)
            except subprocess.TimeoutExpired:
                errors.append("LLM repair: timed out")
                print(f"[autoreviewer] LLM repair timed out", file=sys.stderr)
            except Exception as e2:
                errors.append(f"LLM repair: {e2}")
                print(f"[autoreviewer] LLM repair failed: {e2}", file=sys.stderr)

        if review is None:
            # All strategies exhausted — write raw + degraded .md so the user
            # still has something to open.
            raw_path.write_text(raw)
            md_path.write_text(render_failure_markdown(info, errors, raw_path, raw, labels=labels))
            print(f"[autoreviewer] Raw output saved to {raw_path}", file=sys.stderr)
            print(f"[autoreviewer] Failure report: {md_path}", file=sys.stderr)
            if args.open:
                subprocess.Popen(["open", str(md_path)])
            sys.exit(4)

        # Write outputs
        json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2))
        md_path.write_text(render_markdown(review, info, labels=labels))

        # Update index
        severity = (review.get("severity") or "ok").lower()
        issues = review.get("issues", []) or []
        update_index(reviews_dir / "index.json", {
            "hash": short,
            "full_hash": info["full_hash"],
            "message": info["message"],
            "author": info["author"],
            "date": info["date"],
            "severity": severity,
            "issue_count": len(issues),
            "md_path": str(md_path),
            "json_path": str(json_path),
        })

        # Notify
        if not args.no_notify:
            threshold = cfg.get("notify_threshold", "low")
            if SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(threshold, 0):
                emoji = SEVERITY_EMOJI.get(severity, "⚪")
                issue_word = labels["issue_word"] if len(issues) == 1 else labels["issues_word"]
                notify(
                    title=f"autoreviewer · {repo_name}",
                    subtitle=f"{short} · {info['message'][:75]}",
                    message=f"{emoji} {severity.upper()} · {len(issues)} {issue_word} · {labels['click_to_open']}",
                    file_path=md_path,
                    mode=cfg.get("notification", "terminal-notifier"),
                )

        # Auto-open
        auto_open = cfg.get("auto_open", "false")
        should_open = (
            args.open
            or auto_open is True or auto_open == "true"
            or (auto_open == "on_high" and severity == "high")
        )
        if should_open:
            subprocess.Popen(["open", str(md_path)])

        if args.sync:
            print(f"\n✅ Review saved: {md_path}\n")
            print(md_path.read_text())
        else:
            print(f"[autoreviewer] Done: {md_path}", file=sys.stderr)

    finally:
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
