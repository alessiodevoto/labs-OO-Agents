# LLM Trace Message Storage: Trade Space Analysis

*v0.2 — 2026-03-26 (revised after three independent reviews)*

---

## Problem Statement

In an agentic loop with N turns, the context window grows by roughly one message per turn. Every major LLM observability tool stores the **complete context window as a full copy inside each LLM call span**. This means:

- **Storage complexity: O(N²)** — a 100-turn agent with a 4k-token average new message per turn accumulates ~20M tokens stored across all spans. At ~4 bytes/token with JSON envelope overhead, that is roughly **150–250 MB per session** on disk. At a thousand sessions per day, that is 150–250 GB/day. Track B (content-addressed) would store the same session in ~1.6 MB — a ~100× reduction.
- **Wire inefficiency** — the full history is serialised and sent to the OTLP backend on every LLM call. At turn 100, serialising 400k tokens of span attributes adds non-trivial latency on the critical path.
- **Attribute cap truncation** — OpenTelemetry's default span attribute limit (`OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT`) is 128 per span. The OpenInference flattened-attribute format (`llm.input_messages.N.message.role`, `.content`, `.tool_calls.M.*`) uses 2 attributes for a simple message and 4–6 for messages with tool calls. The cap is therefore hit at roughly 64 plain messages or ~20–30 messages with one tool call each. **The exact threshold depends on conversation structure.** Truncation is silent — the OTel SDK drops excess attributes with a log warning only.

> **Root cause of the "50-message cap" is unconfirmed.** The 128-attribute limit is the most likely culprit, but other candidates include: a hard-coded limit in the openinference-instrumentation-litellm package itself; the gRPC default 4 MB per-message limit hitting with large contexts; or `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` truncating individual large message contents. This needs a reproduction with the actual instrumentor to confirm.

The goals are:

1. **Lossless compression** — reconstruct the full LLM input and output *exactly* for every LLM call. Same for tool input/output.
2. **Compatibility with open standards** — OpenInference, OTel GenAI semantic conventions, ATIF/Harbor, NeMo RL SFT (unless lossless compression is provably incompatible).
3. **Industry-standard telemetry model** — publish to an endpoint; use standard OTLP where possible.

> **Note on goal 3:** OTLP is a push-based, real-time, sampling-tolerant telemetry protocol. SFT training data requires batch-complete, ordering-guaranteed, lossless export. These are semantically incompatible. "Publish to an endpoint" and "NeMo RL SFT compat" imply *different endpoints with different protocols*. No single standard bridges both. The design must handle them separately.

---

## Prior Art Survey

**No LLM observability product has shipped content-addressed or delta-encoded message storage.** The underlying data model, however, has abundant prior art: Git's object store (`sha1(type + "\x00" + content)`), IPFS, Nix, and the event sourcing / CQRS deduplication pattern (using content hash as dedup key) are all direct structural precedents. This is not a novel idea; it has simply not been applied to LLM observability yet.

Every tool surveyed stores the full growing context window redundantly per call:

| Tool | Message storage | Deduplication | Content addressing |
|---|---|---|---|
| Arize Phoenix (OpenInference) | Flat span attrs, full copy per span | None | No |
| W&B Weave | Full copy in `inputs.messages` per call | None | No |
| LangSmith | Full copy in `inputs` per LLM run | None | No |
| Langfuse | Full copy per OTLP obs; large payloads offloaded to S3† | None (S3 key is not content hash) | No |
| Helicone | Full payload per request; session grouping via header | None | No |
| Datadog LLM Observability | Full context per OTel GenAI span | None | No |
| OpenLLMetry (Traceloop) | Full copy per span | None | No |
| MLflow AI Tracing | Full prompt+completion per trace | None | No |
| ATIF / Harbor | Full message per step | None | No |
| NeMo RL SFT | Full `messages[]` per training example | None (dedup upstream via NeMo Curator) | No |

† *Langfuse's v3 architecture: incoming OTLP → S3 raw storage → ClickHouse (ZSTD columnar). Very large payloads are stored in S3 with a reference in ClickHouse. The S3 key is not a content hash, so this is not deduplication — but the reference-based pattern is architecturally adjacent to Approach 2.*

