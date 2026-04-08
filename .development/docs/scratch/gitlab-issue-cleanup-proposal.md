# GitLab Issue Cleanup Proposal

**Date:** 2026-01-21
**Total Issues:** 79 (36 open, 43 closed)

## Current State Analysis

### Issue Statistics
| Status | Count |
|--------|-------|
| Open | 36 |
| Closed | 43 |
| Unlabeled (open) | ~25 |

### Current Label System (26 labels)

| Category | Labels | Usage |
|----------|--------|-------|
| **Type** | `Type::Bug`, `Type::Enhancement`, `Type::Refactor` | Low |
| **Type (duplicate)** | `bug`, `enhancement` | Very Low |
| **Component** | `Component::Agent`, `Component::Capability test`, `Component::Prompt Optimization`, `Component::Simplification`, `Component::Tracing`, `Component::Transcription` | Medium |
| **Experience** | `Experience::AX`, `Experience::DX`, `Experience::UX` | Low |
| **Priority** | `P::Showstopper`, `P::0`, `P::1`, `P::2` | Not Used |
| **Progress** | `Progress::Blocked`, `Progress::In Progress`, `Progress::In Review`, `Progress::Todo` | Not Used |
| **Other** | `automated`, `discussion`, `documentation`, `test` | Very Low |

### Problems Identified

1. **Duplicate labels**: `bug` vs `Type::Bug`, `enhancement` vs `Type::Enhancement`
2. **Unused labels**: Priority and Progress labels are never used
3. **Inconsistent labeling**: ~70% of open issues have no labels
4. **Test issues cluttering history**: 8 closed test issues that could be deleted
5. **Duplicate issues**: #72 and #73 are identical

---

## Issues to Delete (Test/Junk)

These closed issues can be **deleted** to clean up history:

| ID | Title | Reason |
|----|-------|--------|
| #75 | TPM agent is cool | Test/joke issue |
| #74 | Verify that the Traces for TPM are properly visible | Test issue (closed) |
| #69 | Severin wanted another test issue | Test issue |
| #62 | Test from TPM | Test issue |
| #61 | Test from TPM | Test issue (duplicate) |
| #60 | [TEST] Integration test issue - please ignore | Test issue |
| #36 | test issue | Test issue |
| #33 | Test issue from CLI | Test issue |
| #32 | Test Issue - Permission Check | Test issue |

**Total: 9 issues to delete**

---

## Duplicate Issues to Merge

| Keep | Close as Duplicate |
|------|-------------------|
| #73 Bug: Extracting baseline performance for capability tests | #72 (identical) |

---

## Proposed Label Strategy

### Labels to Keep (Simplified)

