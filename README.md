# learn-from-sessions

A small, focused [Claude Code](https://www.claude.com/product/claude-code) skill
that turns your past sessions into better context. It scans your local Claude Code
logs, finds **recurring failures and the fixes that resolved them** (plus the
corrections you gave the agent), and **proposes** concrete rules to add to your
`CLAUDE.md` / `MEMORY.md` — so the same mistakes stop wasting tokens.

It never auto-edits anything: it shows you the proposed learnings and asks for
approval before writing.

> **Inspired by [headroom](https://github.com/chopratejas/headroom).** Headroom is
> a broad context-optimization toolkit (a compression proxy, MCP tools, cross-agent
> memory, and an offline `learn` command). This project deliberately does **one
> thing**: it extracts only the "learn from past sessions" idea and makes it lean —
> a single skill, no proxy, no API key, no background services. If you want the
> full platform, use headroom. If you just want session-driven `CLAUDE.md`
> suggestions with zero infrastructure, use this.

## How it differs from headroom's `learn`

| | headroom `learn` | learn-from-sessions |
|---|---|---|
| Footprint | Python package + LiteLLM + optional proxy | one skill (`SKILL.md` + a ~300-line `scan.py`) |
| The analysis LLM | external model via API key or a CLI subprocess | **the Claude agent you're already talking to** |
| Writing | auto-writes marker blocks into your files | **proposes, you approve, then it writes** |
| Scope | Claude Code, Codex, Gemini | Claude Code only |
| Network / keys | needs an LLM backend | none — fully local |

The deterministic half is a faithful port of headroom's scanner + digest builder
(`headroom/learn/plugins/claude.py` + `analyzer._build_digest`): parse the JSONL
logs in `~/.claude/projects/`, normalize and error-classify every tool call, and
build a token-budgeted digest of the chronological event stream (tool calls,
`USER:` messages, interruptions, subagent summaries). The LLM step and the
auto-writer are what this project intentionally drops.

## Install

```text
/plugin marketplace add DivByZeroIT/learn-from-sessions
/plugin install learn-from-sessions@dbz-skills
```

Then, from any project:

```text
/learn-from-sessions
```

Or just ask: *"learn from my sessions in this project."*

### Manual install (no plugin)

Copy the skill into your personal skills directory:

```bash
git clone https://github.com/DivByZeroIT/learn-from-sessions
cp -r learn-from-sessions/plugins/learn-from-sessions/skills/learn-from-sessions \
  ~/.claude/skills/learn-from-sessions
```

## How it works

1. **Scan** — `scan.py` reads the current project's logs from
   `~/.claude/projects/<encoded-path>/*.jsonl` and emits a compact digest
   (80k-token budget) of the session event stream.
2. **Read baseline** — the agent reads your existing `CLAUDE.md` / `MEMORY.md`
   (and any prior learned block) so it refines instead of duplicating.
3. **Analyze the trajectory** — for each repeated failure the agent reads *forward*
   to the resolution (the later `-> OK` done differently, or your `USER:`
   correction) and turns that into an actionable "use X instead of Y" rule.
4. **Propose** — it shows a table grouped by destination (stable facts →
   `CLAUDE.md`, evolving preferences → `MEMORY.md`) with evidence counts and
   estimated tokens saved. Nothing is written yet.
5. **Apply** — only the rules you approve are written, inside a reversible
   `<!-- learn-from-sessions:start -->` … `<!-- learn-from-sessions:end -->` block.

### Incremental by default

Re-runs only analyze **new or changed** sessions. A session counts as "analyzed"
only after you approve and it's committed (`scan.py --commit`), so aborting before
review re-surfaces the same sessions next time. Use `--full` to re-analyze
everything. Accumulated rules live in your `CLAUDE.md` / `MEMORY.md`, so skipping
old sessions never loses past learnings.

## Privacy

Everything runs locally. `scan.py` only reads `~/.claude/projects/*.jsonl`, makes
no network calls, and starts no subprocess LLM. The only "model" involved is the
Claude Code agent you are already using.

## Scanner reference

```text
scan.py                  # digest for the current working directory's project
scan.py --project /path  # a specific project
scan.py --list           # list discovered projects
scan.py --full           # ignore incremental state, scan all sessions
scan.py --commit         # mark the last scan's sessions as analyzed
scan.py --max-tokens N   # digest budget (default 80000)
```

## Credits

- Built on the ideas in **[headroom](https://github.com/chopratejas/headroom)** by
  Tejas Chopra — specifically its offline failure-learning pipeline. Headroom is a
  separate, more comprehensive project; please check it out if you want compression,
  MCP tools, and multi-agent support.
- This is an independent reimplementation of just the log-mining idea, not a fork.

## License

[MIT](./LICENSE) © 2026 Massimo Chieruzzi