**Adjacent approaches that don't solve this:**
- **Columnar compression (ClickHouse + ZSTD):** byte-level compression, not semantic deduplication
- **Git Context Controller (arxiv 2508.00031):** treats in-context memory like a git repo during execution, not trace storage
- **Semantic caching:** avoids re-running identical calls at inference time, not relevant to trace storage
- **OTel spec "external storage hooks":** the OTel GenAI spec notes that large message content MAY be stored externally with a reference placed in the span, but leaves implementation entirely to the user

---

## Current State of nemo_oo_agents

**Golden path:** `nemo_oo_agents start-dev` starts the local viewer at `localhost:5001`. `enable_tracing()` with no arguments probes that endpoint; if reachable, all spans are sent via OTLP HTTP (`POST /v1/traces`). Files (OTLP JSONL, `{session_id}.jsonl`) are a secondary option — still supported via `exporters.jsonl()` and importable into the viewer, but not the default.

LLM call spans use OpenInference flattened attributes:

```
llm.input_messages.0.message.role = "system"
llm.input_messages.0.message.content = "You are an assistant..."
llm.input_messages.1.message.role = "user"
llm.input_messages.1.message.content = "Hello"
llm.input_messages.1.message.tool_calls.0.tool_call.function.name = "search"
```

Each successive LLM call span re-emits the complete history via the litellm instrumentor. The `context_snapshot` diff mechanism in `_hooks_impl.py` captures agent-method-level context diffs, but the LLM spans themselves carry the full redundant copy.

The viewer receives OTLP spans and stores them in SQLite (`otlp_store.py`). The trace explorer (`explorer.py`, ~3,800 lines) is built entirely around this OTLP/SQLite pipeline: it reads `TracesData` objects, unpacks OTel `AnyValue` encoding, reassembles messages from flattened OpenInference attributes, and constructs the agent session tree from OTel span parent-child links. Any replacement format requires either a significant rewrite of this pipeline or a new viewer endpoint and storage path.

---

## Standards Landscape

### OpenInference
Maintained by Arize. Messages stored as flat per-field span attributes. **Emitted by default (opt-out model)** — not "always emitted." The spec defines `TraceConfig` with `OPENINFERENCE_HIDE_INPUT_MESSAGES`, `OPENINFERENCE_HIDE_OUTPUT_MESSAGES`, `OPENINFERENCE_HIDE_INPUTS`, `OPENINFERENCE_HIDE_OUTPUTS`, etc., settable via environment variables or code. The LiteLLM proxy also exposes `turn_off_message_logging=True` as an independent toggle. This means **today, without any new design, you can suppress message content from OpenInference spans** — at the cost of losing message visibility in Phoenix/Langfuse.

