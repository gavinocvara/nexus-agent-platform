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
The marker scan alone did not close the in-process gap; the next section does, for
manipulation that is not aware of SentinelQA.

### Runner integrity: a planted failure the runner must report (0.27.0)

**Problem, demonstrated.** The candidate's code runs in the runner's process. Against a
real pytest (local-process sandbox, calculator fixture, defect left in place), three
candidates received a `passed` verdict from 0.26.0 without touching a locked file or a
scanned marker: an `atexit` handler that prints `1 passed` and calls `os._exit(0)`;
`sys.modules.get("_pytest.reports")` patching every report to `passed`; and
`_pytest.python.Function.runtest` wrapped to swallow exceptions. Summary, exit status, and
counts were all consistent, so no evidence-consistency rule could object.

**Alternatives considered.**

1. *Extend the marker scan* (`os._exit`, `atexit`, `sys.modules.get`, `gc`, stack
   inspection, string literals naming the harness). Rejected as the primary defense: each
   pattern has a trivial spelling around it (`getattr`, `importlib`, encoded strings), and
   several have legitimate uses in the code the resident engineer maintains, which builds
   pytest commands itself.
2. *A structured report channel* (JUnit XML or a trusted plugin writing a nonce-bound
   result file). Rejected: anything the runner process writes, the candidate's code in the
   same process can write too, and a nonce held in that process is readable by it. It also
   requires changing operator commands, which SentinelQA never does.
3. *Out-of-process isolation of the code under test* (per-test child processes judged by a
   parent that never imports candidate code, or an RPC proxy between tests and code).
   Rejected for now: collection imports the code, a child's exit status is as forgeable as
   the summary, and transparent proxying of arbitrary Python objects is brittle. It remains
   the only full answer and stays deferred with the Docker runner proof.
4. *A known-answer positive control* (chosen): plant a test whose correct outcome is
   known — it must fail — and require the runner to report it. Placing it inside the
   existing full-suite runs would cost nothing extra but would change their pass semantics
   and let a fail-fast option (`-x`, `--maxfail`) stop at the canary and skip real tests;
   a dedicated probe run keeps every existing run and count rule unchanged.

**Decision.** After the verification runs and the PatchForge cross-check, and only when
no blocking finding exists, SentinelQA derives a canary from the review identity: a module
`test_sentinelqa_canary_<token>.py` whose single test raises `AssertionError`, placed in the
directory holding the most locked `test_*.py`/`*_test.py` modules. It plants the canary in
the verification tree for exactly one run of the operator's full-suite command and removes
it afterwards (`canary.py`, `SpecificationRunner.run(..., canary=...)`). The probe
demonstrates runner integrity only if the runner names the canary in a `FAILED` short-summary
line, the summary counts exactly one failure and no errors, the exit status is 1, and the
tree is unchanged. Otherwise:

- the canary is reported but other tests fail too: `pristine_tests_failed`;
- the probe changed the tree: `verification_tree_mutated`;
- the canary is not properly reported: SentinelQA runs the same probe on the *pristine*
  tree as a control. If trusted code reports the canary, the candidate changed how results
  are reported: `runner_integrity_violated` (rejection). If even the pristine tree does not,
  the profile cannot demonstrate integrity: `runner_integrity_unproven` (unverifiable, so
  the verdict is inconclusive and fails closed). No locked pytest module to plant beside is
  also `runner_integrity_unproven`.

`SentinelVerdict` enforces the rule in its contract: `passed` requires a verification-tree
run that demonstrated runner integrity, so no caller can produce a pass without the probe.
Scripted and content-oracle sandboxes never run repository code; the deterministic gates
wrap them in `HonestCanaryRunner`, which reports planted canaries the way pytest does, and
scenarios model a silenced runner by withholding that on the candidate tree. The real-pytest
regressions live in `tests/unit/test_sentinelqa_runner_integrity.py`. A review now runs at
most seven commands (five before, plus the probe and, only when needed, its control); an
honest pass costs one extra full-suite run. The canary token is derived from the review, not
secret: code in the same process can list the directory, so secrecy would add nothing.

**Candidate-changed tests stay rejected.** SentinelQA does not start judging the merit of a
candidate's changes to locked tests. Any such change remains `specification_modified` (or
`evaluation_config_altered`), and candidate-added tests remain excluded from evidence. Test
repair stays an owner decision (REVISE), because a candidate that may edit the
specification defines what correct means.

**Residual risk, accepted and recorded.** The canary catches manipulation that is not
aware of it. Code written to recognise the canary (for example by listing test directories
for files it did not expect) and to spare it while silencing the real tests is not caught,
and neither is code that behaves correctly only when it detects a test run, or a candidate
that satisfies the tests without solving the task. Such code is purpose-built against the
reviewer and visible in the patch under human review. Closing it needs out-of-process
execution of the code under test (alternative 3).

**Count substitution, analysed and not built.** Candidate code can shrink the set of
pristine tests the runner collects without a skip or an error (`__test__ = False` on a
production base class a test class inherits from) and pad the count with its own test
module outside the prefixes. Against a real pytest this is rejected already: the
operator's targeted command collects only the specification, where the pristine test is
missing (`tests_missing`). Tests whose cases come from production data (`parametrize`
over a constant the candidate may edit) remain weak by construction; pytest's index-based
ids would not change, so per-test-identifier tracking would not see it either. Data a test
depends on belongs under a test prefix or in the lock's additional paths (runbook).

### Execution reuses the sandbox boundary

`SpecificationRunner` executes only operator-profile commands through any
`SandboxExecutor` (Docker in production; scripted `FakeSandbox` or the benchmark's
content oracle in deterministic gates) under the operator's sandbox policy, with review-
bound request identities and at most seven runs per review. Test counts come from
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
  tree, a passing status with reported failures, a runner silenced by the candidate's
  code, a runner that cannot report the canary, and a probe that reveals failures.
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
