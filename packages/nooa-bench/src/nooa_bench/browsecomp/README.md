# BrowseComp-Plus baseline for nooa-bench

Deep-research agent evaluation over BrowseComp-Plus (arXiv 2508.06600). A
minimal nooa `CodeActStrategy` agent with two research methods (`search`,
`get_document`) and a swappable retriever backend. No context management yet.

Upstream: <https://github.com/texttron/BrowseComp-Plus>

## Package layout

```
packages/nooa-bench/src/nooa_bench/browsecomp/
  dataset.py       load_records / BrowseCompRecord (830 queries, each with inline gold_docs)
  retriever.py     Retriever protocol; OracleRetriever + BM25Retriever
  agent.py         BrowseCompAgent (nooa.Agent, CodeActStrategy) + BrowseCompAnswer
  grader.py        HeuristicGrader (substring) + LLMJudgeGrader (upstream prompt)
  runner.py        evaluate() / evaluate_async()
  quickstart.py    CLI entry: python -m nooa_bench.browsecomp.quickstart
```

## One-time setup

### 1. Java 21 (for Pyserini)

```bash
brew install openjdk@21
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export PATH="$JAVA_HOME/bin:$PATH"
```

Add the two exports to your shell rc if you'll run this repeatedly.

### 2. Python deps

```bash
uv pip install datasets pyserini
uv pip install -U transformers   # see "Known issues" below
```

### 3. Data (~4 GB total)

Default location is `~/.cache/nooa-bench/browsecomp/` (override with
`$NOOA_BENCH_BROWSECOMP_DIR`). You need HF login: `hf auth login`.

```bash
mkdir -p ~/.cache/nooa-bench/browsecomp
cd ~/.cache/nooa-bench/browsecomp

# Queries + qrels + gold answers (decrypt canary is public, ships in upstream repo)
git clone --depth=1 https://github.com/texttron/BrowseComp-Plus /tmp/bcp
python3 /tmp/bcp/scripts_build_index/decrypt_dataset.py \
    --output ./browsecomp_plus_decrypted.jsonl \
    --generate-tsv ./queries.tsv

# BM25 index (~2 GB, Pyserini Lucene)
hf download Tevatron/browsecomp-plus-indexes \
    --repo-type=dataset --include="bm25/*" --local-dir ./indexes

# Optional: raw 100K-doc corpus. Only needed if you plug in a dense retriever
# that doesn't store raw text. BM25 index has raw text inline.
# python3 -c "from datasets import load_dataset; \
#   load_dataset('Tevatron/browsecomp-plus-corpus', split='train').save_to_disk('./corpus')"
```

Verify:

```bash
wc -l ~/.cache/nooa-bench/browsecomp/browsecomp_plus_decrypted.jsonl   # 830
ls ~/.cache/nooa-bench/browsecomp/indexes/bm25 | head                   # Lucene segment files
```

## Running

### Smoke test (1 query, ~3 min)

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export PYTHONPATH=packages/nooa-bench/src
export OPENAI_BASE_URL=https://inference-api.nvidia.com/v1/
export OPENAI_API_KEY=<your-key>

python3 -m nooa_bench.browsecomp.quickstart \
    --model openai/nvidia/qwen/qwen3.6-27b \
    --retriever bm25 \
    --limit 1 \
    --output /tmp/browsecomp_smoke.json
```

Expected on query 769:

```
accuracy=100.00%  errors=0  n=1
  OK  qid=769  gold='Queen Arwa University'  got='Queen Arwa University'
```

### Broader subset (10-20 queries)

Each query takes ~2-4 min single-threaded because the CodeAct loop issues
~20 LLM calls (search → read → refine). Parallelise cautiously — the NVIDIA
endpoint's rate limits are undocumented from our side, so start at 3.

```bash
python3 -m nooa_bench.browsecomp.quickstart \
    --model openai/nvidia/qwen/qwen3.6-27b \
    --retriever bm25 \
    --limit 20 \
    --concurrency 3 \
    --output ~/browsecomp_bm25_20q.json
```

Or a hand-picked set by ID:

```bash
python3 -m nooa_bench.browsecomp.quickstart \
    --model openai/nvidia/qwen/qwen3.6-27b \
    --retriever bm25 \
    --query-ids 769,770,771,772,773 \
    --output ~/browsecomp_bm25_5q.json
