# Agentic Memory in Other Harnesses — Comparison to nooa.memory

## Scope & method

This document compares how popular **coding-agent harnesses** persist and recall knowledge across sessions, and positions the `nooa.memory` subsystem against them. The harnesses surveyed are drawn from the custom-harness landscape: **Claude Code, OpenAI Codex, Gemini CLI, Cursor, GitHub Copilot coding agent, Cline, Aider, Goose (Block), OpenHands, Hermes Agent (Nous Research), Devin (Cognition),** and the **Letta (MemGPT) / Mem0 / Zep** research lineage (a runtime plus drop-in memory layers rather than mainstream coding CLIs).

**Method.** Findings come from public sources only — official documentation, changelogs, engineering blogs, and source repositories — with per-harness citations preserved in the [Harness-by-harness](#harness-by-harness) section and the [References](#references). Each harness was assessed along the same dimensions: memory mechanism, persistence scope, who writes the memory, memory types covered, retrieval method, consolidation/forgetting, and external-layer integration.

**Date and uncertainty.** This snapshot reflects sources current as of **2026-06-21**. Several features are in active flux: Copilot Memory and Cursor Memories are in public preview/beta; Gemini CLI Auto Memory and the OpenHands V0→V1 transition are experimental or partially rolled out; Hermes Agent's "Curator" consolidation mechanics are under-documented officially (some cadence numbers come from third-party write-ups). Items that could not be confirmed from primary sources are flagged inline as *unconfirmed* or *partially confirmed*. Internal implementation details of proprietary systems (Devin Knowledge backend, Copilot Memory storage, Cursor Memories storage) are not publicly disclosed and are treated as such. This is a landscape comparison, not a benchmark; performance numbers cited are vendor self-reported unless otherwise noted.

## TL;DR

- **Most coding harnesses today = instruction-file memory + conversation compaction.** `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` / `.clinerules` / `CONVENTIONS.md` / `.goosehints` / microagents, loaded always-in-context (or path/keyword-triggered), plus an LLM condenser that summarizes-and-replaces history when the window fills.
- **A newer tier adds agent/harness-extracted long-term facts** — Claude Code auto-memory, Codex Memories, Cursor Memories, Copilot Memory, Gemini Auto Memory — but retrieval stays **keyword/grep or always-in-context, not embedding-ranked** (Cursor's vectors index code, not memory; Copilot uses live-citation validation, not disclosed embeddings), and **consolidation/forgetting is shallow** (manual curation, or a flat unused-TTL like Codex's ~30 days / Copilot's 28 days).
- **`nooa.memory` is research-grade:** typed, agent-authored records (info / skill / episode / intent / reflection / scratch) with **hybrid dense+sparse retrieval, ACT-R-style scoring, multi-hop graph spread, dream-based consolidation, and Ebbinghaus forgetting** — delivered as an **opt-in subsystem installed onto an existing agent with zero core edits**.
- **The landscape overlaps with ours more than a single headline suggests.** Only **Hermes, Devin, and the Letta/Mem0/Zep family** approach retrieval-based, typed, actively-consolidated long-term memory — and Letta/Mem0/Zep already implement the full pattern (Letta as a runtime, Mem0/Zep as drop-in layers).
- **Honest caveats:** those harnesses beat ours on scale-hardening, plain-file transparency/version control, IDE/org governance, zero-dependency recall, and (for Mem0/Zep) proven contradiction/temporal handling. Our published gains are from controlled evals (a real trade-off: dreaming aids synthesis but hurts pinpoint lookup), not fleet-scale production data.

## Comparison table

| Harness | Memory mechanism | Persistence scope | Who writes | Types (sem / proc / epis / pref) | Retrieval (in-context vs vector/graph) | Consolidation / forgetting | External layers |
|---|---|---|---|---|---|---|---|
| **Claude Code** | `CLAUDE.md` instruction files + self-written "auto memory" (`MEMORY.md` + topic files) + session transcripts + compaction; API Memory tool (filesystem, beta) | Org / user / project / local; auto-memory per-repo, machine-local; sessions across-session | User (CLAUDE.md) + agent (auto memory) + harness (compaction summaries) | sem (file) / proc (strong) / epis (transcripts+summaries) / pref (file+auto) | Always-in-context files + path-triggered lazy load; **no native vector/semantic recall** | Micro/auto/manual compaction; auto-memory self-curation; no native decay/TTL (API tool *suggests* caps) | Mem0/Zep only via third-party MCP |
| **OpenAI Codex** | `AGENTS.md` + auto-generated "Memories" (`~/.codex/memories/`) + session JSONL + compaction | AGENTS.md project+global; Memories cross-session/cross-project but per-user local; cloud tasks ephemeral (12h cache) | User (AGENTS.md) + agent/harness (Memories, auto-extracted; users can't hand-author entries) + harness (compaction) | sem (partial) / proc / epis (summarized) / pref | Always-in-context injection; deeper detail via **grep/keyword** in `MEMORY.md`; **no embeddings** | Async consolidation (~6h idle); ~30-day prune of unused; server-side encrypted compaction; no structured conflict resolution | Mem0/Vectorize/etc. via MCP only |
| **Gemini CLI** | `GEMINI.md` tiered files + `save_memory`/direct edits + checkpoints + experimental Auto Memory (drafts patches + `SKILL.md`) | Global / project / subdir / private (`MEMORY.md`); local-only; "org" = committed file | User (hand-edit) + agent (`save_memory`/edits) + harness (Auto Memory, opt-in, human-gated via `/memory inbox`) | sem / proc (incl. extracted `SKILL.md`) / epis (partial, restore-only) / pref | Always-in-context concat + JIT locality load; **no native semantic recall** | Conversation compaction; Auto Memory drafts diffs (gated); **no decay / contradiction handling** | Mem0, Zep/Graphiti, Neo4j via MCP |
| **Cursor** | Rules (`.cursor/rules/*.mdc`, `AGENTS.md`) + Memories (auto-extracted, beta) + codebase vector index (Turbopuffer) + @-context | Rules team/project/user (git); Memories per-project + per-user (siloed); index 6-wk TTL | User (rules) + harness sidecar proposes Memories → user approves; harness (index) | proc+pref (rules, strong) / sem (Memories, limited) / epis (weak) | Rules always-in-context / glob / agent-judged; **vector search over codebase** (not conversation); Memories surfaced as project context | Index re-embeds + 6-wk forget; Memories curation manual, no documented dedup/decay | Mem0/OpenMemory via MCP only |
| **GitHub Copilot coding agent** | Instruction files (`copilot-instructions.md`/`AGENTS.md`) + Copilot Memory (GitHub-hosted, citation-anchored observations) + separate VS Code local memory tool | Instructions project+org; Memory repo-level (shared) + user prefs (private); cross-agent | User (instructions) + agent (Memory, auto self-write, gated by write access) | proc / sem (Memory core) / epis (limited, feedback signals) / pref | Always-in-context instructions; Memory **relevance-surfaced + just-in-time citation-validated against live branch**; **no disclosed embeddings** | 28-day unused-TTL with renewal; discard/correct on stale citations; feedback de-weighting | None first-party; MCP for tools, not memory |
| **Cline** | `.clinerules`/`AGENTS.md` + Memory Bank (markdown convention) + Auto Compact | Within-session transcript; Rules+Memory Bank across-session (files); workspace/global; org=committed files | User/team (hand-edit) + agent (self-edit rules + Memory Bank, propose→approve) + harness (Auto Compact) | sem / proc / epis (progress.md) / pref (informal) — all **convention-driven** | Always-in-context (rules + instructed full Memory Bank read); @-mentions; conditional glob rules; **no vector recall** | Auto Compact (lossy summarize/replace) + truncation fallback; updates manual; no temporal/fact-invalidation | Mem0 / KG memory via MCP only (community) |
| **Aider** | Repo map (tree-sitter + PageRank) + `CONVENTIONS.md` + chat-history files + git auto-commit | Project-level files + repo-map disk cache + git log; no org/shared store | User (`CONVENTIONS.md`) + harness (repo map, history, commits, summaries); **agent does not self-write memory** | proc+pref (conventions) / epis (shallow: chat log+git) / sem (structural map only, regenerated) | Always-in-context (map + read files); **no semantic/vector recall**; history restored-or-summarized, not queried | Chat summarization (ChatSummary); `/clear`; map mtime-refresh; no fact-level forgetting | None built-in (DIY/third-party only) |
| **Goose (Block)** | `.goosehints` + Memory extension (MCP tool, category/tag files) + recipes + auto-compaction | Per-entry local vs global (`is_global`); across-session; no org sync | User (`.goosehints`) + agent (Memory ext, self-write, usually user-triggered/confirmed) + harness (compaction) | sem / proc / pref (Memory ext + hints) / epis (only transient compaction summaries) | `.goosehints` always-in-context; Memory ext **tag/category keyword match**; **no vector recall** | Auto-compact at 80%; tool-output summarization; memory forgetting **manual only**; no dedup/decay/conflict | Hjarni/Notion/Pieces via MCP; no first-party Mem0/Zep |
| **OpenHands** | Append-only event stream (`ConversationMemory`) + microagents/Skills (markdown) + LLM condenser | Within+across-session (conversation persistence); repo/user/org/global skills | User/team (skills) + harness (event log + condensed summaries); **no agent self-write of facts** | proc (strong) / sem (static, human-curated) / epis (strong, full event log) / pref (only if written) | Always-in-context repo agents; **keyword-triggered** knowledge agents (RecallAction); V1 summary-first on-demand; **no vector recall** | `LLMSummarizingCondenser` (keep-first + summarize old); forgetting = lossy truncation; updates = edit markdown | None default (RAG roadmap; no Mem0/Zep) |
| **Hermes Agent (Nous)** | `MEMORY.md`/`USER.md` (memory tool) + episodic SQLite/FTS5 + Skills (`SKILL.md`) + `SOUL.md` + optional external provider | Cross-session, profile-scoped (`~/.hermes/`); org via shared dirs/cloud (manual) | Agent self-writes (memory tool, skill_manage; optional approval gate) + harness (state.db, providers) + user hand-edit | sem (`MEMORY.md`) / proc (Skills) / epis (FTS5 search) / pref (`USER.md` + Honcho) — **all four** | Hybrid: frozen always-in-context curated snapshot + on-demand `session_search` (FTS5) + progressive-disclosure skills; providers add vector/KG | Hard-bounded files (error-on-overflow → agent consolidates); `replace`/`remove`; background "Curator" (under-documented); per-provider forget | First-class plugins: **Mem0, Honcho, Hindsight (KG), Holographic, RetainDB, OpenViking, ByteRover, Supermemory** (one active) |
| **Devin (Cognition)** | Knowledge (semantic, trigger-cued) + Playbooks (`.devin.md` procedural) + rules-file ingestion + Machine Snapshots (blockdiff) | Knowledge org/enterprise/repo; Playbooks team/org; snapshots account-scoped | Mostly user-authored; harness auto-generates repo Knowledge + ingests rules; auto-suggest **partially confirmed** | sem (Knowledge) / proc (Playbooks) / pref (Knowledge) / epis (largely no; rollback only) | Knowledge **on-demand semantic recall via Trigger Description** (repo-pinned always-on); Playbooks explicit attach; backend undisclosed | Knowledge update on rules change; Usage tab flags "misleading"; **no auto decay/forgetting**; snapshot blockdiff compaction | None — fully proprietary, no Mem0/Zep |
| **Letta (MemGPT) + Mem0/Zep** | Letta: self-editing core blocks + recall + archival **vector** store + Letta Code git MemFS. Mem0: auto-extract + vector + optional **graph** + KV | Letta: DB-persisted indefinitely, shareable blocks; Mem0: session/user/org by ID | Letta: **agent self-writes** + sleeptime subagents + user `/remember`. Mem0: **auto LLM extraction** + explicit `add()` | Letta: sem/epis/pref/proc (blocks+files). Mem0: factual/episodic/semantic/working (**procedural a gap**) | Letta: hybrid always-in-context blocks/`system/` + on-demand vector (archival) + recall search. Mem0: on-demand semantic, relevance-ranked injection | Letta: **sleeptime compute** rewrites/condenses blocks; fixed block limits. Mem0: **ADD/UPDATE/DELETE/NOOP** Updater + graph conflict resolver | Letta = native runtime (no Mem0/Zep backend). Mem0/Zep = the drop-in layer others integrate |
| **`nooa.memory` (ours)** | Opt-in subsystem `MemoryManager.install()`; agent-authored via native tools (remember/recall/search/update/forget/associate) + injected `MEMORY_SCHEMA_GUIDE`; one SQLite (records + typed causal graph + vector blobs) | Per-store SQLite, separate from session DB; scope = wherever the store file lives (no built-in org/team-sync layer — see caveats) | **Agent self-writes/curates**, explicitly instructed with a cognitive schema; not harness extraction | **All, typed:** info (sem) / skill (proc) / episode (epis) / intent (prospective) / reflection (gist) / scratch (working) | **Hybrid dense (embeddings) + sparse (keyword) → ACT-R scoring (relevance+recency+importance) → multi-hop graph spread** + per-turn spontaneous-association context block | **Dreaming:** dedup merge → LLM reconciliation (keep-latest, archive stale) → edge formation → re-score → episode→reflection abstraction → prune. **Forgetting:** Ebbinghaus decay (slowed by retrieval) + protected types + archive-vs-delete | Pluggable VectorIndex (numpy / sqlite-vec / chroma) + pluggable embeddings (hashing default / litellm). Not designed to wrap external Mem0/Zep |

## Harness-by-harness

Each subsection contrasts the harness with `nooa.memory` and preserves the source citations.

### Claude Code

The most mature *file-based* story: layered `CLAUDE.md` (org→user→project→local), agent-written auto-memory, and robust compaction. It covers all four memory types — but semantic facts are markdown, retrieval is always-loaded-files or path-triggered, and there is no native vector/semantic recall, no decay/TTL in the CLI, and no graph. The API Memory tool (`memory_20250818`, beta) is a separate client-side filesystem primitive, not a vector DB. Ours replaces "load the whole file every turn" with embedding+keyword candidate generation and ACT-R relevance/recency/importance scoring, adds Ebbinghaus decay and dream-based consolidation, and uses typed records over a causal graph. Claude Code's edge is transparency and battle-tested multi-scope layering (including un-excludable managed org policy); ours' edge is selective retrieval, forgetting, and abstraction.

Sources: [How Claude remembers your project — Claude Code Docs](https://code.claude.com/docs/en/memory) · [Memory tool — Claude API Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) · [Context Management — claude.com/blog](https://claude.com/blog/context-management) · [Manage sessions — Claude Code Docs](https://code.claude.com/docs/en/sessions) · [Create custom subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents) · [Mem0 + Claude Code](https://mem0.ai/blog/claude-code-memory)

### OpenAI Codex (CLI + cloud)

Closest to ours in *spirit* on the write side — "Memories" is genuinely agent/harness-extracted across sessions and projects, with secret redaction and ~30-day pruning of unused entries. But recall is grep/keyword over `MEMORY.md` (explicitly no embeddings), consolidation is delayed/async (a thread must idle ~6h before its summary is written), and there's no structured contradiction resolution. `AGENTS.md` is capped at 32 KiB with silent truncation; cloud tasks are essentially ephemeral (12h container cache). Ours does dense+sparse hybrid retrieval, online decay rather than fixed 30-day TTL, and an explicit reconsolidation step that reconciles contradictions (keep-latest, archive stale). Codex's pruning-by-TTL is simpler and arguably more predictable than our activation-based decay.

Sources: [Memories – Codex | OpenAI Developers](https://developers.openai.com/codex/memories) · [Features – Codex CLI](https://developers.openai.com/codex/cli/features) · [AGENTS.md guide – Codex](https://developers.openai.com/codex/guides/agents-md) · [Cloud environments – Codex web](https://developers.openai.com/codex/cloud/environments) · [Changelog – Codex](https://developers.openai.com/codex/changelog) · [Context Compaction (Justin3go)](https://justin3go.com/en/posts/2026/04/09-context-compaction-in-codex-claude-code-and-opencode) · [Codex CLI Memory + Mem0](https://mem0.ai/blog/how-memory-works-in-codex-cli) · [Memories in Codex · Discussion #12567](https://github.com/openai/codex/discussions/12567)

### Gemini CLI

Tiered markdown (`GEMINI.md`: global → project → subdir → private `MEMORY.md`) plus an experimental, human-gated Auto Memory that uniquely extracts procedural `SKILL.md` files — ahead of most CLIs. The `save_memory` tool (legacy) and v0.40.0 "Tiered Memory" let the agent self-write durable facts. Still brute-force concatenation with no semantic recall, no decay, no contradiction handling natively; vector/graph only via MCP (Mem0, Zep/Graphiti, Neo4j). Auto Memory candidates must be human-approved via `/memory inbox` before promotion. Ours bakes typed skills, semantic retrieval, decay, and consolidation into the core rather than leaving them to opt-in experiments or external servers. Gemini's human-approval inbox is a safety property ours does not emphasize (we trust the agent to author directly).

Sources: [Memory files / Memory tool — Gemini CLI docs](https://geminicli.com/docs/tools/memory/) · [GEMINI.md files](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md) · [Memory management tutorial](https://geminicli.com/docs/cli/tutorials/memory-management/) · [Auto Memory](https://geminicli.com/docs/cli/auto-memory/) · [v0.40.0 Tiered Memory (Discussion #26216)](https://github.com/google-gemini/gemini-cli/discussions/26216) · [Checkpointing](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/checkpointing.md) · [Auto Memory regression #25623](https://github.com/google-gemini/gemini-cli/issues/25623) · [Neo4j Extension — Google Cloud Blog](https://cloud.google.com/blog/topics/developers-practitioners/using-the-neo4j-extension-in-gemini-cli) · [Zep / Graphiti MCP](https://help.getzep.com/graphiti/getting-started/mcp-server)

### Cursor

The only harness here with a real first-party vector store — but it indexes the **codebase** (Turbopuffer, AST-aware ~500-token chunks, encrypted, 6-week inactivity TTL), not conversation/memory. Its "Memories" (beta since v1.0) are auto-proposed by a background sidecar then user-approved, per-project and per-user siloed, with no documented dedup/decay. Rules (`.cursor/rules/*.mdc`, `AGENTS.md`, legacy `.cursorrules`) give strong, git-versioned, team/org-enforceable procedural+preference memory with clear precedence (Team → Project → User). Ours applies vectors to *memory itself* (typed records), adds a graph and consolidation, and doesn't silo by project. Cursor's privacy-preserving encrypted index and IDE integration are genuinely stronger productized infrastructure than ours.

Sources: [Memories — Cursor Docs](https://docs.cursor.com/en/context/memories) · [Rules — Cursor Docs](https://cursor.com/docs/context/rules) · [Cursor 1.0 Changelog](https://cursor.com/en-US/changelog/1-0) · [Codebase Indexing — Cursor Docs](https://cursor.com/docs/context/codebase-indexing) · [Securely indexing large codebases — Cursor Blog](https://cursor.com/blog/secure-codebase-indexing) · [How Cursor Indexes Codebases Fast — Engineer's Codex](https://read.engineerscodex.com/p/how-cursor-indexes-codebases-fast) · [Cursor persistent memory — MemNexus](https://memnexus.ai/blog/2026-02-20-cursor-persistent-memory) · [OpenMemory MCP — Mem0](https://mem0.ai/blog/introducing-openmemory-mcp)

### GitHub Copilot coding agent

The most novel retrieval guard: citation-anchored observations (fact + file:line + reason) that are just-in-time validated against the live branch ("self-healing") before use, plus a 28-day usage-TTL with renewal and cross-agent sharing (code review ↔ coding agent ↔ CLI). Notably it does this **without** disclosed embeddings — retrieval is relevance-surfacing + citation validation, not similarity search. A separate VS Code local memory tool (markdown under `/memories/`) is a distinct, complementary system. Ours uses embeddings + graph + ACT-R scoring and LLM reconsolidation instead of live-citation validation. Copilot's grounding-against-code is arguably *more* robust against staleness for code facts specifically, and its cloud-hosted repo/user scoping is more production-hardened; ours is more general-purpose and cognitively typed but has no equivalent "verify against source" gate.

Sources: [Building an agentic memory system for GitHub Copilot — The GitHub Blog](https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/) · [About GitHub Copilot Memory — GitHub Docs](https://docs.github.com/en/copilot/concepts/agents/copilot-memory) · [Managing Copilot Memory — GitHub Docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/copilot-memory) · [Copilot Memory on by default (Changelog 2026-03-04)](https://github.blog/changelog/2026-03-04-copilot-memory-now-on-by-default-for-pro-and-pro-users-in-public-preview/) · [Memory in VS Code agents](https://code.visualstudio.com/docs/copilot/agents/memory) · [Understanding Agentic Memory in GitHub Copilot — Ken Muse](https://www.kenmuse.com/blog/understanding-agentic-memory-in-github-copilot/)

### Cline

Pure documentation discipline: `.clinerules` (also reads `.cursorrules`, `.windsurfrules`, `AGENTS.md`) + the Memory Bank convention + lossy Auto Compact. Broad type coverage, but it is prompt-engineering over plain files — Cline core explicitly has no vector store or embedding-based retrieval — no decay, no fact-invalidation, and persistence depends on humans/agent diligently editing files (the "update memory bank" command, or the propose→approve self-improving-rules loop). Ours provides the durable, queryable store the Memory Bank only simulates, with automatic consolidation instead of manual "update memory bank." Cline's total transparency and git-versioning are advantages ours' SQLite blobs lack.

Sources: [Memory Bank — Cline docs](https://docs.cline.bot/features/memory-bank) · [Memory Bank instruction rule](https://github.com/cline/prompts/blob/main/.clinerules/memory-bank.md) · [Auto Compact — Cline docs](https://docs.cline.bot/features/auto-compact) · [Rules (.clinerules) — Cline docs](https://docs.cline.bot/customization/cline-rules) · [Self-improving Cline — Cline Blog](https://cline.bot/blog/double-clicking-on-toggleable-clinerules-self-improving-cline) · [Context Management — DeepWiki](https://deepwiki.com/cline/cline/3.5-plan-and-act-modes) · [Memory Bank blog](https://cline.bot/blog/memory-bank-how-to-make-cline-an-ai-agent-that-never-forgets) · [Context condensing token burn — Issue #5616](https://github.com/cline/cline/issues/5616) · [Mem0 MCP integration](https://docs.mem0.ai/platform/features/mcp-integration)

### Aider

Deliberately not adaptive memory: a tree-sitter + NetworkX PageRank repo map (token-budgeted, diskcache-backed, mtime-validated), `CONVENTIONS.md`, chat history, and git as the episodic record. The agent never self-writes durable facts — its only durable side effect is committed code. The repo map is a clever, always-current structural-semantic artifact with no embeddings — efficient and zero-dependency, but it cannot recall decisions or learn. Cross-session continuity is off by default (`--restore-chat-history False`) and lossy when on (ChatSummary). Ours is the opposite philosophy: an agent-authored, decaying, consolidating fact/skill/episode store. Aider wins on simplicity and "always current without an embedding pipeline"; ours wins on cross-session learning.

Sources: [Repository map | aider](https://aider.chat/docs/repomap.html) · [aider/repomap.py](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py) · [Options reference | aider](https://aider.chat/docs/config/options.html) · [CONVENTIONS.md | aider](https://aider.chat/docs/usage/conventions.html) · [Git integration | aider](https://aider.chat/docs/git.html) · [YAML config | aider](https://aider.chat/docs/config/aider_conf.html) · [Token limits | aider](https://aider.chat/docs/troubleshooting/token-limits.html) · [Chat history control · Issue #3607](https://github.com/Aider-AI/aider/issues/3607)

### Goose (Block)

Clean separation of `.goosehints` / Memory extension / recipes / compaction, with an explicit MCP memory tool the agent self-writes (usually user-confirmed) and per-entry local/global scoping (`is_global`). But recall is tag/category keyword matching, everything loads into context, and forgetting is manual — Block's own blog ("What's in my `.goosehints` file (and why it probably shouldn't be)") warns about bloat. Auto-compaction triggers at 80% context (`GOOSE_AUTO_COMPACT_THRESHOLD`). Ours' hybrid retrieval + scoring avoids loading everything, and decay/dreaming handle the bloat Goose pushes onto the user. Goose's plain-file, greppable storage and explicit scoping flag are simpler to reason about.

Sources: [Memory Extension | goose](https://goose-docs.ai/docs/mcp/memory-mcp/) · [Smart Context Management | goose](https://goose-docs.ai/docs/guides/sessions/smart-context-management/) · [What's in my .goosehints file (DEV)](https://dev.to/blockopensource/whats-in-my-goosehints-file-and-why-it-probably-shouldnt-be-3j8p) · [block/goose GitHub repo](https://github.com/block/goose) · [Hjarni for Goose](https://hjarni.com/for/goose) · [Goose ↔ Notion as memory](https://keepwebsitesweird.pika.page/posts/til-connecting-goose-notion-as-an-agent-memory-system) · [Memory tool-call issue #1108](https://github.com/block/goose/issues/1108)

### OpenHands

Strongest *episodic* design: an append-only, replayable, persistable event stream (`ConversationMemory` / `EventLog`) plus scoped microagents/Skills (repo/user/org/global, with V1 on-demand summary-first loading) and a measured LLM condenser (`LLMSummarizingCondenser`, keep-first + summarize old; condensation tracked as a first-class event). But there is no agent self-write of semantic facts, retrieval is keyword-triggered (`RecallAction` → `RecallObservation`, no embeddings), and updates mean editing markdown. Pluggable event backends (local/in-memory/S3/GCS). Ours adds agent-authored semantic/skill/preference memory, embedding+graph retrieval, and abstraction of episodes into reflections — which is precisely OpenHands' gap (it keeps raw events, we distill them). OpenHands' pluggable event backends and first-class condensation events are more operationally mature.

Sources: [Skills Overview — OpenHands Docs](https://docs.openhands.dev/overview/skills) · [Context Condenser — OpenHands Docs](https://docs.openhands.dev/sdk/guides/context-condenser) · [Conversation Persistence](https://docs.openhands.dev/sdk/guides/convo-persistence) · [skills/README.md](https://github.com/OpenHands/OpenHands/blob/main/skills/README.md) · [Context Condensation blog](https://www.openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents) · [PR #7311 AgentCondensationAction](https://github.com/OpenHands/OpenHands/pull/7311) · [PR #5306 Condenser Interface](https://github.com/OpenHands/OpenHands/pull/5306) · [Event Storage and Replay — DeepWiki](https://deepwiki.com/All-Hands-AI/OpenHands/12.2-event-storage-and-replay) · [The OpenHands Software Agent SDK (arXiv 2511.03690)](https://arxiv.org/html/2511.03690v1)

### Hermes Agent (Nous Research)

The closest peer in ambition — it covers all four classic types with purpose-fit mechanisms (curated `MEMORY.md`/`USER.md` via a `memory` tool, episodic SQLite/FTS5 `session_search`, `SKILL.md` procedural via progressive disclosure, dialectic user model), agent self-curation, and **first-class pluggable external providers including a knowledge graph (Hindsight) and Mem0**. Differences: Hermes' built-in recall is FTS5 (lexical), with semantic/graph coming from external providers (one active at a time); its curated memory is a *frozen, hard-bounded* snapshot (stale within a session, ~800/~500 tokens, error-on-overflow forces in-turn consolidation). Ours keeps semantic retrieval, graph, decay, and consolidation **in-core** (not requiring a provider), is not size-frozen, and refreshes via a per-turn spontaneous-association block rather than a session-frozen snapshot. Hermes' ecosystem breadth (8 providers, KG, dialectic modeling) and cache-friendly engineering are real advantages; our consolidation/forgetting pipeline is more explicitly specified than Hermes' under-documented "Curator." (Exact Curator cadence numbers in third-party write-ups are *unverified*; Zep is *not* a listed built-in provider.)

Sources: [GitHub — NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent) · [Persistent Memory — Hermes docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) · [Memory Providers](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) · [Honcho Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho) · [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) · [Memory Systems chapter — Ken Huang](https://kenhuangus.substack.com/p/chapter-8-memory-systems-and-state) · [Hermes Memory System — Medium (Timi)](https://medium.com/@xpf6677/hermes-agent-memory-system-curated-memory-session-search-and-self-improvement-a84d2a9d5d01) · [Issue #4889 (Honcho dialectic prefetch)](https://github.com/NousResearch/hermes-agent/issues/4889) · [Issue #17649 (FTS5 skill retrieval)](https://github.com/NousResearch/hermes-agent/issues/17649)

### Devin (Cognition)

Productized separation of semantic Knowledge (Content + Trigger Description, trigger-cued on-demand semantic recall; repo-pinned Knowledge always-on; backend undisclosed — vector vs LLM ranking not confirmed) vs. procedural Playbooks (`.devin.md`: Overview / Procedure / Specifications / Forbidden Actions), with strong org/enterprise scoping and VM Machine Snapshots (blockdiff) for environment persistence, sleep/wake, and rollback. But it is largely manual curation (auto-suggest of Knowledge is *partially confirmed* / version-dependent), has no durable episodic memory (rollback only), no auto decay/conflict-resolution (a Usage tab flags "misleading" items for manual pruning), and is fully proprietary. Ours adds episodic memory, automatic consolidation/forgetting, and an open pluggable backend. Devin's enterprise scoping, snapshot-based environment persistence, and trigger-based selective recall at scale are more production-proven.

Sources: [Knowledge — Devin Docs](https://docs.devin.ai/product-guides/knowledge) · [Knowledge Onboarding](https://docs.devin.ai/onboard-devin/knowledge-onboarding) · [Session Insights](https://docs.devin.ai/product-guides/session-insights) · [Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks) · [Using Playbooks](https://docs.devin.ai/product-guides/using-playbooks) · [Introducing Playbooks](https://docs.devin.ai/working-with-teams/playbooks-intro) · [Instructing Devin Effectively](https://docs.devin.ai/essential-guidelines/instructing-devin-effectively) · [Blockdiff — Cognition](https://cognition.com/blog/blockdiff) · [Devin Sept '24 Update — Cognition](https://cognition.ai/blog/sept-24-product-update)

### Letta (MemGPT) + Mem0/Zep

This is the research lineage ours sits in, so the contrast is about *placement*, not category. Letta is a whole runtime built around self-editing core memory blocks + recall (full history search) + archival **vectors** + sleeptime consolidation (background subagents rewrite/condense blocks every *N* steps), with Letta Code adding a git-backed MemFS; everything is DB-persisted indefinitely and blocks are shareable across agents. Mem0/Zep are drop-in layers with auto-extraction, vector(+graph) stores, and an explicit ADD/UPDATE/DELETE/NOOP Updater (Mem0) / temporal knowledge graph with validity windows (Zep). Ours echoes all three pillars — agent-authored typed memory (Letta-like), vector+graph retrieval and LLM reconciliation (Mem0-like), consolidation (sleeptime/dream-like). The key difference: Letta requires you to build *inside* it (lock-in) and Mem0/Zep require *wrapping* your LLM calls externally, whereas ours is an **opt-in subsystem installed onto an existing agent with zero core edits**. Honest caveats: Mem0/Zep's conflict-resolution and Zep's temporal graph are more battle-tested at production scale than our reconsolidation; Letta's full-DB "nothing is ever lost" persistence and shared-block multi-agent model are more proven than ours. (No official Letta feature uses Mem0/Zep as a backend; Mem0's procedural-memory typing is *unconfirmed*.)

Sources: [Memory | Letta Docs (agents)](https://docs.letta.com/guides/agents/memory) · [Memory | Letta Code / MemFS](https://docs.letta.com/letta-code/memory) · [Sleep-time agents | Letta Docs](https://docs.letta.com/guides/agents/architectures/sleeptime/) · [Sleep-time Compute | Letta Blog](https://www.letta.com/blog/sleep-time-compute) · [Agent Memory | Letta Blog](https://www.letta.com/blog/agent-memory/) · [letta-ai/letta-code](https://github.com/letta-ai/letta-code) · [Memory Types | Mem0 Docs](https://docs.mem0.ai/core-concepts/memory-types) · [Graph Memory | Mem0 Docs](https://docs.mem0.ai/platform/features/graph-memory) · [Mem0 (arXiv 2504.19413)](https://arxiv.org/html/2504.19413v1) · [Mem0 vs Letta — Vectorize](https://vectorize.io/articles/mem0-vs-letta)

## Analysis: two paradigms

### Paradigm 1 — Static context / instruction memory

The dominant pattern across mainstream coding harnesses is **static instruction/context files + conversation compaction**: `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` / `.clinerules` / `CONVENTIONS.md` / `.goosehints` / microagents, loaded always-in-context (or path/keyword-triggered), plus an LLM condenser that summarizes-and-replaces history when the window fills. Aider, Cline, Goose, OpenHands, and the file layers of Claude Code / Codex / Gemini CLI are squarely here. The strengths are real: the memory is plain text a human can read, diff, git-commit, and own; it is deterministic and auditable; and it requires no embedding pipeline. The weaknesses follow directly: everything relevant must fit in the context window (or be manually `@`-mentioned), retrieval is coarse, and "forgetting" is either nonexistent (files accumulate until hand-edited) or blunt (lossy compaction / truncation).

### Paradigm 2 — Retrieval-based long-term memory

A **second, newer tier** layers agent/harness-extracted long-term facts on top — Claude Code auto-memory, Codex Memories, Cursor Memories, Copilot Memory, Gemini Auto Memory — but with two consistent limits. First, **retrieval is keyword/grep or always-in-context, not embedding-based** (Cursor's vectors are over code, not memory; Copilot uses citation-validation, not disclosed embeddings). Second, **consolidation/forgetting is shallow** — manual curation, or a flat unused-TTL like Codex's ~30 days / Copilot's 28 days. Only **Hermes, Devin, and the Letta/Mem0/Zep family** approach genuine **retrieval-based long-term episodic/semantic memory with typed stores and active consolidation** — and of those, only Hermes is itself an open coding harness; Devin is proprietary; Letta/Mem0/Zep are runtimes/layers rather than mainstream coding CLIs. `nooa.memory` sits firmly in this second paradigm, but pushes it further in-core (hybrid retrieval + cognitive scoring + graph spread + consolidation + decay) than any mainstream coding CLI does today.

## Where ours is differentiated — and where the harnesses are better

### Where ours is genuinely differentiated

Three things, taken together, are rare-to-unique among coding harnesses:

1. **Hybrid retrieval + cognitive scoring + graph spread in-core.** Dense+sparse candidate generation, ACT-R-style activation (relevance + base-level recency + importance), and multi-hop associative spreading — most harnesses have *none* of this natively (keyword match or full-file load); Cursor's vectors target code, not memory; Hermes' core recall is lexical FTS5 with semantics deferred to plugins.
2. **A real consolidation + forgetting pipeline.** "Dreaming" (dedup → LLM reconsolidation with contradiction handling → graph edge formation → importance re-scoring → episode→reflection abstraction → prune) plus online Ebbinghaus decay slowed by retrieval strength, with protected types and archive-vs-delete. This is Letta-sleeptime / Mem0-Updater-grade machinery, which no mainstream coding CLI ships in-core. Most harnesses either don't forget (files accumulate until hand-edited) or forget bluntly (flat TTL / lossy compaction).
3. **A typed cognitive taxonomy authored by the agent under an explicit schema.** Six types including *prospective* (intent) and *reflection/gist* — broader than the sem/proc/epis/pref most harnesses cover — and the agent is *instructed* with `MEMORY_SCHEMA_GUIDE` rather than relying on harness extraction. Combined with **opt-in install (zero core edits, inert when disabled)** and **pluggable vector/embedding backends**, this positions ours as research-grade memory delivered as an additive module.

### Where the harnesses are honestly better

- **Battle-tested at scale.** Claude Code, Codex, Copilot, Cursor, and Devin run across millions of repos/users; their TTLs, scoping, and privacy boundaries are production-hardened. Ours' empirical evidence is from controlled evals, not fleet scale.
- **Simple, transparent, user-editable files.** CLAUDE.md / AGENTS.md / GEMINI.md / Memory Bank / `.goosehints` are plain markdown a human can read, diff, git-commit, and own. Ours' SQLite + vector blobs are far less inspectable and not naturally version-controlled.
- **No embedding dependency.** Aider's repo map, Codex/Goose keyword recall, and Copilot's citation-validation deliver useful recall with zero embedding pipeline. Ours' best mode needs an embeddings backend (the hashing default avoids a network dependency but is weaker).
- **IDE / platform integration & governance.** Cursor and Copilot (IDE), Devin (org/enterprise Knowledge promotion), and Claude Code (un-excludable managed org policy) offer team/org sharing and governance ours does not natively provide — our store is file-local with no built-in org-sync.
- **Grounding against live code.** Copilot's just-in-time citation validation and Aider's always-regenerated repo map are arguably *more* resistant to staleness for code facts than our activation decay + periodic reconsolidation.
- **Cache-friendly engineering & approval gates.** Hermes' frozen-snapshot prompt-caching discipline, and the human-in-the-loop approval flows in Gemini/Cursor/Cline, are operational maturities ours' direct agent-authoring trades away.
- **More proven contradiction/temporal handling.** Mem0's ADD/UPDATE/DELETE Updater and Zep's *temporal* validity windows are more validated than our keep-latest reconsolidation.

## Positioning & takeaways

**Ours brings research-grade memory — the MemGPT / Mem0 / Zep lineage of typed, agent-authored records with hybrid retrieval, consolidation, and forgetting — into an agent harness as an opt-in, zero-core-edit subsystem.** Most coding harnesses today ship **instruction-file memory plus context compaction**, increasingly augmented by **keyword/citation-surfaced extracted facts with flat TTLs** — useful, transparent, and battle-tested, but without embedding-ranked recall, a causal graph, or an active consolidation/decay pipeline in-core.

Stated fairly, with caveats: the landscape **overlaps** with ours more than a single headline suggests — Codex / Claude Code / Cursor / Copilot / Gemini already do agent/harness-written long-term facts; Copilot adds live-citation grounding and usage-TTL; Cursor has a real (code) vector store; **Hermes covers all four memory types and even bundles a knowledge graph and Mem0**; and **Letta/Mem0/Zep already implement the full research-grade pattern** (Letta as a runtime, Mem0/Zep as drop-in layers). Our distinct claim is therefore not "first to have memory" but **"research-grade typed memory + hybrid retrieval + consolidation + Ebbinghaus forgetting, delivered as an additive module on an existing agent rather than as a separate runtime (Letta), an external service to wrap (Mem0/Zep), an experimental opt-in (Gemini), or a productized-but-proprietary feature (Devin/Copilot/Cursor)."**

The honest caveats: our cognitive taxonomy and forgetting are richer on paper than what most harnesses ship, but those harnesses beat us on scale-hardening, file transparency, IDE/org governance, zero-dependency recall, and (for Mem0/Zep) proven contradiction/temporal handling — and our published gains are from controlled evals showing a real trade-off (dreaming aids synthesis but hurts pinpoint lookup), not fleet-scale production data.

## References

**Claude Code**
- How Claude remembers your project — Claude Code Docs: https://code.claude.com/docs/en/memory
- Memory tool — Claude API Docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- Context Management — claude.com/blog: https://claude.com/blog/context-management
- Manage sessions — Claude Code Docs: https://code.claude.com/docs/en/sessions
- Create custom subagents — Claude Code Docs: https://code.claude.com/docs/en/sub-agents
- Mem0 + Claude Code: https://mem0.ai/blog/claude-code-memory

**OpenAI Codex**
- Memories – Codex: https://developers.openai.com/codex/memories
- Features – Codex CLI: https://developers.openai.com/codex/cli/features
- AGENTS.md guide – Codex: https://developers.openai.com/codex/guides/agents-md
- Cloud environments – Codex web: https://developers.openai.com/codex/cloud/environments
- Changelog – Codex: https://developers.openai.com/codex/changelog
- Context Compaction (Justin3go): https://justin3go.com/en/posts/2026/04/09-context-compaction-in-codex-claude-code-and-opencode
- Codex CLI Memory + Mem0: https://mem0.ai/blog/how-memory-works-in-codex-cli
- Memories in Codex · Discussion #12567: https://github.com/openai/codex/discussions/12567

**Gemini CLI**
- Memory files / Memory tool — Gemini CLI docs: https://geminicli.com/docs/tools/memory/
- GEMINI.md files: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md
- Memory management tutorial: https://geminicli.com/docs/cli/tutorials/memory-management/
- Auto Memory: https://geminicli.com/docs/cli/auto-memory/
- v0.40.0 Tiered Memory (Discussion #26216): https://github.com/google-gemini/gemini-cli/discussions/26216
- Checkpointing: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/checkpointing.md
- Auto Memory regression #25623: https://github.com/google-gemini/gemini-cli/issues/25623
- Neo4j Extension — Google Cloud Blog: https://cloud.google.com/blog/topics/developers-practitioners/using-the-neo4j-extension-in-gemini-cli
- Zep / Graphiti MCP server: https://help.getzep.com/graphiti/getting-started/mcp-server

**Cursor**
- Memories — Cursor Docs: https://docs.cursor.com/en/context/memories
- Rules — Cursor Docs: https://cursor.com/docs/context/rules
- Cursor 1.0 Changelog: https://cursor.com/en-US/changelog/1-0
- Codebase Indexing — Cursor Docs: https://cursor.com/docs/context/codebase-indexing
- Securely indexing large codebases — Cursor Blog: https://cursor.com/blog/secure-codebase-indexing
- How Cursor Indexes Codebases Fast — Engineer's Codex: https://read.engineerscodex.com/p/how-cursor-indexes-codebases-fast
- Cursor persistent memory — MemNexus: https://memnexus.ai/blog/2026-02-20-cursor-persistent-memory
- OpenMemory MCP — Mem0: https://mem0.ai/blog/introducing-openmemory-mcp

**GitHub Copilot coding agent**
- Building an agentic memory system for GitHub Copilot — The GitHub Blog: https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/
- About GitHub Copilot Memory — GitHub Docs: https://docs.github.com/en/copilot/concepts/agents/copilot-memory
- Managing Copilot Memory — GitHub Docs: https://docs.github.com/en/copilot/how-tos/use-copilot-agents/copilot-memory
- Copilot Memory on by default (Changelog 2026-03-04): https://github.blog/changelog/2026-03-04-copilot-memory-now-on-by-default-for-pro-and-pro-users-in-public-preview/
- Memory in VS Code agents: https://code.visualstudio.com/docs/copilot/agents/memory
- Understanding Agentic Memory in GitHub Copilot — Ken Muse: https://www.kenmuse.com/blog/understanding-agentic-memory-in-github-copilot/

**Cline**
- Memory Bank — Cline docs: https://docs.cline.bot/features/memory-bank
- Memory Bank instruction rule: https://github.com/cline/prompts/blob/main/.clinerules/memory-bank.md
- Auto Compact — Cline docs: https://docs.cline.bot/features/auto-compact
- Rules (.clinerules) — Cline docs: https://docs.cline.bot/customization/cline-rules
- Self-improving Cline — Cline Blog: https://cline.bot/blog/double-clicking-on-toggleable-clinerules-self-improving-cline
- Context Management — DeepWiki: https://deepwiki.com/cline/cline/3.5-plan-and-act-modes
- Memory Bank blog: https://cline.bot/blog/memory-bank-how-to-make-cline-an-ai-agent-that-never-forgets
- Context condensing token burn — Issue #5616: https://github.com/cline/cline/issues/5616
- Mem0 MCP integration: https://docs.mem0.ai/platform/features/mcp-integration

**Aider**
- Repository map | aider: https://aider.chat/docs/repomap.html
- aider/repomap.py: https://github.com/Aider-AI/aider/blob/main/aider/repomap.py
- Options reference | aider: https://aider.chat/docs/config/options.html
- CONVENTIONS.md | aider: https://aider.chat/docs/usage/conventions.html
- Git integration | aider: https://aider.chat/docs/git.html
- YAML config | aider: https://aider.chat/docs/config/aider_conf.html
- Token limits | aider: https://aider.chat/docs/troubleshooting/token-limits.html
- Chat history control · Issue #3607: https://github.com/Aider-AI/aider/issues/3607

**Goose (Block)**
- Memory Extension | goose: https://goose-docs.ai/docs/mcp/memory-mcp/
- Smart Context Management | goose: https://goose-docs.ai/docs/guides/sessions/smart-context-management/
- What's in my .goosehints file (DEV): https://dev.to/blockopensource/whats-in-my-goosehints-file-and-why-it-probably-shouldnt-be-3j8p
- block/goose GitHub repo: https://github.com/block/goose
- Hjarni for Goose: https://hjarni.com/for/goose
- Goose ↔ Notion as memory: https://keepwebsitesweird.pika.page/posts/til-connecting-goose-notion-as-an-agent-memory-system
- Memory tool-call issue #1108: https://github.com/block/goose/issues/1108

**OpenHands**
- Skills Overview — OpenHands Docs: https://docs.openhands.dev/overview/skills
- Context Condenser — OpenHands Docs: https://docs.openhands.dev/sdk/guides/context-condenser
- Conversation Persistence: https://docs.openhands.dev/sdk/guides/convo-persistence
- skills/README.md: https://github.com/OpenHands/OpenHands/blob/main/skills/README.md
- Context Condensation blog: https://www.openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents
- PR #7311 AgentCondensationAction: https://github.com/OpenHands/OpenHands/pull/7311
- PR #5306 Condenser Interface: https://github.com/OpenHands/OpenHands/pull/5306
- Event Storage and Replay — DeepWiki: https://deepwiki.com/All-Hands-AI/OpenHands/12.2-event-storage-and-replay
- The OpenHands Software Agent SDK (arXiv 2511.03690): https://arxiv.org/html/2511.03690v1

**Hermes Agent (Nous Research)**
- GitHub — NousResearch/hermes-agent: https://github.com/nousresearch/hermes-agent
- Persistent Memory — Hermes docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
- Memory Providers: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md
- Honcho Memory: https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho
- Skills System: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- Memory Systems chapter — Ken Huang: https://kenhuangus.substack.com/p/chapter-8-memory-systems-and-state
- Hermes Memory System — Medium (Timi): https://medium.com/@xpf6677/hermes-agent-memory-system-curated-memory-session-search-and-self-improvement-a84d2a9d5d01
- Issue #4889 (Honcho dialectic prefetch): https://github.com/NousResearch/hermes-agent/issues/4889
- Issue #17649 (FTS5 skill retrieval): https://github.com/NousResearch/hermes-agent/issues/17649

**Devin (Cognition)**
- Knowledge — Devin Docs: https://docs.devin.ai/product-guides/knowledge
- Knowledge Onboarding: https://docs.devin.ai/onboard-devin/knowledge-onboarding
- Session Insights: https://docs.devin.ai/product-guides/session-insights
- Creating Playbooks: https://docs.devin.ai/product-guides/creating-playbooks
- Using Playbooks: https://docs.devin.ai/product-guides/using-playbooks
- Introducing Playbooks: https://docs.devin.ai/working-with-teams/playbooks-intro
- Instructing Devin Effectively: https://docs.devin.ai/essential-guidelines/instructing-devin-effectively
- Blockdiff — Cognition: https://cognition.com/blog/blockdiff
- Devin Sept '24 Update — Cognition: https://cognition.ai/blog/sept-24-product-update

**Letta (MemGPT) + Mem0 / Zep**
- Memory | Letta Docs (agents): https://docs.letta.com/guides/agents/memory
- Memory | Letta Code / MemFS: https://docs.letta.com/letta-code/memory
- Sleep-time agents | Letta Docs: https://docs.letta.com/guides/agents/architectures/sleeptime/
- Sleep-time Compute | Letta Blog: https://www.letta.com/blog/sleep-time-compute
- Agent Memory | Letta Blog: https://www.letta.com/blog/agent-memory/
- letta-ai/letta-code: https://github.com/letta-ai/letta-code
- Memory Types | Mem0 Docs: https://docs.mem0.ai/core-concepts/memory-types
- Graph Memory | Mem0 Docs: https://docs.mem0.ai/platform/features/graph-memory
- Mem0 (arXiv 2504.19413): https://arxiv.org/html/2504.19413v1
- Mem0 vs Letta — Vectorize: https://vectorize.io/articles/mem0-vs-letta
