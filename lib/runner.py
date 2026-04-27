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


# ---------- rendering ----------

def render_markdown(review: dict, info: dict) -> str:
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
        out += ["## 总结", summary, ""]

    issues = review.get("issues", []) or []
    if issues:
        out += ["## 问题清单", ""]
        for it in issues:
            lvl = (it.get("level") or "medium").lower()
            emoji = SEVERITY_EMOJI.get(lvl, "⚪")
            file = it.get("file", "?")
            line = it.get("line", "?")
            out.append(f"### {emoji} [{lvl.upper()}] {file}:{line}")
            out.append(it.get("message", ""))
            sug = it.get("suggestion", "")
            if sug:
                out.append(f"**建议**: {sug}")
            out.append("")
    else:
        out += ["## 问题清单", "无", ""]

    impact = review.get("impact", {}) or {}
    if impact:
        out += ["## 影响范围", ""]
        modified = impact.get("modified_files", []) or []
        if modified:
            out.append("**直接修改的文件**:")
            for f in modified:
                out.append(f"- {f}")
            out.append("")
        callers = impact.get("affected_callers", []) or []
        if callers:
            out.append("**可能受影响的调用方**:")
            for c in callers:
                out.append(f"- {c.get('file', '?')}:{c.get('line', '?')} — {c.get('reason', '')}")
            out.append("")
        risk = impact.get("risk_level", "")
        if risk:
            out.append(f"**风险等级**: {risk}")
            out.append("")

    suggestions = review.get("suggestions", []) or []
    if suggestions:
        out += ["## 改进建议", ""]
        for s in suggestions:
            out.append(f"- {s}")
        out.append("")

    return "\n".join(out)


# ---------- notifications ----------

def notify(title: str, subtitle: str, message: str, file_path: Path, mode: str):
    if mode == "none":
        return
    if mode == "terminal-notifier" and shutil.which("terminal-notifier"):
        # IMPORTANT: do NOT pass -sender. macOS routes click events to the
        # sender app instead of running -execute, breaking click-to-open.
        # The notification will show terminal-notifier's icon/name, which is fine.
        subprocess.run([
            "terminal-notifier",
            "-title", title,
            "-subtitle", subtitle,
            "-message", message,
            "-execute", f"open {str(file_path)!r}",
        ], capture_output=True)
        return
    # Fallback: osascript (not clickable)
    safe_msg = message.replace('"', '\\"')
    safe_title = title.replace('"', '\\"')
    safe_sub = subtitle.replace('"', '\\"')
    subprocess.run([
        "osascript", "-e",
        f'display notification "{safe_msg}" with title "{safe_title}" subtitle "{safe_sub}"',
    ], capture_output=True)


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
    ap.add_argument("--no-notify", action="store_true")
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

    # Resolve commit info
    info = get_commit_info(args.commit, repo_root)
    short = info["short_hash"]

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
        full_prompt = (
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

        try:
            review = extract_json(raw)
        except Exception as e:
            raw_path.write_text(raw)
            print(f"[autoreviewer] Failed to parse JSON: {e}", file=sys.stderr)
            print(f"[autoreviewer] Raw output saved to {raw_path}", file=sys.stderr)
            sys.exit(4)

        # Write outputs
        json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2))
        md_path.write_text(render_markdown(review, info))

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
                notify(
                    title=f"Review · {short} · {emoji} {severity.upper()}",
                    subtitle=info["message"][:80],
                    message=f"{len(issues)} issues · click to view",
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
