# ADR 0011: SentinelQA-lite Pristine-Specification Verification

## Status

Accepted

## Context

Benchmark v0 (ADR 0010, Milestone H) exposed a failure mode: when a PatchForge profile
permits test changes, PatchForge proposes patches that also weaken the specification
tests (`fix_and_edit_tests`: 5/5 proposals). PatchForge's attestor lists the test changes
but cannot judge them, because inside PatchForge "the tests pass" is defined by whatever
tests are in the workspace. Only the benchmark's hidden ground truth rejected those
proposals, and ground truth does not exist for real tasks.

Atlas (ADR 0009) already requires a `ReviewResult` with verdict `passed` before a human
may approve a job. What was missing is the reviewer: an independent verifier that decides
against the *original* specification and cannot be redefined by the candidate.

## Decision

Build `nexus.sentinelqa` (Milestone J, SentinelQA-lite) as a runtime-owned verifier with
no model, no ground truth, and no trust in PatchForge's conclusions.

### The pristine reference is a specification lock

`SpecificationLock` is captured from the Git objects of the exact source commit, never
from a working tree, before or independently of any engineering run. It binds:

- the repository URL, source commit SHA, operator profile identity and canonical hash,
  and the profile's test path prefixes;
- one `SpecificationEntry` (path, kind, mode, SHA-256, size) for every file that counts as
  specification: files under the test prefixes (`test`), evaluation configuration
  anywhere in the tree (`conftest.py`, `pytest.ini`, `.pytest.ini`, `tox.ini`,
  `setup.cfg`, `pyproject.toml`, `sitecustomize.py`, `usercustomize.py`, `*.pth`), and
  operator-declared extra paths (`operator`);
- a `lock_sha256` identity that excludes the capture time.

Symbolic links and submodules inside the specification set cannot be locked; capture
fails closed. The Git commit SHA is the cryptographic anchor: SentinelQA re-derives the
lock from a fresh clone at that SHA and requires the entries to be identical.

### Verification uses a fresh tree with the pristine specification restored

`SentinelQAVerifier.review(PatchResult, source)`:

1. requires a `patch_proposed` result whose task, profile, lock, and diff base all bind to
   the same source commit (`identity_mismatch` otherwise);
2. reads the patch from the content-addressed artifact store and checks its hash against
   the attested diff (`evidence_incomplete` otherwise);
3. materializes two `.git`-free trees of the source commit from a bare clone, outside any
   PatchForge workspace, and proves the pristine tree matches the lock
   (`pristine_reference_unverifiable` otherwise);
4. runs the operator's reproduction, targeted, and full-suite commands on the pristine
   tree as a baseline;
5. applies the patch to the second tree, recomputes the changed paths the way PatchForge
   does, and compares them with the attested list (`unrecorded_change`,
   `evidence_incomplete`);
6. compares every locked file with the lock (`specification_modified`,
   `specification_deleted`, `specification_not_regular_file`,
   `evaluation_config_altered`), and re-checks protected and sensitive paths;
7. builds the **verification tree**: the candidate with every locked file restored to its
   pristine content and mode, and every candidate-added test or configuration file
   removed. Candidate-added tests are never evidence (`candidate_tests_excluded`). If
   nothing of the candidate remains, it changed only the specification
   (`no_code_change`);
8. runs the targeted and full-suite commands on the verification tree, fingerprinting the
   tree before and after (`verification_tree_mutated`);
9. applies count rules against the pristine baseline, using only the runner's own summary
   line: zero failures and errors (`pristine_tests_failed`), no more skips or xfails than
   the baseline (`tests_skipped`), at least as many accounted tests and no more
   deselections (`tests_missing`);
10. cross-checks PatchForge's attested targeted and full-suite checks: the same command
    hashes must have run, and a passing PatchForge check that fails independently is
    `independent_validation_disagrees`.

### Verdicts fail closed

Findings carry a category that decides the verdict: any `unverifiable` finding makes the
review `inconclusive`; otherwise any `rejection` finding makes it `failed`; only a clean
review is `passed`. `SentinelVerdict` enforces this in its contract, and a `passed`
verdict additionally requires an intact specification, both verification runs passed on
an unchanged tree with at least one test, and the candidate's patch and head identities.