```

### Sanity: oracle baseline (no BM25 needed)

Confirms the plumbing + grader without exercising retrieval quality. Oracle
returns the record's `gold_docs` directly, so a working LLM should hit 100%.

```bash
python3 -m nooa_bench.browsecomp.quickstart \
    --model openai/nvidia/qwen/qwen3.6-27b \
    --retriever oracle \
    --limit 5 \
    --output ~/browsecomp_oracle_5q.json
```

## Output format

Each run writes JSON with per-query trace:

```json
{
  "n": 5,
  "accuracy": 0.6,
  "error_count": 0,
  "per_query": [
    {
      "query_id": "769",
      "query": "...",
      "gold_answer": "Queen Arwa University",
      "response": "Explanation: ...\nExact Answer: ...\nConfidence: 85%",
      "extracted_answer": "Queen Arwa University",
      "correct": true,
      "result": {
        "exact_answer": "...",
        "explanation": "...",
        "evidence_docids": ["5412", "82002"],
        "confidence": 85
      }
    }
  ]
}
```

## Grader notes

Default is `HeuristicGrader` — normalized substring match on the `Exact
Answer:` line. Cheap, but will slightly *over*-count vs. upstream's
LLM-as-judge rubric. For leaderboard-comparable numbers, wire in
`LLMJudgeGrader(judge=callable)` where `judge(prompt) -> str` calls an LLM
with `grader.GRADER_TEMPLATE` (kept verbatim from upstream).

Not yet exposed via the CLI — needs ~10 lines to add `--grader-model` and
build the callable. Do this before publishing any numbers.

## Known issues

### tokenizers version regression

Some project installs pull `tokenizers==0.23.1` but the pinned
`transformers==5.12.1` requires `<=0.23.0`. Fix: `uv pip install -U
transformers` (bumps tokenizers to 0.22.2). If Pyserini imports break with an
`ImportError: tokenizers>=0.22.0,<=0.23.0 is required...` message, rerun that
command.

### `pyserini.search.lucene.__init__` pulls transformers

`BM25Retriever` imports `pyserini.search.lucene._searcher.LuceneSearcher`
directly — going through the package's `__init__` triggers the
`_impact_searcher` → `transformers` chain, which fails intermittently (see
above). Comment in `retriever.py` explains the workaround.

### litellm needs the `openai/` prefix

The nooa `UnifiedLLM` layer routes through litellm. For any
OpenAI-compatible endpoint (including the NVIDIA inference API), prefix
the model with `openai/`:

- `--model nvidia/qwen/qwen3.6-27b`          → **fails** (litellm can't route)
- `--model openai/nvidia/qwen/qwen3.6-27b`   → works

### Java 21 required (not 17 or 22)

Pyserini's Lucene bindings only accept 21. `openjdk@22` won't work; older
`openjdk@17` won't either.

## Next steps if picking this up later

1. **LLM-as-judge grader in the CLI** — thin wrapper; ~half a day. Needed
   before any numbers get reported externally.
2. **Real BM25 baseline on 50-100 queries** — enough to compare to
   upstream's leaderboard row for BM25 + qwen.
3. **Wire `context-evict-api`** — this is the actual point of running the
   benchmark from a nooa perspective. Add it as a `CodeActConfig`
   parameter (or a strategy variant) and sweep accuracy vs. context
   length. Should slot in cleanly since BrowseCompAgent already returns
   structured output and the runner already collects per-query traces.
4. **Dense retriever** — download `qwen3-embedding-0.6b` index (~2 GB;
   the 8B version is too heavy for a laptop), implement `DenseRetriever`
   in `retriever.py`. Roughly a day. Useful for a retrieval-quality
   sweep vs. BM25.

## Repro state as of this session

- Worktree: `.claude/worktrees/bench-feasibility` on branch
  `worktree-bench-feasibility` (nothing committed).
- Verified end-to-end: `--limit 1 --retriever bm25` on qid 769 →
  100% accuracy, ~3 min wall-clock, agent cited docids
  `5412, 82002, 86190` (all in the gold set).
- Untested: `--concurrency > 1`, `oracle` retriever with a real LLM,
  runs > 1 query end-to-end.