#### Type Labels (Required - every issue should have one)
| Label | Color | Description |
|-------|-------|-------------|
| `Type::Bug` | Red (#dc143c) | Something is broken |
| `Type::Enhancement` | Green (#009966) | New feature or improvement |
| `Type::Refactor` | Gray (#808080) | Code cleanup, no behavior change |
| `Type::Discussion` | Blue (#6699cc) | Design discussion, RFC |

#### Component Labels (Optional - helps filtering)
| Label | Color | Description |
|-------|-------|-------------|
| `Component::Core` | Yellow (#eee600) | Core framework (runtime, strategies) |
| `Component::Tracing` | Yellow (#eee600) | OpenTelemetry, traces |
| `Component::Evaluation` | Yellow (#eee600) | Benchmarks, capability tests |
| `Component::Experiments` | Yellow (#eee600) | Ablations, experiments |
| `Component::Agents` | Yellow (#eee600) | TPM, Librarian, etc. |
| `Component::Context-Blocks` | Yellow (#eee600) | Context setting and rendering |
| `Component::Agentdoc` | Yellow (#eee600) | doc() introspection tool for agents |
| `Component::UnifiedLLM` | Yellow (#eee600) | LLM client abstraction layer |
| `Component::Viewers` | Yellow (#eee600) | Trace viewer, eval viewer |
| `Component::Docs` | Yellow (#eee600) | Library documentation, examples |

#### Priority Labels (Use for planning)
| Label | Color | Description |
|-------|-------|-------------|
| `P::Showstopper` | Pink (#ff00bb) | Unbreak now, production on fire |
| `P::0` | Red (#ff4500) | Top priority |
| `P::1` | Orange (#ffa500) | Should do soon |
| `P::2` | Yellow (#ffd700) | Whenever we get to it |

### Labels to Delete
- `bug` (duplicate of Type::Bug)
- `enhancement` (duplicate of Type::Enhancement)
- `discussion` (merge into Type::Discussion)
- `documentation` (merge into Component::Docs)
- `automated` (not useful)
- `test` (not useful)
- `Progress::*` (use GitLab boards instead)
- `Component::Simplification` (historical, no longer relevant)
- `Component::Agent` (too vague, use Component::Agents)
- `Component::Transcription` (too specific, use Component::Agents)
- `Experience::AX`, `Experience::DX`, `Experience::UX` (not needed for small team)

---

## Open Issues Triage

### High Priority (Should label and track)

| ID | Title | Suggested Labels |
|----|-------|------------------|
| #79 | agentdoc: Inconsistent doc() output | Type::Bug, Component::Agentdoc |
| #77 | feat: Add call-level LLM override support | Type::Enhancement, Component::Core |
| #73 | Bug: Extracting baseline performance | Type::Bug, Component::Evaluation |
| #64 | Bug: CodeActStrategy hangs on non-existing tool | Type::Bug, Component::Core |
| #40 | gpt-oss-20b: Empty content when reasoning exhausts budget | Type::Bug, Component::Core |
| #39 | Incomplete trace files on timeout | Type::Bug, Component::Tracing |
| #38 | Traces don't capture reasoning content | Type::Bug, Component::Tracing |

### Medium Priority (Good backlog items)

| ID | Title | Suggested Labels |
|----|-------|------------------|
| #76 | Track LLM judge traces in eval pipeline | Type::Enhancement, Component::Evaluation |
| #71 | Implement Memory-track benchmarks | Type::Enhancement, Component::Evaluation |
| #70 | WP-9: Agent Framework Comprehension | Type::Enhancement, Component::Evaluation |
| #68 | Fix & improve SWE benchmark | Type::Enhancement, Component::Evaluation |
| #67 | Deploy eval viewer to track progress | Type::Enhancement, Component::Evaluation |
| #63 | Fix & improve Tau benchmark | Type::Enhancement, Component::Evaluation |
| #57 | WP-8: Do Agents Get Confused by Nested Calls? | Type::Enhancement, Component::Evaluation |
| #54 | Add capability test for large input parameters | Type::Enhancement, Component::Evaluation |
| #53 | Deploy an Agent006 based agent on Astra | Type::Enhancement, Component::Agents |
| #47 | WP-3 Context Block Manipulation | Type::Enhancement, Component::Evaluation |
| #43 | Add max_tokens support to @plan decorator | Type::Enhancement, Component::Core |
| #42 | Get an OTel collector on Astra | Type::Enhancement, Component::Tracing |
| #41 | No LLM retries on plan method error | Type::Bug, Component::Core |
| #35 | Move FakeLLMClient to unifiedllm package | Type::Refactor |

### Lower Priority / Discussion

| ID | Title | Suggested Labels |
|----|-------|------------------|
| #66 | Code review of CodeAct strategy | Type::Discussion |
| #58 | Discussion: Scoped documentation and runtime enforcement | Type::Discussion |
| #37 | Design: Block rendering in task messages vs system prompt | Type::Discussion |
| #34 | Create issue for TPMAgent to read meeting transcriptions | Type::Enhancement, Component::Agents |
| #30 | Integrate Teams meeting transcription with TPM agent | Type::Enhancement, Component::Agents |
| #29 | Need a set of tasks that put the Agent through its paces | Type::Enhancement |
| #21 | Many type hints are not useful or consistent | Type::Refactor |
| #20 | Evaluate exec() and safety | Type::Discussion |
| #19 | Need a logging concept in the framework | Type::Enhancement, Component::Core |
| #5 | [DX] Test breakpoint() to PDB | Type::Enhancement, Experience::DX |
| #4 | [Feat] Implement checkpointing and time-travel debugging | Type::Enhancement |

---

## Recommended Actions

### Immediate (Do Now)
1. Delete 9 test issues listed above
2. Close #72 as duplicate of #73
3. Delete unused labels: `bug`, `enhancement`, `discussion`, `documentation`, `automated`, `test`
4. Delete `Progress::*` labels (use GitLab boards/milestones instead)

### Short Term (This Week)
1. Create missing component labels: `Component::Core`, `Component::Evaluation`, `Component::Experiments`, `Component::Context-Blocks`, `Component::Agentdoc`, `Component::UnifiedLLM`, `Component::Viewers`, `Component::Docs`
2. Add `Type::Discussion` label
3. Apply labels to all 36 open issues per triage above
4. Review and potentially close stale issues (1+ month old with no activity)

### Ongoing
- Every new issue must have at least one Type:: label
- Use milestones for sprints/releases instead of Progress:: labels
- Use GitLab boards for kanban-style tracking

---

## Commands to Execute Cleanup

```bash
# Delete test issues (run one by one, confirm each)
glab issue delete 75 --yes
glab issue delete 74 --yes
glab issue delete 69 --yes
glab issue delete 62 --yes
glab issue delete 61 --yes
glab issue delete 60 --yes
glab issue delete 36 --yes
glab issue delete 33 --yes
glab issue delete 32 --yes

# Close duplicate
glab issue close 72 --yes

# Delete unused labels (via GitLab UI - no CLI support)
# Labels to delete: bug, enhancement, discussion, documentation, automated, test
# Progress::Blocked, Progress::In Progress, Progress::In Review, Progress::Todo
# Component::Simplification, Component::Agent, Component::Transcription
# Experience::AX, Experience::DX, Experience::UX

# Add labels to high priority bugs
glab issue update 79 --label "Type::Bug,Component::Agentdoc"
glab issue update 73 --label "Type::Bug"
glab issue update 40 --label "Type::Bug"
glab issue update 39 --label "Type::Bug,Component::Tracing"
glab issue update 38 --label "Type::Bug,Component::Tracing"
```
