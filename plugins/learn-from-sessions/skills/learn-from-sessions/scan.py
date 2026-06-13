#!/usr/bin/env python3
"""Scan Claude Code session logs and emit a token-efficient digest.

Self-contained port of headroom's learn scanner + digest builder
(headroom/learn/plugins/claude.py + headroom/learn/analyzer._build_digest),
minus the LLM call and the file writer. The *running* Claude agent is the
analyzer: it reads this digest and proposes rules for CLAUDE.md / MEMORY.md.

Usage:
    python3 scan.py                      # current working directory's project
    python3 scan.py --project /path      # a specific project
    python3 scan.py --list               # list discovered projects
    python3 scan.py --full               # ignore incremental state, scan all
    python3 scan.py --commit             # mark last scan's sessions analyzed
    python3 scan.py --max-tokens 60000   # override digest budget (default 80k)

Reads JSONL from ~/.claude/projects/<encoded-path>/. No dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
MAX_DIGEST_TOKENS = 80_000  # leave room for the agent's own reasoning + output

# --- Error classification (ordered; first match wins) -----------------------
_ERROR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"No such file or directory|ENOENT|FileNotFoundError|does not exist", re.I), "file_not_found"),
    (re.compile(r"ModuleNotFoundError|ImportError|No module named", re.I), "module_not_found"),
    (re.compile(r"command not found", re.I), "command_not_found"),
    (re.compile(r"Permission denied|EACCES|EPERM|auto-denied", re.I), "permission_denied"),
    (re.compile(r"file is too large|too many lines|exceeds.*limit", re.I), "file_too_large"),
    (re.compile(r"EISDIR|Is a directory", re.I), "is_directory"),
    (re.compile(r"SyntaxError|IndentationError", re.I), "syntax_error"),
    (re.compile(r"Traceback \(most recent|Exception:|Error:", re.I), "runtime_error"),
    (re.compile(r"timed? ?out|TimeoutError|deadline exceeded", re.I), "timeout"),
    (re.compile(r"No (?:matches|files|results) found|0 matches", re.I), "no_matches"),
    (re.compile(r"user.*reject|user.*denied|declined|didn't want to proceed", re.I), "user_rejected"),
    (re.compile(r"[Ss]ibling tool call errored", re.I), "sibling_error"),
    (re.compile(r"exit code|non-zero|exited with", re.I), "exit_code"),
    (re.compile(r"ConnectionError|ConnectionRefused|ECONNREFUSED|network", re.I), "connection_error"),
    (re.compile(r"BUILD FAILED|compilation error|compile error", re.I), "build_failure"),
]
_ERROR_INDICATORS = (
    "Error:", "error:", "ENOENT", "No such file", "command not found",
    "Permission denied", "ModuleNotFoundError", "Traceback (most recent",
    "FAILED", "EISDIR", "auto-denied", "Sibling tool call errored",
    "timed out", "exit code", "FileNotFoundError",
)


def is_error_content(content: str) -> bool:
    if not content or len(content) < 10:
        return False
    snippet = content[:1000]
    return any(ind in snippet for ind in _ERROR_INDICATORS)


def classify_error(content: str) -> str:
    for pattern, category in _ERROR_PATTERNS:
        if pattern.search(content[:2000]):
            return category
    return "unknown"


# --- Path encoding ----------------------------------------------------------
def encode_project_dir(path: Path) -> str:
    """Encode a project path the way Claude Code names its log directory.

    Claude replaces every non-alphanumeric run with '-' and prefixes the
    result, e.g. /private/tmp -> -private-tmp. We encode the *known* target
    path and look the directory up directly, avoiding ambiguous reverse decode.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", str(path))


def decode_project_dir(name: str) -> str:
    """Best-effort human label for a log directory name (display only, lossy)."""
    return "/" + name[1:].replace("-", "/") if name.startswith("-") else name