### OTel GenAI Semantic Conventions
Maintained by the OpenTelemetry GenAI SIG. **Status: Development** — not stable. The spec is actively evolving. Key properties:
- Message content (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`) is **opt-in** — suppressed by default due to PII concerns
- The spec defines an opt-in `gen_ai.client.inference.operation.details` log event that carries message content in the OTel Logs signal, structured, never as a JSON string
- `gen_ai.conversation.id` attribute exists (also Development status) for cross-span conversation linking

**Correction from draft v0.1:** The spec does not declare "spans for metadata, log events for content" as a directional mandate. It explicitly supports recording message content on spans (structured or JSON string) as equally valid. The log event approach is one option, not a stated direction of travel.

### ATIF / Harbor
Agent Trajectory Interchange Format. Maintained at [harbor-framework/harbor](https://github.com/harbor-framework/harbor). **Current stable version: v1.4** as documented at harborframework.com. A v1.6 RFC draft adds multimodal content (image paths) and `continued_trajectory_ref` — but this has not been reflected in the stable docs as of March 2026. Designed for eval, SFT, and RL training — not real-time observability. No OTel compatibility. Each step stores full message content. No content addressing.

### NeMo RL SFT
HuggingFace chat format JSONL. Required: `messages: [{role, content}]` array, self-contained per example. No delta format or conversation-ID referencing. Dataset-level deduplication is handled by NeMo Curator upstream. An export adapter from a content-addressed store is straightforward — it is a single-pass reconstruction, a few dozen lines of code.

---

## Design Approaches

### Approach 1 — Status Quo + fix the cap

Raise `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT` (one env var, or `SpanLimits(max_span_attributes=N)` on `TracerProvider`). Keep full copies per span.

**Half-measure option:** Set `OPENINFERENCE_HIDE_INPUT_MESSAGES=True` today to stop storing message content in spans entirely. Eliminates the storage problem and the attribute cap, but Phoenix/Langfuse no longer show message content.

| Dimension | Value |
|---|---|
| Storage complexity | O(N²) — ~150–250 MB/session |
| Wire per LLM call | Full history (latency impact at turn 100+) |
| Reconstruction | Trivial (self-contained spans) |
| OpenInference compat | Full |
| OTel GenAI compat | Full |
| ATIF compat | Full |
| NeMo RL SFT compat | Full |
| OTLP backend compat | Full |
| Lossless | Yes |
| Implementation effort | **One line** (cap fix); one env var (hide half-measure) |

**Pros:** Minimal change; fixes immediate bug; half-measure available today.
**Cons:** O(N²) storage and latency remain; inaccurate for very long conversations even with cap raised (gRPC 4MB limit).

---

### Approach 2 — Content-Addressed Blob Store (CAS) + hash refs in spans

Replace `message.content = "..."` with `message.content_ref = "sha256:abc"`. Side-channel blob store maps hash → full message object.

**Correct hash function:** `sha256(json.dumps(message_dict, sort_keys=True, ensure_ascii=False).encode())` over the complete message dict (including `role`, `content`, `tool_calls`, `tool_call_id`, `name`). The `role + "\x00" + content` scheme in the v0.1 draft had collision bugs: two messages with the same role and content but different `tool_calls` would hash identically; two pure-tool-call assistant messages with null/empty content would always collide.

| Dimension | Value |
|---|---|
| Storage complexity | O(N) |
| Wire per LLM call | Hashes only |
| Reconstruction | Lossless iff blob store is intact (blob store is a new failure surface) |
| OpenInference compat | **Breaks** — backends see empty content fields |
| OTel GenAI compat | **Breaks** |
| ATIF compat | Adapter needed |
| NeMo RL SFT compat | Adapter needed |
| OTLP backend compat | **None** |
| Lossless | Yes (with intact blob store) |
| Implementation effort | Medium |

**Pros:** Best storage and wire efficiency.
**Cons:** Unilaterally breaks all ecosystem compatibility; blob store is a separate operational dependency.

---

### Approach 3 — OTel Log Events per message

Each message is emitted as an OTel Log event keyed to `gen_ai.conversation.id` at the moment it is first added to the conversation. LLM call spans carry no message content.

```
Log event: {gen_ai.conversation.id: "conv-123", sequence: 42,
            gen_ai.message.role: "user", gen_ai.message.content: "..."}
LLM span:  {gen_ai.conversation.id: "conv-123",
            gen_ai.usage.input_tokens: 1234}
Reconstruct: filter events by conversation_id, sort by sequence number
```

**Critical failure mode:** OTel log sampling is standard in production deployments. A tail-based sampler at 10% silently loses 90% of message events with no indication in the spans that content is missing. Unlike the O(N²) storage problem (which is noisy), sampling-induced data loss is invisible. This is a significantly worse failure mode for the lossless requirement.

**Current backend support:** As of March 2026, neither Phoenix nor Langfuse consume OTel log events for message display. This approach is not deployable today against any standard backend.

| Dimension | Value |
|---|---|
| Storage complexity | O(N) |
| Wire per LLM call | Delta (new messages only) |
| Reconstruction | Lossless if events are not sampled; requires sequence numbers (timestamps alone are insufficient) |
| OpenInference compat | Partial — Phoenix doesn't support log events for message content yet |
| OTel GenAI compat | **Full** — spec-aligned |
| ATIF compat | Adapter needed |
| NeMo RL SFT compat | Adapter needed |
| OTLP backend compat | Partial (backends need log signal ingestion) |
| Lossless | **Fragile under log sampling** |
| Implementation effort | Medium |

**Pros:** Architecturally cleanest; OTel GenAI spec-aligned; future-proof.
**Cons:** Not deployable today; silently broken under log sampling; backends don't support it yet.

---

### Approach 4 — Delta Spans

Store only messages added since the previous LLM call. Reconstruction walks the parent span chain.

| Dimension | Value |
|---|---|
| Storage complexity | O(N) |
| Wire per LLM call | Delta only |
| Reconstruction | **Fragile** — any sampled or dropped ancestor span breaks reconstruction |
| OpenInference compat | **Breaks** |
| OTel GenAI compat | **Breaks** |
| ATIF compat | Adapter needed |
| NeMo RL SFT compat | Adapter needed |
| OTLP backend compat | **None** |
| Lossless | No — fragile by design |
| Implementation effort | Medium |

**Pros:** O(N) storage.
**Cons:** Fragile reconstruction (sampling kills it); cannot handle message pruning; breaks all standards. Strictly dominated by Approach 5.

---

### Approach 5 — Dual-track Export (recommended)

Two parallel exporters:

**Track A (compatibility):** Standard OpenInference spans with full message content → Phoenix, Langfuse, any OTLP backend. Same as today with cap fixed. Optional: suppress message content via `OPENINFERENCE_HIDE_INPUT_MESSAGES` if external observability tools are not used.

**Track B (native/efficient):** Custom JSONL — a message journal. Each unique message stored once; LLM call records reference messages by hash.

#### Track B Schema (complete)

A `msg` record stores the **full message dict** as its canonical form. The hash covers the complete message:

```jsonl
{"type": "msg", "h": "sha256:3a7f...",
 "msg": {"role": "user", "content": "What is the capital of France?"}}

{"type": "msg", "h": "sha256:bb4c...",
 "msg": {"role": "assistant", "content": null,
         "tool_calls": [{"id": "call_abc123", "function": {"name": "search",
                         "arguments": "{\"q\": \"France capital\"}"}}]}}

{"type": "msg", "h": "sha256:cc9e...",
 "msg": {"role": "tool", "tool_call_id": "call_abc123", "content": "Paris"}}

{"type": "call", "span_id": "abc123", "parent_span_id": "def456",
 "ts_start": 1764857479133, "ts_end": 1764857483785,
 "model": "gpt-4o", "session_id": "20260326_...",
 "input": ["sha256:3a7f...", "sha256:bb4c...", "sha256:cc9e..."],
 "output": ["sha256:dd1c..."],
 "tokens": {"prompt": 1234, "completion": 89, "cached": 0}}
```

**Hash function:** `sha256(json.dumps(msg_dict, sort_keys=True, ensure_ascii=False).encode())`. This covers all fields including `tool_calls`, `tool_call_id`, `name`, and handles null content correctly.

**Note on tool call IDs:** Tool call IDs (`call_abc123`) are ephemeral correlation tokens generated at inference time. They are embedded in the `msg` record content. Reconstruction is exact — the ID in the assistant's `tool_calls` matches the `tool_call_id` in the corresponding tool result. However, this binding makes the data non-portable for *replay scenarios* (re-running the conversation with a different model generates new IDs). This is a known limitation, not a bug.

**Integration point:** Track B cannot be implemented as a `SpanExporter` because messages must be written *before* the LLM call completes (to maintain the "msg before call" ordering invariant). The correct integration point is the `on_messages_built` hook in `_hooks_impl.py`, which fires before the LLM call is made. This is a different architectural layer than the existing OTLP exporter.

**Concurrency:** Under concurrent agent execution, a per-session write lock is required. The `on_messages_built` hook fires in the thread that is building messages for a specific session — per-session JSONL files already avoid cross-session contention. A per-session mutex (or async lock for async agents) is sufficient.

**Partial write resilience:** JSONL appends are not atomic. A `call` record written after a crash that interrupted the preceding `msg` write will have a dangling hash reference. Mitigation: the reader validates that all hashes in `call.input` and `call.output` exist in the file before serving results. Missing hashes are reported as reconstruction errors, not silently ignored.

| Dimension | Value |
|---|---|
| Storage complexity | O(N) Track B; O(N²) Track A (optional, ~100× larger) |
| Wire per LLM call | Full history (Track A) + hashes (Track B, negligible) |
| Reconstruction | **Strong from Track B** — hash join within one file, no ancestry dependency |
| OpenInference compat | Full (Track A) |
| OTel GenAI compat | Full (Track A) |
| ATIF compat | Export adapter from Track B |
| NeMo RL SFT compat | Export adapter from Track B (trivial ~50 lines) |
| OTLP backend compat | Full (Track A) |
| Lossless | Yes — see proof below |
| Implementation effort | Medium-high (hooks integration + explorer rewrite) |

**Pros:** Full compatibility for external tools (Track A); O(N) native storage; simple reconstruction; adapters to ATIF and NeMo RL SFT are straightforward.
**Cons:** Two systems to maintain; Track A still O(N²) if used; explorer requires a significant rewrite (see migration section).

---

## Trade Space Matrix

| | **1 Status Quo+** | **2 CAS+hashes** | **3 Log Events** | **4 Delta Spans** | **5 Dual-track** |
|---|:---:|:---:|:---:|:---:|:---:|
| Storage complexity | O(N²) ~250MB/sess | O(N) ~1.6MB | O(N) | O(N) | O(N) Track B |
| Wire per call | Full | Hashes | Delta | Delta | Full (A) + hashes (B) |
| Lossless guarantee | Strong | Blob-dependent | **Fragile (sampling)** | **Fragile (ancestry)** | Strong |
| OpenInference compat | ✅ | ❌ | ⚠️ partial | ❌ | ✅ |
| OTel GenAI compat | ✅ | ❌ | ✅ | ❌ | ✅ |
| ATIF compat | ✅ | adapter | adapter | adapter | adapter |
| NeMo RL SFT compat | ✅ | adapter | adapter | adapter | adapter |
| OTLP backend compat | ✅ | ❌ | ⚠️ partial | ❌ | ✅ |
| Fixes attribute cap | ✅ (one var) | ✅ | ✅ | ✅ | ✅ |
| Implementation effort | **Trivial** | Medium | Medium | Medium | **Medium-high** |
| Prior art in LLM obs | Universal | None | None (emerging) | None | None |
| Prior art (general) | Universal | Git, IPFS, CQRS | OTel Logs signal | None | — |

---

## Lossless Reconstruction Proof (Track B)

**Claim:** Every LLM call's input and output can be reconstructed exactly from a Track B JSONL file.

**Invariants maintained by the writer:**
1. A `msg` record is written before any `call` record that references its hash (enforced by writing `msg` records in `on_messages_built`, before the LLM call; `call` records written in `on_llm_end`)
2. Every hash in `call.input` or `call.output` corresponds to a `msg` record in the same file
3. The hash is `sha256(json.dumps(msg_dict, sort_keys=True, ensure_ascii=False).encode())` — deterministic over the full message dict

**Reconstruction:**
```python
def reconstruct(call_record, journal_path):
    msg_by_hash = {}
    with open(journal_path) as f:
        for line in f:
            r = json.loads(line)
            if r["type"] == "msg":
                msg_by_hash[r["h"]] = r["msg"]
    # Validate: all hashes must resolve
    for h in call_record["input"] + call_record["output"]:
        if h not in msg_by_hash:
            raise ReconstructionError(f"Missing msg for hash {h}")
    inputs  = [msg_by_hash[h] for h in call_record["input"]]
    outputs = [msg_by_hash[h] for h in call_record["output"]]
    return inputs, outputs
```

**Self-contained:** No external dependencies. The JSONL file contains all content needed.

**Edge cases handled:**
- **Null content (tool-calling turns):** `json.dumps({"role": "assistant", "content": null, "tool_calls": [...]})` is distinct from any other message; no collision.
- **Identical content at multiple positions:** `call.input` may contain the same hash multiple times (e.g., two identical tool results). Order is preserved by the list.
- **Message pruning:** If the agent prunes messages, `call.input` contains fewer hashes. Pruned messages' `msg` records remain in the file but are not referenced. Reconstruction of what the model actually saw is exact.
- **Partial write on crash:** Reader validates all hashes before returning results. Missing hashes raise a `ReconstructionError`; they are not silently ignored.

**What "exact reconstruction" does not mean:**
- It does not enable *replay* — tool call IDs are ephemeral and re-running the conversation generates new ones. The data is an accurate record of what happened, not a portable replay script.
- Multimodal content (images) is not covered by this proof — see Open Questions.

---

## Recommended Migration Path

### Phase 1 (immediate)
- **Fix attribute cap bug:** `SpanLimits(max_span_attributes=2048)` on the `TracerProvider` in `enable_tracing()`. One line.
- **Implement Track B at the hooks layer** (`on_messages_built` + `on_llm_end` in `_hooks_impl.py`). Since the golden path is OTLP HTTP to the viewer, Track B records should be posted to a new viewer endpoint — e.g., `POST localhost:5001/v1/message-journal` — rather than written to a sidecar file. The viewer stores them in SQLite alongside spans (join key: `session_id` + `span_id`). For the file fallback, Track B can be written to a sidecar `.journal` file next to the JSONL.
- **Add viewer endpoint** for Track B ingestion. The viewer needs a new route that accepts Track B `msg` and `call` records and persists them to a new SQLite table.
- **Rewrite trace explorer** to read from the Track B SQLite tables instead of reconstructing messages from flattened OpenInference attributes. This is a significant rewrite of the ~3,800-line `explorer.py` pipeline; the session tree structure must come from `parent_span_id` fields in `call` records. Plan this as a non-trivial undertaking.
- **Export adapters:** `track_b_to_sft(session_id)` — reconstruct `messages[]` per turn, group by `call` records in order. ~50 lines. `track_b_to_atif(session_id)` — similar.

### Phase 2 (when OTel GenAI log-event backends land)
- Migrate Track A from full-content OpenInference spans to OTel GenAI spans + log events. At that point Track A and Track B converge toward the same model.
- If Track B has proven stable, consider retiring Track A or making it opt-in only.

### Phase 3 (optional / future)
- Propose the content-addressing extension upstream to the OTel GenAI SIG as a formal `gen_ai.message.content_ref` attribute.

---

## Open Questions

1. **Does Track A need full message content at all?** If Phoenix/Langfuse are not in use, Track A could use `OPENINFERENCE_HIDE_INPUT_MESSAGES=True` today, eliminating the O(N²) cost entirely. Track B carries everything for the trace explorer and SFT export. This is available now, before any new design work.

2. **Eval pipeline integration.** The viewer stores eval results in SQLite alongside trace sessions. If the trace explorer migrates to Track B, the join key between Track B JSONL files and SQLite eval metadata is `session_id`. The Track B schema already includes `session_id` per `call` record, but the eval tooling needs to be audited to ensure it doesn't assume OTLP JSONL as the source of truth for sessions.

3. **SFT turn labelling.** Track B records which messages the model saw (`call.input`) and what it said (`call.output`), but not which turns to train on, per-turn success labels, or whether to train on every assistant turn vs. only successful ones. The SFT adapter is a few dozen lines of code, but the *labelling strategy* (which turns to include, how to handle tool call round-trips within a turn) is a design decision that Track B deliberately leaves to the adapter. Document this decision at the adapter layer.

4. **Multimodal content.** Binary content (images, audio) should be hashed but referenced by path or base64 in the `msg` record. Very large images would dominate the journal. Consider a separate binary blob sidecar with content-hash filenames, referenced from `msg` records.

5. **Retention and cleanup.** Track B JSONL files accumulate indefinitely (same as today's OTLP JSONL). For eval runs (450 tasks × 30 turns × ~2kB/msg ≈ ~27 MB unique content per run), storage is manageable but needs a policy. Options: (a) ephemeral per-eval-run, deleted post-SFT-export; (b) permanent audit log with configurable TTL. This needs an explicit decision, not a default.

6. **OTel log-event backend readiness.** The Approach 3 (log events) path requires both Phoenix and Langfuse to ingest OTel logs and correlate by `gen_ai.conversation.id`. As of March 2026, neither does this for message display. The `gen_ai.conversation.id` attribute itself is Development status. Monitor: Phoenix GitHub issues and Langfuse changelog for OTel logs ingestion support.
