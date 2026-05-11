# issue-187: build & publish versioned packages on every main commit

GitLab issue: https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/187

## Goal

Internal consumers cannot `uv add` the latest main without waiting for a tag.
Extend CI to publish wheels for every main push, with PEP 440 dev versions
that sort above the latest stable, so `--pre` consumers get the freshest dev.

Adopt the [`uv-dynamic-versioning`](https://github.com/ninoseki/uv-dynamic-versioning)
recipe linked in the issue — version derives from `git describe` at build
time, no manual bumps in pyproject.toml ever.

## Approach

`uv-dynamic-versioning` is a hatch plugin that wraps
[`dunamai`](https://github.com/mtkennerly/dunamai). With:

```toml
[build-system]
requires = ["hatchling>=1.26.0", "uv-dynamic-versioning>=0.14.0"]
build-backend = "hatchling.build"

[project]
dynamic = ["version"]

[tool.hatch.version]
source = "uv-dynamic-versioning"

[tool.uv-dynamic-versioning]
vcs = "git"
style = "pep440"
bump = true            # past tag → X.Y.(Z+1).dev<distance>
metadata = false       # drop +g<sha> local segment (PyPI-friendly)
fallback-version = "0.2.0"
```

each `uv build` invocation runs `git describe`, parses the nearest `vX.Y.Z`
tag, and stamps the resulting version into the wheel:

| Repo state | Derived version |
|---|---|
| Exactly on tag `v0.3.0` | `0.3.0` |
| N commits past `v0.3.0` | `0.3.1.devN` |
| No reachable tag | `0.0.1.dev<commits-from-root>` (fallback only fires when git is unavailable, e.g. building from sdist) |

PEP 440 sort: `0.3.0 < 0.3.1.dev5 < 0.3.1`. Consumers with `--pre` see
fresh dev; without it they keep getting the last stable release.

## Cross-package deps

`benchmarks` and `cli` declare a static `>=` floor on `nemo-oo-agents`:

```toml
"nemo-oo-agents>=0.2.0",
```

This expresses the maintainer's compat judgment ("this cli still works with
core 0.2.0+"). When that judgment changes (cli starts using a new core API),
bump the floor in a normal MR, just like any other third-party dep.

No CI rewrite to `==<exact>`. Rejected because:

- It throws away the maintainer's compat info in favor of a coarse "always
  exact" rule.
- Co-release is already guaranteed by CI building all three packages from
  the same commit; what's NOT guaranteed is what consumers install later,
  and `==<exact>` doesn't add anything the floor doesn't already say (a
  reckless consumer can still pin a specific older `nemo-oo-agents`
  separately).

## Files changed

1. `pyproject.toml` — backend → `hatchling.build`; add
   `[tool.uv-dynamic-versioning]` + `[tool.hatch.version]` + wheel target;
   `version = ".."` → `dynamic = ["version"]`.
2. `packages/nemo-oo-agents-benchmarks/pyproject.toml` — same change.
3. `packages/nemo-oo-agents-cli/pyproject.toml` — same change.
4. `.gitlab-ci.yml` — delete ~80 lines of version-derivation and sed
   rewrites; build/publish jobs just run `uv build` + `uv publish` with
   `GIT_DEPTH: "0"` so `git describe` has full history. Rules unchanged
   (tag end-anchored, main-push only).
5. `RELEASING.md` — shrink to "tag a commit and that's it".
6. `uv.lock` — regenerated (static `version` fields removed from workspace
   entries, hatchling pulled in transitively).

## Edge cases

- **No tags exist yet.** Until the first `vX.Y.Z` tag is cut, dev publishes
  use the fallback-with-distance shape (`0.0.1.dev<N>`). Bootstrapping the
  release flow is a one-time `git tag v0.2.0` after this MR merges.
- **Workspace lockfile churn.** Switching backends changes how uv expresses
  the workspace in `uv.lock`. The diff is small (removed `version =` lines,
  expanded extra references) and shouldn't affect installs.
- **Tag pipelines + main-push pipelines.** Both call the same `uv build`;
  dunamai handles both cases (exact tag vs distance-from-tag) without any
  CI-side conditionals.
- **GitLab shallow clone.** GitLab CI defaults to shallow clones, which
  break `git describe`. The build jobs set `GIT_DEPTH: "0"` to disable.
- **Publish idempotency.** Unchanged — `uv publish` 400s on duplicate
  version, which is the right safe-fail. Republish via empty commit so the
  derived version advances.

## Test strategy

CI YAML can't be unit-tested. Validation plan:

1. `glab ci lint` — YAML valid.
2. `uv sync --all-extras` — workspace installs cleanly with new backend.
3. `uv build` on each workspace package — produces wheels with the
   dunamai-derived version stamped in METADATA.
4. Validated locally:
   - Build with no tags → `0.0.1.dev917`.
   - `git tag -a v0.5.0 HEAD && uv build` → `0.5.0`.
   - `git tag -a v0.4.0 HEAD~5 && uv build` → `0.4.1.dev20` (with
     `bump = true`).
   - cli wheel METADATA shows `Requires-Dist: nemo-oo-agents>=0.2.0` (no
     auto-pin; matches pyproject.toml exactly).

End-to-end on the real GitLab Package Registry: temp-allow this branch to
publish, push, verify three wheels at the derived version appear in the
registry, verify `uv add` resolves cleanly. Then revert the temp allow
before merge.

## Rollout / rollback

- Rollout: merge MR. Next push to main publishes a `0.0.1.dev<N>` wheel.
  Cut the first real tag (`git tag v0.2.0 && git push origin v0.2.0`) to
  bootstrap the release flow and switch dev publishes to `0.2.1.dev<N>`.
- Rollback: revert MR. Pre-existing tag-based publish (with sed rewrites)
  restored. Dev wheels already in the registry remain; harmless.