def context_file_for(project: Path) -> Path:
    """The CLAUDE.md the agent should read/write — mirrors headroom's writer.

    For the home directory, Claude Code reads ~/.claude/CLAUDE.md, not ~/CLAUDE.md.
    """
    if project == Path.home():
        return Path.home() / ".claude" / "CLAUDE.md"
    return project / "CLAUDE.md"


# --- Scanning ---------------------------------------------------------------
def scan_session(jsonl_path: Path) -> dict | None:
    """Parse one JSONL session into normalized events + tool calls."""
    tool_uses: dict[str, tuple[str, dict]] = {}
    events: list[dict] = []
    tool_calls: list[dict] = []
    tokens_in = tokens_out = 0
    idx = 0
    bad_lines = 0
    first_ts: str | None = None

    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                idx += 1
                if first_ts is None:
                    first_ts = d.get("timestamp")
                ltype = d.get("type", "")
                if ltype == "assistant":
                    msg = d.get("message", {})
                    for block in msg.get("content", []) or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tid, name = block.get("id", ""), block.get("name", "")
                            inp = block.get("input", {})
                            if tid and name:
                                tool_uses[tid] = (name, inp if isinstance(inp, dict) else {})
                    usage = msg.get("usage", {})
                    tokens_in += usage.get("input_tokens", 0)
                    tokens_in += usage.get("cache_read_input_tokens", 0)
                    tokens_in += usage.get("cache_creation_input_tokens", 0)
                    tokens_out += usage.get("output_tokens", 0)
                elif ltype == "user":
                    msg = d.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_result":
                                tid = block.get("tool_use_id", "")
                                if tid not in tool_uses:
                                    continue
                                out = block.get("content", "")
                                if not isinstance(out, str):
                                    out = str(out)
                                name, inp = tool_uses[tid]
                                is_err = bool(block.get("is_error", False)) or is_error_content(out)
                                tc = {
                                    "name": name, "input": inp, "output": out,
                                    "is_error": is_err,
                                    "error_category": classify_error(out) if is_err else "",
                                    "idx": idx, "bytes": len(out.encode("utf-8")),
                                }
                                tool_calls.append(tc)
                                events.append({"type": "tool_call", "idx": idx, "tc": tc})
                                # Subagent (Agent tool) summary — token-waste signal.
                                if name in ("Agent", "agent"):
                                    meta = d.get("toolUseResult", {})
                                    if isinstance(meta, dict):
                                        events.append({
                                            "type": "agent_summary", "idx": idx,
                                            "tool_count": meta.get("totalToolUseCount", 0),
                                            "tokens": meta.get("totalTokens", 0),
                                            "duration_ms": meta.get("totalDurationMs", 0),
                                            "prompt": str(meta.get("prompt", ""))[:200],
                                        })
                            elif block.get("type") == "text":
                                txt = block.get("text", "")
                                if "[Request interrupted by user" in txt:
                                    events.append({"type": "interruption", "idx": idx, "text": txt[:200]})
                    elif isinstance(content, str) and content.strip():
                        events.append({"type": "user_message", "idx": idx, "text": content[:500]})
    except (OSError, UnicodeDecodeError):
        return None

    if not tool_calls:
        return None
    events.sort(key=lambda e: e["idx"])
    return {
        "id": jsonl_path.stem,
        "tool_calls": tool_calls,
        "events": events,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "failures": sum(1 for tc in tool_calls if tc["is_error"]),
        "bad_lines": bad_lines,
        "first_ts": first_ts or "",
    }


def input_summary(name: str, inp: dict) -> str:
    if name in ("Bash", "bash"):
        cmd = inp.get("command", "")
        return cmd[:100] + "..." if len(cmd) > 100 else cmd
    for key in ("file_path", "pattern"):
        if key in inp:
            return str(inp[key])
    return str(inp)[:80]


def format_tool_call(tc: dict) -> str:
    inp = input_summary(tc["name"], tc["input"])[:120]
    if tc["is_error"]:
        prev = tc["output"][:200].replace("\n", " ").strip()
        return f"  [{tc['idx']}] {tc['name']}: {inp} -> ERROR({tc['error_category']}): {prev}"
    size = f"({tc['bytes']} bytes)" if tc["bytes"] else ""
    return f"  [{tc['idx']}] {tc['name']}: {inp} -> OK {size}"


