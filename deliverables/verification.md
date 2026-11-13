# Operational verification

This is automated operational evidence, separate from the human requirement checklist at [planning/validation.md](../planning/validation.md). Human gold review is also pending. Neither a passing suite nor the mocked accuracy score marks those reviews complete.

## Configuration and evidence sources

The committed extraction and queue records use **mock** responses from `tests/mock_responses/`. Each extraction retains its configuration, prompt/schema/routing fingerprints, policy hash, source hash and attempt ID. [accuracy.md](accuracy.md) records matching per-document observation provenance for the 20 submission records and isolated paraphrase. Its 252/252 fixture agreement is not live extraction performance.

[request-evidence.json](request-evidence.json) is a constructed request plus one **fake HTTP transport** observation. Requested/returned model IDs are synthetic (`fixture-requested-model` / `fixture-returned-model`); the dummy credential is redacted. It points to the implemented adapter/gate and T15. Facilitator-key/model access remains unverified; no live provider call was made.

The observed runtime is Python 3.12.3 with uv 0.13.7 on Linux. Python 3.11+ is declared compatible, but Python 3.11 execution was not observed. `uv.lock` pins jsonschema 4.26.0 and its dependencies. T18 audits setup offline using already installed packages; a fresh network install is not an observed result.

## Intended checks and observed evidence

The executable commands are in [README.md](../README.md). The complete outer-suite result is saved in [planning/test-run.txt](../planning/test-run.txt). Consult that run's final result before treating an assertion as observed passing evidence. The rows below describe what the named tests exercise, not a replacement for opening their evidence.

| Test | Intended assertion | Observed evidence to inspect |
|---|---|---|
| T01 | Saved replies, deterministic barriers/errors, call counts; network/key isolation includes children | `tests/test_t01_mock_provider.py`; installed guards in `tests/support/`; named result in the full run |
| T02 | Original bytes/hash/filename and decoded message preserved; metadata cannot become source facts; 200 words survive | `tests/test_t02_input_prompt.py`; fixture-backed request captures |
| T03a–T03e | Completion/refusal/interruption precede parsing; schema/date checks; grounding clarification; safe errors without implicit retries | `tests/test_t03_response_gate.py`; authored invalid envelope/schema fixtures |
| B001–B020 / T04 | All 14 extraction keys, independent scalar facts, exact source evidence and ordered routes; no approval/provisioning | `tests/test_t04_routing.py`; source-derived `tests/expected/step4.json` |
| T05 | Reopened SQLite state retains source/history; current pointers and queue joins agree; pinned export generations survive publication | `tests/test_t05_durable_records.py`; includes a publication barrier while intake continues |
| T06–T10 | Reuse only current source/settings; revisions and explicit archived replay; obsolete completion/replay fencing; omitted inputs retained | `tests/test_t06_revisions_replay.py`; intentionally finite provider scripts expose extra calls |
| T11 | A held provider reply leaves another completed result and an independent failure inspectable; earlier versions are labeled | `tests/test_t07_operations.py`; barrier-controlled CLI queue observations |
| T12 | Two actual CLI processes share one live claim; a newer revision remains current when an older reply finishes last | The same operational test file; coherent exported pairs inspected during overlap |
| T13 | A killed process resumes after lease expiry, including reply-before-persistence death; completed work is not recalled; stale token cannot commit | Real killed-worker harness in `tests/support/controlled_worker.py`; direct fake-clock lease fencing assertions |
| T14 | Rate limit, timeout and unavailability stay locatable until explicit recovery; 2,000 mock records use at most four simultaneous calls | The `T14 mock workload` JSON observation in the full test run: duration, message count, peak calls and configuration are actual run measurements |
| T15 | Exact request/configuration/schema/authentication mapping; fake HTTP completion/error variants; redacted evidence; injected key lookup | `tests/test_t08_provider.py`; fake opener and fake transport; request-evidence references |
| T16 | Authored fresh wording uses the shared pipeline and isolated destinations; production state/output stay unchanged | `tests/test_t09_paraphrase.py`; success and refusal fixtures; rejected destination collisions |
| T17 | Fixed 252 denominator; wrong/null/missing/failed controls lower scores; every discrepancy and source/configuration is visible | `tests/test_t10_accuracy.py`; independent `tests/expected/accuracy.json`; pending human-review label |
| T18 | README mock commands execute in temporary state; deliverables join and match accuracy provenance; human validation stays separate | `tests/test_t11_handover.py`; setup uses offline dependency audit; the full test command is executed externally to avoid recursion |

## Observed results and repairs

Each test-first checkpoint preceded its implementation commit. Existing passing behavior was retained honestly, including the storage stale-completion check and the independent gold-provenance check. Final implementation run details, test names and the observed workload measurement are in `planning/test-run.txt`; no latency SLA is inferred from that measurement.

The Step 7 review identified and repaired an interruption gap: registration now archives the complete batch before provider work begins, so resume can find the unprocessed tail. Durable claim tokens fence completions, while renewable leases allow live calls outside database transactions. The operational tests also stop a real worker after its mock reply but before persistence.

The handover run generates a persistent local mock intake database and a separate evaluation database under `.local/`; only outputs/evidence are committed. The current generation contains exactly 20 submission records. The separate paraphrase contributes twelve accuracy slots without joining those production exports. A repeated explicit replay may add history while preserving one current row per filename.

## Limitations and human work

- SQLite/WAL coordination is for a single host and local filesystem. Functional tests do not establish multi-host support, an unmeasured workload SLA or resilience to every storage failure.
- Leases and claim fencing do not prevent duplicate remote execution or billing after interruption. No exactly-once provider guarantee is made. Terminal errors are not implicitly retried; resume handles unfinished work, and retry handles named terminal attempts.
- The configurable HTTP timeout bounds I/O operations, not total elapsed request time. Requests are non-streaming, bounded in response size and do not follow redirects.
- Exact quote and literal-fact presence checks are necessary checks, not complete semantic validation. Purpose summaries, conflict/correction selection and real-model extraction quality still need human evaluation. Q-45's broader override classifier remains unspecified.
- User Q-80 is resolved as human clarification for detected grounding mismatches. Q-40/Q-46/Q-49 and Q-43/Q-51 conventions are implemented as described in README; older planning questions retain their historical wording.
- Replay restores archived bytes, prompt/schema and provider settings while running installed application code. Use the matching code version for historical semantic reproducibility; archived application code is not dynamically executed.
- Review all 252 gold facts before making a complete benchmark claim. The draft gold and this mock report explicitly remain pending human review. There is no supplied passing threshold.
- Fill and count [planning/validation.md](../planning/validation.md) by opening the actual evidence. It is not automatically marked by this operational verification.
- C18–C20 (final commit/session pairing, remote default-branch delivery and Classroom receipt) remain manual, require the facilitator's instructions and submission authorization, and are not completed by these tests. No push, submission or manual checkpoint was performed.