Unverifiable evidence includes: not a proposal, identity mismatch, an unreadable or
mismatched artifact, a source commit that cannot be materialized or does not match the
lock, an executor that cannot run, a run that timed out or hit an output limit, a run
with no parseable test summary, a passing exit status with reported failures, and a
pristine run that mutates its tree.

`SentinelVerdict.to_atlas_review` produces Atlas' `ReviewResult` for reviewer
`sentinelqa.reviewer`, citing the content-addressed verdict artifact as evidence. Atlas
already permits human approval only when that verdict is `passed`; SentinelQA can never
approve.

### The harness itself is part of the specification boundary (0.25.x)

Two evasions do not touch a locked file and were added to the rejection vocabulary. A
module or package that would shadow the runner on the import path (`pytest.py`,
`_pytest/`, `pluggy/`, `unittest.py`, `site-packages/`, ...) counts as evaluation
configuration, so `evaluation_config_altered` fires wherever it appears. Code under test
that reaches into the runner is `harness_tampering`: SentinelQA scans the added lines of
the patch outside tests and configuration for imports of the harness, `sys.modules`
edits, `__import__` replacement, plugin registration, `PYTEST_*` reads, assertion-rewrite
hooks, tracers, and `sys.excepthook` rebinding. A skip marker added to a pristine test
remains `specification_modified`. The adversarial catalog carries a scenario for each.
What SentinelQA-lite still cannot see is in-process manipulation that leaves counts and
outcomes intact without any of these markers; the Docker runner proof and out-of-process
oracles remain deferred.

### Execution reuses the sandbox boundary

`SpecificationRunner` executes only operator-profile commands through any
`SandboxExecutor` (Docker in production; scripted `FakeSandbox` or the benchmark's
content oracle in deterministic gates) under the operator's sandbox policy, with review-
bound request identities and at most five runs per review. Test counts come from
pytest's summary line via a strict parser that returns nothing rather than guess.

### Gates

- `python -m nexus.sentinelqa` runs the adversarial catalog twice and requires the
  expected verdict, expected findings, no forbidden findings, universal invariants (only
  operator commands and policy, no network or secrets, untouched source repository,
  fail-closed verdicts), and byte-identical replay. The catalog covers honest fixes,
  candidate-added tests, test modification, weakening, deletion, renaming, moving out of
  the prefixes, expected-value manipulation, `conftest.py`/`pyproject.toml`/
  `sitecustomize.py` injection, skips and non-collection caused by code, a candidate that
  passes only with its own tests, a mutated verification tree, rewritten source history,
  tampered or incomplete locks, a lock for another profile, corrupted artifacts, an
  unavailable executor, a missing summary, a timeout, a pristine run that mutates its
  tree, and a passing status with reported failures.
- Benchmark v0 now reviews every proposal with SentinelQA and records whether the
  independent verdict agrees with the hidden ground truth. The gate requires
  `reference` 5/5 passed and `fix_and_edit_tests` 5/5 failed with
  `specification_modified`, with zero inconclusive verdicts and zero disagreements.
- Unit tests pin the calculator fixture's lock identity and cover forged PatchForge
  evidence (protected paths, unrecorded changes, symlinked tests, non-applying patches,
  lying artifact stores, task-binding mismatches) and the Atlas approval gate.

## Consequences

- A candidate can no longer redefine "correct": the specification is fixed by content
  hash at the source commit, and only that specification is evidence.
- PatchForge's `allow_test_file_changes` becomes a workflow choice rather than a safety
  boundary; SentinelQA rejects specification changes regardless of the PatchForge policy.
- SentinelQA judges the candidate's code against the pristine tests only. Reviewing the
  quality of tests a candidate adds, requirements-to-tests, browser or API evidence,
  reproduction reports, and the selector/DOM benchmark remain future SentinelQA work.
- Count rules depend on the runner's summary; per-test-identifier tracking, mutation
  testing of the specification, and a Docker integration proof of the runner are
  deferred. Real repositories whose tests legitimately write into the tree need an
  operator profile that keeps such output outside the tree.
- The first live PatchForge run is now justified only as a SentinelQA-reviewed run: the
  live path evaluates every proposal through SentinelQA and prints its verdict.