def format_event(ev: dict) -> str | None:
    if ev["type"] == "tool_call":
        return format_tool_call(ev["tc"])
    if ev["type"] == "user_message" and ev["text"].strip():
        return f'  [{ev["idx"]}] USER: "{ev["text"].strip()[:300]}"'
    if ev["type"] == "interruption":
        return f"  [{ev['idx']}] INTERRUPTED: {ev['text'][:150]}"
    if ev["type"] == "agent_summary":
        return (
            f"  [{ev['idx']}] SUBAGENT: {ev['tool_count']} tool calls, "
            f"{ev['tokens']:,} tokens, {ev['duration_ms'] / 1000:.1f}s "
            f'-> prompt: "{ev["prompt"][:100]}"'
        )
    return None


def build_digest(label: str, project_path: str, sessions: list[dict], max_tokens: int) -> str:
    lines: list[str] = [f"Project: {label} ({project_path})"]
    total_calls = sum(len(s["tool_calls"]) for s in sessions)
    total_fail = sum(s["failures"] for s in sessions)
    tin = sum(s["tokens_in"] for s in sessions)
    tout = sum(s["tokens_out"] for s in sessions)
    rate = f" ({total_fail / total_calls:.1%})" if total_calls else ""
    lines.append(f"Total: {len(sessions)} sessions, {total_calls} tool calls, {total_fail} failures{rate}")
    if tin:
        lines.append(f"Tokens used: {tin:,} in / {tout:,} out  (input includes cache reads; inflated)")
    lines.append("")

    char_budget = max_tokens * 4  # ~4 chars/token
    used = sum(len(x) for x in lines)
    for i, s in enumerate(sessions):
        if used > char_budget:
            lines.append(f"... (remaining {len(sessions) - i} sessions truncated)")
            break
        header = f"=== Session {s['id'][:12]} ({len(s['tool_calls'])} calls, {s['failures']} failures"
        if s["tokens_in"]:
            header += f", {s['tokens_in']:,} input tokens"
        header += ") ==="
        lines.append(header)
        used += len(header)
        for ev in s["events"]:
            if used > char_budget:
                lines.append("  ... (remaining events truncated)")
                break
            line = format_event(ev)
            if line:
                lines.append(line)
                used += len(line)
        lines.append("")
    return "\n".join(lines)


# --- Incremental state -------------------------------------------------------
# A session is "analyzed" once its learnings have been committed. We key on
# (size, mtime_ns): a session that grows OR is rewritten in place is re-analyzed.
# The learned baseline still lives in CLAUDE.md / MEMORY.md, so accumulated rules
# persist even though old sessions are skipped from the digest.
def state_paths(data_dir: Path) -> tuple[Path, Path]:
    mem = data_dir / "memory"
    return mem / ".learn-state.json", mem / ".learn-pending.json"


def load_analyzed(state_path: Path) -> dict[str, list]:
    try:
        return json.loads(state_path.read_text()).get("analyzed", {})
    except (OSError, json.JSONDecodeError):
        return {}


def commit(data_dir: Path, project: Path) -> int:
    """Fold the last scan's pending sessions into the analyzed state."""
    state_path, pending_path = state_paths(data_dir)
    try:
        pending = json.loads(pending_path.read_text())
    except (OSError, json.JSONDecodeError):
        print("Nothing to commit (run a scan first).", file=sys.stderr)
        return 1
    if pending.get("project") != str(project):
        print(
            f"Pending state is for {pending.get('project')!r}, not {str(project)!r}. "
            "Re-scan this project before committing.",
            file=sys.stderr,
        )
        return 1
    analyzed = load_analyzed(state_path)
    analyzed.update(pending.get("sessions", {}))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"analyzed": analyzed}, indent=2))
    try:
        pending_path.unlink()
    except OSError:
        pass
    print(f"Committed {len(pending.get('sessions', {}))} session(s). "
          f"{len(analyzed)} now tracked as analyzed.")
    return 0


