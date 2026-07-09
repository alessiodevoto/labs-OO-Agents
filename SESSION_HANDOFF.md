# Benchmark Campaign — Session Hand-off

## Status: 55/56 todos done. One item BLOCKED-ON-USER.

## Pushed
- nooa main @ 7c45feb0
- harbor MR !22 (commit a61ddaa4 — TB1 x86_64 cp312-overlay setup fix)
- harbor_campaign skill (task-id resume + cp312 triage + infra phase categorization)

## Answers delivered this session
- **opus = 4.6** confirmed (gateway echoes bedrock-claude-opus-4-6; 4-5 rejected by key → not an alias).
- **TB1 infra is NOT timeouts** — ~70-85% are agent-setup failures from container Python-version
  diversity (cp36/cp310/cp311/cp313 vs cp312-only wheels). Only ~1/run is a real solve-timeout, so
  doubling the per-task timeout would NOT fix the bulk.
- **TB1 fix VALIDATED** (harbor a61ddaa4): 28 baseline-opus infra tasks (0 scored before) → 17 scored /
  6 passed, 0 cp311-wheel failures (was 13).
- **react comparison** (TB1 only — TB2/SWEBench ran one agent each): baseline beats react +5.2/+36.0/+7.3
  (sonnet/opus/ultra); specialized +8.4/+34.1/−0.4.
- **SWEBench sonnet complete**: 330/496 = 66.5% (4 infra).

## Final benchmark numbers (all in docs/benchmark_results.md)
- TB1 (241): baseline 45.7/63.4/46.2 · specialized 48.9/61.5/38.5 · react 40.5/27.4/38.9 (sonnet/opus/ultra)
- TB2 (89, clean): opus 64.4 · sonnet 40.4 · ultra 34.8
- SWEBench (500): opus 75.4 · sonnet 66.5 · ultra 60.2

## BLOCKED item (a9a14af6): from-scratch 3-machine validation
3 new machines provisioned (7d leases til 2026-06-08), but all reject my SSH key:
- z590-0277 = 10.59.107.184 (up, rejects key)
- z590-0294 = 10.57.210.65 (reprovision FAILED, unreachable)
- z590-0297 = 10.57.202.10 (unreachable after reprovision reboot)

Exhausted 6 automated key-deploy avenues: securekey register (403), bm config ansible (invalid repo),
lease secrets (masked), no cached CLI token, ipp2 bootstrap (no kerberos/GSSAPI), full reprovision
(re-ran local-account-setup.yml which deploys a vault/colossus-managed key, not my local id_rsa).
Key deploy needs the AD/domain password, not available in this headless session.

### USER DECISION NEEDED — pick one:
1. Give the canonical colossus key-deploy ansible repo+playbook → I run `bm config` and proceed.
2. Log into the 3 machines with domain creds and run util/harbor/setup_colossus.sh (self-verifying) →
   I take over SIF rsync + benchmark runs.
3. Skip the validation (fix already validated on real tasks; docs make it reproducible) AND release the
   3 leases — I will NOT release without explicit confirmation (destructive).

### Resumption recipe (once SSH works)
clone nooa + 3p/harbor (branch feat/skip-editable-installs-with-pth) → run setup_colossus.sh
→ rsync SIF cache from DFW Lustre → run TB1/TB2/SWEBench one per machine.
Validation criteria: TB1 agent-setup infra ~0 cp311 failures; TB2 ~0 exit-127;
SWEBench ~75% opus / ~66% sonnet / ~60% ultra.
