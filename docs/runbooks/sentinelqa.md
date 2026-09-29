# SentinelQA-lite Runbook

## Boundary

SentinelQA-lite (`nexus.sentinelqa`, ADR 0011) independently verifies a PatchForge
`patch_proposed` result against the pristine specification of the task's source commit.
It makes no model calls, needs no network, runs only operator-profile commands through a
sandbox executor, and never approves, merges, pushes, or edits the source repository.

Its verdict is what Atlas requires before a human may approve a PatchForge job.

## Concepts

| Term | Meaning |
| --- | --- |
| Specification lock | Content digests of every test, evaluation-config, and operator-declared file at the source commit, captured from Git objects, bound to the operator profile hash. |
| Pristine tree | A fresh `.git`-free checkout of the source commit that matches the lock. |
| Verification tree | The candidate with every locked file restored to pristine content and every candidate-added test or config file removed. |
| Verdict | `passed`, `failed` (candidate rejected on evidence), or `inconclusive` (evidence could not be trusted). Only `passed` unlocks approval. |

## Deterministic gates (no Docker, no model)

```bash
python -m pytest -q tests/unit/test_sentinelqa_*.py   # lock, parser, verifier, catalog, Atlas
python -m nexus.sentinelqa                            # adversarial catalog, replayed twice
python -m nexus.patchforge.benchmark_corpus           # Benchmark v0 with SentinelQA verdicts
```

The catalog gate prints one line per scenario (candidate outcome, verdict, finding codes,
run count, verdict hash) and fails on any unexpected verdict, missing or forbidden
finding, invariant violation, or non-identical replay.

## Reviewing a candidate in code

```python
from nexus.sentinelqa import SentinelQAVerifier, capture_specification_lock

lock = capture_specification_lock(git, source_repository, source_sha, profile, captured_at=now)
verifier = SentinelQAVerifier(
    task=task,  # the EngineeringTask PatchForge ran
    profile=profile,  # the operator RepositoryProfile (hash must match the task)
    lock=lock,
    artifact_store=artifact_store,  # where PatchForge stored the patch artifact
    sandbox=sandbox,  # DockerSandbox in production
    git=git,
    clock=clock,
    review_id=review_id,
    work_root=work_root,  # caller-owned, disposable
)
verdict = verifier.review(patch_result, source_repository)
review = verdict.to_atlas_review(
    artifact_store.put(canonical_json(verdict).encode(), artifact_type="sentinelqa_verdict")
)
```

Capture the lock before PatchForge runs when you can; it is also re-derived from the
source commit at review time and both must agree.

## Reading a verdict

- `verdict`: `passed` / `failed` / `inconclusive`.
- `findings`: typed codes with a category (`rejection`, `unverifiable`, `advisory`), a
  detail, and the path concerned. Advisory findings (`reproduction_not_demonstrated`,
  `candidate_tests_excluded`) never decide the verdict.
- `integrity`: locked entry counts, modified/deleted/non-regular paths, evaluation-config
  changes, candidate-added tests that were excluded, restored paths, and what the
  candidate still changes once the specification is restored.
- `runs`: up to five attested executions (pristine reproduction, targeted, full suite;
  verification targeted, full suite) with command and policy hashes, status, parsed
  counts, and tree fingerprints before and after.
- `patchforge_checks_agree`: whether PatchForge's passing checks reproduced independently.

## Finding codes

| Code | Category | Cause |
| --- | --- | --- |
| `specification_modified` / `specification_deleted` / `specification_not_regular_file` | rejection | A locked test or data file changed, disappeared, or became a symlink/directory. |
| `evaluation_config_altered` | rejection | `conftest.py`, `pytest.ini`, `.pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`, `sitecustomize.py`, `usercustomize.py`, or a `.pth` file was added, changed, or removed anywhere. |
| `protected_path_changed` / `sensitive_path_changed` | rejection | Defense in depth over the operator profile and sensitive-path rules. |
| `patch_apply_failed` | rejection | The patch does not apply to its own base commit. |
| `pristine_tests_failed` | rejection | The pristine targeted or full suite fails on the verification tree. |
| `tests_skipped` / `tests_missing` | rejection | More skips/xfails than the pristine baseline; fewer accounted tests, more deselections, or no tests at all. |
| `verification_tree_mutated` | rejection | A verification run changed the tree. |
| `independent_validation_disagrees` | rejection | PatchForge attested a passing check that fails independently. |
| `unrecorded_change` | rejection | The patch changes a path the attested diff does not list. |
| `no_code_change` | rejection | Restoring the specification leaves nothing of the candidate. |
| `not_a_proposal` / `identity_mismatch` / `pristine_reference_unverifiable` / `evidence_incomplete` / `executor_unavailable` | unverifiable | Nothing to verify, mismatched bindings, an untrustworthy source or lock, missing or contradictory evidence, or no executor. |

## Operator guidance

- Keep every file a test reads under a test prefix or list it in the lock's additional
  paths; anything else is treated as code the candidate may change.
- Tests that write inside the repository tree make runs non-attestable
  (`verification_tree_mutated` / `evidence_incomplete`). Direct such output elsewhere.
- The runner reads counts from pytest's final summary line. Commands that suppress it, or
  plugins that add unknown summary tokens, yield `evidence_incomplete`.
- Candidate-added tests are excluded from verification. They may still be valuable; review
  them as part of the human approval, not as evidence of correctness.

## Not yet built

Docker integration proof of the SentinelQA runner (the E2E and benchmark gates use a
scripted or oracle sandbox), per-test-identifier tracking, review of candidate-added
tests, requirements-to-tests, browser/API evidence, and the selector/DOM benchmark.