def load_sessions(data_dir: Path, analyzed: dict[str, list], full: bool) -> tuple[list[dict], int]:
    out: list[dict] = []
    skipped = 0
    files = list(data_dir.glob("*.jsonl"))
    # Roughly chronological: order sessions by last-modified time.
    for f in sorted(files, key=lambda p: p.stat().st_mtime):
        st = f.stat()
        key = [st.st_size, st.st_mtime_ns]
        if not full and analyzed.get(f.stem) == key:
            skipped += 1
            continue
        s = scan_session(f)
        if s:
            s["key"] = key
            out.append(s)
    return out, skipped


def list_projects() -> None:
    if not PROJECTS_DIR.exists():
        print(f"No projects directory at {PROJECTS_DIR}", file=sys.stderr)
        return
    rows = []
    for entry in sorted(PROJECTS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        n = len(list(entry.glob("*.jsonl")))
        if n:
            rows.append((n, decode_project_dir(entry.name)))
    if not rows:
        print("No projects with session logs found.", file=sys.stderr)
        return
    print("Paths below are decoded best-effort (lossy); pass the real path to --project.\n")
    print(f"{'sessions':>8}  project")
    for n, label in rows:
        print(f"{n:>8}  {label}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, default=None, help="Project dir (default: cwd)")
    ap.add_argument("--list", action="store_true", help="List discovered projects and exit")
    ap.add_argument("--max-tokens", type=int, default=MAX_DIGEST_TOKENS)
    ap.add_argument("--full", action="store_true", help="Re-analyze all sessions, ignoring prior state")
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Mark the sessions from the last scan as analyzed (call after the user approves)",
    )
    args = ap.parse_args()

    if args.list:
        list_projects()
        return 0

    project = (args.project or Path.cwd()).resolve()
    enc = encode_project_dir(project)
    data_dir = PROJECTS_DIR / enc
    if not data_dir.exists():
        print(f"No Claude Code logs for {project}\n(looked in {data_dir})", file=sys.stderr)
        print("Run with --list to see available projects.", file=sys.stderr)
        return 1

    state_path, pending_path = state_paths(data_dir)
    if args.commit:
        return commit(data_dir, project)

    analyzed = load_analyzed(state_path)
    sessions, skipped = load_sessions(data_dir, analyzed, args.full)
    if not sessions:
        if skipped:
            print(
                f"All {skipped} session(s) already analyzed — nothing new. "
                "Use --full to re-analyze everything.",
                file=sys.stderr,
            )
            return 0
        print(f"No sessions with tool calls found in {data_dir}", file=sys.stderr)
        return 1

    # Record exactly what this scan covered so a later --commit marks these.
    # Clear any stale pending first, and fail loudly if we cannot persist it.
    try:
        pending_path.unlink()
    except OSError:
        pass
    try:
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(json.dumps({
            "project": str(project),
            "full": args.full,
            "sessions": {s["id"]: s["key"] for s in sessions},
        }))
    except OSError as e:
        print(f"Could not write pending state ({e}); --commit will be unavailable.", file=sys.stderr)

    claude_md = context_file_for(project)
    memory_md = data_dir / "memory" / "MEMORY.md"
    bad = sum(s["bad_lines"] for s in sessions)

    print("=== Target files (read these for the existing baseline before proposing) ===")
    print(f"CLAUDE.md : {claude_md}  ({'exists' if claude_md.exists() else 'absent'})")
    print(f"MEMORY.md : {memory_md}  ({'exists' if memory_md.exists() else 'absent'})")
    if skipped:
        print(f"Incremental: {len(sessions)} new/changed session(s); {skipped} already analyzed (skipped). --full to include all.")
    if bad:
        print(f"Note: skipped {bad} malformed JSONL line(s) across sessions — digest may be partial.")
    print()
    print(build_digest(project.name or enc, str(project), sessions, args.max_tokens))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
