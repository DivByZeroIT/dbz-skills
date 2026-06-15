# learn-from-sessions

A small, focused [Claude Code](https://www.claude.com/product/claude-code) skill
that turns your past sessions into better context. It scans your local Claude Code
logs, finds **recurring failures and the fixes that resolved them** (plus the
corrections you gave the agent), and **proposes** concrete rules to add to your
`CLAUDE.md` / `MEMORY.md` — so the same mistakes stop wasting tokens.

The failure-learning idea comes straight from **[headroom](https://github.com/chopratejas/headroom)**
by Tejas Chopra; this skill distills just that one piece into a zero-infrastructure
form. Big thanks to that project — go use it if you want the full platform.

It never auto-edits anything: it shows you the proposed learnings and asks for
approval before writing.

> ℹ️ This repo is the **`dbz-skills`** Claude Code plugin marketplace. Its first
> (and currently only) plugin is **`learn-from-sessions`**, documented below.

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
/plugin marketplace add DivByZeroIT/dbz-skills
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
git clone https://github.com/DivByZeroIT/dbz-skills
cp -r dbz-skills/plugins/learn-from-sessions/skills/learn-from-sessions \
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

## Example run

Here is a full `/learn-from-sessions` on a small TypeScript project, end to end.

**1. The scanner digests your sessions** (abbreviated — repeated failures and your
corrections are what it keys on):

```text
SESSION 2026-06-10 14:02  (project: acme-web)
  bash: npm test                  -> ERR  "Unknown command: test"
  bash: npm run test              -> ERR  exit 1, "vitest: not found"
  bash: pnpm vitest run           -> OK
  USER: "we use pnpm here, not npm"
  read: src/generated/schema.ts   -> 38.0k tokens
  read: src/generated/schema.ts   -> 38.1k tokens   (read in full again)
  bash: python parse.py           -> ERR  "command not found: python"
  bash: python3 parse.py          -> OK
```

**2. It proposes — nothing is written yet.** You see a table grouped by
destination, each row with its evidence count and the digest line that justifies
it, and you pick which to apply:

| Destination | Rule it suggests | Evidence | ~Tokens/session |
|---|---|---|---|
| `CLAUDE.md` · Commands | Run tests with `pnpm vitest run`; `npm test` / `npm run test` aren't wired up | 2 sessions | ~600 |
| `CLAUDE.md` · Environment | Use `python3` — there is no `python` on `PATH` | 3 sessions | ~250 |
| `CLAUDE.md` · Known-large files | `src/generated/schema.ts` is ~38k tokens — `grep`/`head` it, don't read in full | 4 reads | ~38k |
| `MEMORY.md` · Preferences | User requires `pnpm` over `npm` for all package ops | 1 explicit correction | — |

**3. You approve a subset; it writes them** — each destination in its own
convention. Stable project facts go into a reversible block in `CLAUDE.md`:

```markdown
<!-- learn-from-sessions:start -->
## Learned from sessions (2026-06-10)
### Commands
- Run tests with `pnpm vitest run`; `npm test` is not configured.
### Known-large files
- `src/generated/schema.ts` (~38k tokens) — grep or head it; avoid full reads.
<!-- learn-from-sessions:end -->
```

The evolving preference goes into your memory **index** — a one-line pointer in
`MEMORY.md`…

```markdown
- [Use pnpm, not npm](use-pnpm-not-npm.md) — user-enforced package manager
```

…backed by its own file `memory/use-pnpm-not-npm.md`:

```markdown
---
name: use-pnpm-not-npm
description: User requires pnpm for all package operations in acme-web
metadata:
  type: feedback
---
Always use `pnpm` for installs, scripts, and test runs — never `npm`.
**Why:** the user corrected this explicitly ("we use pnpm here, not npm").
**How to apply:** swap `npm …` → `pnpm …`; tests are `pnpm vitest run`.
```

Next run, those reads and retries don't happen — the agent already knows.

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
