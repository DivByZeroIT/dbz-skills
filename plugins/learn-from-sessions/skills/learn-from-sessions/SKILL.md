---
name: learn-from-sessions
description: Mine past Claude Code session logs for recurring failures, the workarounds that resolved them, and user corrections — then propose concrete rules to add to CLAUDE.md / MEMORY.md. Use when the user wants to "learn from my sessions/history/logs", reduce repeated mistakes, or auto-improve their project context files. Claude Code only; reads ~/.claude/projects logs locally.
---

# Learn from sessions

Offline learning loop: parse this project's Claude Code logs into a digest, read
the trajectory (failures **and how they were resolved**), and propose rules that
would prevent the waste next time. You — the running agent — are the analyzer;
nothing calls an external LLM. You **propose**, the user approves, then you write.

## Workflow

### 1. Build the digest
The scanner `scan.py` ships **next to this SKILL.md**. Resolve its path once —
prefer the plugin root if set, else locate it (works for both plugin and
personal-skill installs):

```bash
SCAN="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/learn-from-sessions/scan.py}"
[ -f "$SCAN" ] || SCAN=$(find "$HOME/.claude/plugins" "$HOME/.claude/skills" -name scan.py -path '*learn-from-sessions*' 2>/dev/null | head -1)
python3 "$SCAN"            # scans the current working directory's project
```

Reuse `$SCAN` for the calls below.

- Other project: `python3 "$SCAN" --project /path/to/repo`
- Don't know what's available: `python3 "$SCAN" --list`
- Output too big / want to focus: `--max-tokens 40000`
- Re-analyze everything (ignore incremental state): `--full`

**Incremental by default:** the scanner skips sessions already analyzed in a
previous run (keyed by file size, so a session that later grew is re-analyzed).
A session is "analyzed" only once you commit it in Step 5 — so re-running before
approving re-shows the same new sessions. The accumulated rules still live in
CLAUDE.md / MEMORY.md, which you read in Step 2, so skipping old sessions does
not lose past learnings. If the header says everything is already analyzed,
there's nothing new — stop and tell the user (or offer `--full`).

The script prints a header naming the **target files** (the project `CLAUDE.md`
and the per-project `MEMORY.md`), then a digest of every session. Sessions are
ordered roughly by time (last-modified); events **within** a session are in
strict order. Each line is a tool call as `-> OK` / `-> ERROR(category)`, or a
`USER:`, `INTERRUPTED:`, or `SUBAGENT:` line.

### 2. Read the baseline
Read the two target files named in the header (skip any marked `absent`). Treat
their existing content as the starting point so you refine and dedupe rather than
re-propose what's there. Recognize **both** marker styles as prior learnings:
this skill's `<!-- learn-from-sessions:start -->` block and, if the project was
ever processed by upstream headroom, its `<!-- headroom:learn:start -->` block —
preserve/merge into them rather than duplicating their rules.

### 3. Analyze the trajectory — not just the errors
The digest is ordered. For each problem, **read forward to find the resolution** —
that's where the rule comes from:

- A failure followed by a later **`-> OK`** doing the same thing differently
  ⇒ the rule is the working form. *e.g. `python3 x.py -> ERROR(command_not_found)`
  then `uv run python x.py -> OK`* ⇒ "Use `uv run python`, not bare `python3`."
- A failure or action followed by a **`USER:`** correction or an `INTERRUPTED:`
  ⇒ encode the user's stated preference.
- A repeated wrong path / re-read of the same large file, later done right
  ⇒ record the correct path or the known-large file.
- A search that returns `no_matches` then succeeds with a different scope
  ⇒ record the right scope.

Rules for what to propose (mirrors the original headroom rubric):
- **Evidence required**: 2+ occurrences, OR one explicit user direction. Skip
  one-off transient errors (flaky network, a typo fixed immediately).
- **Actionable and specific**: "use X instead of Y", not "be careful".
- **No tautologies** (don't propose "use python3 not python3").
- Estimate **tokens saved per session** and note the **evidence count** so the
  user can prioritize.
- **Split by destination**:
  - **CLAUDE.md** (`CONTEXT_FILE`) — stable, project-level facts: environment
    commands, correct paths, known-large files, build/test invocations.
  - **MEMORY.md** (`MEMORY_FILE`) — evolving, session-level preferences: things
    the user corrected, rejected, or asked for.

### 4. Propose to the user
Present a compact table grouped by destination. Do **not** write yet. For each
candidate show: target file · section · the rule (1–3 lines) · evidence count ·
est. tokens/session saved · the digest line(s) that justify it. Then ask which to
apply (all / a subset / none). If nothing clears the evidence bar, say so plainly.

### 5. Write what's approved
Two destinations, two different conventions — keep them separate.

**CLAUDE.md** — stable project facts. Append/update inside a clearly marked,
regenerable block so the edits are reversible and a re-run updates in place
rather than duplicating:

```markdown
<!-- learn-from-sessions:start -->
## Learned from sessions (YYYY-MM-DD)
### <section>
<rule>
<!-- learn-from-sessions:end -->
```

- If the block exists, replace matching sections and carry forward untouched ones
  (never silently drop prior learnings).
- CLAUDE.md → project root (or `~/.claude/CLAUDE.md` if the project *is* home).

**MEMORY.md** — evolving preferences. Here `MEMORY.md` is an **index**, not a
container; do **not** write the learning into `MEMORY.md` itself. For each
approved item, in the memory dir shown in the scanner header
(`~/.claude/projects/<encoded>/memory/`; create it if needed):

- Write the fact to its **own file** `<slug>.md` with frontmatter (`name`,
  `description`, `metadata.type: feedback | project`). For feedback/project
  facts, follow the body with **Why:** and **How to apply:** lines.
- Add a **one-line pointer** to `MEMORY.md`: `- [Title](<slug>.md) — hook`.
  One line per memory — never put the fact's content in the index.
- Before creating, check for an existing file that already covers it and update
  that instead of creating a duplicate.

- Use Read-before-Edit; show the diff you made.

**Then mark these sessions as analyzed** so the next run skips them:

```bash
python3 "$SCAN" --commit
```

Only commit after the user has reviewed (whether they approved rules or chose
"none" — both mean the sessions were considered). If the user aborted without
reviewing, do **not** commit, so the sessions resurface next time. Pass the same
`--project` you scanned with if it wasn't the cwd.

## Notes
- Pure local analysis — no API keys, no network, no subprocess LLM. The scanner
  only reads `~/.claude/projects/*.jsonl`.
- Faithful to headroom's `learn` pipeline (scan → digest → analyze → write) with
  the LLM step replaced by you and the auto-writer replaced by propose-then-confirm.
- Scope: Claude Code only. Codex/Gemini logs are not parsed.
