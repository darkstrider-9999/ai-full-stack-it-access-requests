# IT access-request intake

This CLI turns exported messages into structured facts and a queue for human review. It prepares review work; it grants no access and records no approval. A manager is a contact. Security Review examines risk and forwards its recommendation to the system owner for a decision.

The committed outputs use **mock** responses. The OpenAI Responses HTTP adapter is implemented and checked with fake transport. Facilitator-key/model access and live extraction accuracy remain **unverified**.

## Setup

Use Linux on a single host, Python **3.11 or newer**, and `uv`. The observed environment is Python 3.12.3 and uv 0.13.7; Python 3.11 execution has not been observed. `pyproject.toml` pins `jsonschema==4.26.0`; `uv.lock` pins its dependencies. The adapter uses Python's standard HTTP/TLS library, not a provider SDK.

Run from the repository root. These variables let you choose durable state and output locations; keep the evaluation destinations separate. Local databases retain original bytes, immutable attempts and history outside the exports.

<!-- smoke:setup -->
```bash
uv sync --frozen
source .venv/bin/activate
INTAKE_STATE="${INTAKE_STATE:-.local/intake.sqlite3}"
EVALUATION_STATE="${EVALUATION_STATE:-.local/evaluation.sqlite3}"
INTAKE_OUTPUT="${INTAKE_OUTPUT:-deliverables}"
EVALUATION_OUTPUT="${EVALUATION_OUTPUT:-.local/evaluation-exports}"
ACCURACY_OUTPUT="${ACCURACY_OUTPUT:-deliverables/accuracy.md}"
```

A fresh dependency install may require package-index access. T18 audits setup offline against the installed environment; it does not claim a fresh network installation. `.local/` is ignored by Git. Preserve that directory if you need the generated attempt history; there is no automatic deletion.

## Mock intake and inspection

No key is needed. The default mock maps input filenames to explicitly authored response fixtures. `--mock-script tests/mock_responses/manifest-m20.json` can instead select the 20 replies explicitly. Mock replies are inputs, not the accuracy oracle.

<!-- smoke:intake -->
```bash
env -u API_KEY python3 -m access_intake intake materials/requests --provider mock --state "$INTAKE_STATE"
```

In another terminal, activate the same environment and set the same `INTAKE_STATE`. Committed results remain inspectable while other calls are running. Processing states (`pending`, `running`, `interrupted`, `succeeded`, `failed`) are separate from policy status. Pending revisions identify their own attempt/hash and label earlier results explicitly.

<!-- smoke:queue -->
```bash
env -u API_KEY python3 -m access_intake queue --include-pending --state "$INTAKE_STATE"
```

Inspect a document's exact supporting quotes, issues, status, owner, next action and provenance. The override paragraph is displayed as untrusted evidence.

<!-- smoke:inspect -->
```bash
env -u API_KEY python3 -m access_intake inspect --document IT-006.json --state "$INTAKE_STATE"
```

<!-- smoke:history -->
```bash
env -u API_KEY python3 -m access_intake history --document IT-001.json --state "$INTAKE_STATE"
```

Add `--json` to queue, inspect or history for machine-readable output. JSON history includes archived source bytes as base64, configuration, policy hash and replay links. Each filename identifies an independent document even when request IDs match.

## Recovery and refresh

Intake archives the whole batch before starting calls. Workers default to four (`--workers`); claims default to a 60-second renewable lease (`--lease-seconds`). Model calls hold no database transaction. A second operator cannot claim the same live attempt. After a killed worker's lease expires, resume reclaims unfinished attempts using archived inputs/settings; completed work is left alone.

<!-- smoke:resume -->
```bash
env -u API_KEY python3 -m access_intake resume --state "$INTAKE_STATE"
```

Rate limits, timeouts and unavailability produce visible, safe errors. There is no implicit retry loop. An unchanged failure remains visible until explicit retry. The following example finds and replays IT-001's latest archived attempt; an operator can instead copy a specific attempt ID from history or its error's replay command.

<!-- smoke:retry -->
```bash
ATTEMPT_ID="$(env -u API_KEY python3 -m access_intake history --document IT-001.json --json --state "$INTAKE_STATE" | python3 -c 'import json, sys; print(json.load(sys.stdin)[-1]["attempt_id"])')"
env -u API_KEY python3 -m access_intake retry --attempt "$ATTEMPT_ID" --state "$INTAKE_STATE"
```

Retry uses archived bytes even if the source path changed or disappeared. A replay of an obsolete version stays historical. Stale claims and old source completions cannot overwrite newer work. Remote calls can duplicate after interruption; claim fencing does not guarantee exactly-once remote execution or billing.

Rerun intake to refresh changed source bytes or relevant provider/model, prompt, schema, routing and policy fingerprints. Formatting-only changes and A→B→A create revisions. Worker count does not invalidate a result. Omitted files are retained, never treated as withdrawals. Replays retain archived prompt/schema/settings but execute installed application code; preserve the matching code version for historical semantic reproducibility across deployments.

## Export and isolated evaluation

<!-- smoke:export -->
```bash
env -u API_KEY python3 -m access_intake export --output "$INTAKE_OUTPUT" --state "$INTAKE_STATE"
```

Each export writes `extractions.jsonl` and `queue.json` from one database snapshot into a generation directory, then atomically switches `current`. The top-level files are relative symlinks through `current`. For a consistent pair, resolve `"$INTAKE_OUTPUT/current"` once and read both files inside that pinned generation. Readers opening top-level aliases across a switch must compare `generation_id` and retry on disagreement. Old generations remain readable. Pending work is separately inspectable, rather than exported as a completed extraction.

The authored 21st message lives in `tests/inputs/IT-001.json`. Evaluation uses the same loader, prompt, response gate and router as intake; its default mock script is `manifest-paraphrase.json`. Existing intake state/output is rejected as an evaluation destination. Evaluation markers bind its own state and output for later reruns.

<!-- smoke:evaluate -->
```bash
env -u API_KEY python3 -m access_intake evaluate-paraphrase --input tests/inputs/IT-001.json --state "$EVALUATION_STATE" --output "$EVALUATION_OUTPUT" --provider mock
```

Production exports keep 20 messages. The evaluation state supplies the separate paraphrase case to accuracy scoring.

<!-- smoke:accuracy -->
```bash
env -u API_KEY python3 -m access_intake accuracy --expected tests/expected/accuracy.json --state "$INTAKE_STATE" --evaluation-state "$EVALUATION_STATE" --output "$ACCURACY_OUTPUT"
```

Use `--json` without `--output` for the complete machine-readable comparison. The denominator stays **21 × 12 = 252**. Values are compared case-sensitively without normalization; a present null equals null. Missing/failed extractions lose all 12 slots, including expected nulls. Quotes, issue explanations and routing are reported separately. Purpose wording differences require human semantic adjudication; they are not automatically hallucinations. Duplicate scoped measurements are rejected. No passing score is specified.

The source-derived gold is a draft awaiting human review of all 252 fields, including the clarified override and reversed-date cases. The mock 252/252 observation verifies fixture processing, not live model extraction. Preserve the draft test fixture; after actually reviewing the facts, an operator may save a separate reviewed gold copy with reviewer metadata and pass that path to `--expected`. Do not label an unreviewed gold file as a complete benchmark.

## Provider configuration

The production-only loader reads **`API_KEY`** and maps it to Bearer authentication. Tests inject a dummy credential or lookup; mock commands and accuracy do not load the key. Keep credentials outside the repository and prompts. The adapter uses safe local error descriptions and credential-redacted request evidence.

For a provider run, obtain the facilitator's configuration and supply an explicit Responses model that supports strict JSON Schema. There is no default model or verified key/model pairing. `MODEL_ID` below is a shell variable selected by the operator, not an application credential variable. This example is not executed by tests:

```bash
python3 -m access_intake intake materials/requests --provider openai --model "$MODEL_ID" --endpoint https://api.openai.com/v1/responses --timeout 60 --output-limit 4096 --state .local/openai-intake.sqlite3
```

`--temperature` is optional; omit it for models that do not support sampling temperature. The endpoint must be HTTPS without embedded credentials. Requests are non-streaming, use the unchanged supplied schema, and set `store=false`. The timeout bounds I/O operations, not a total wall-clock SLA. HTTP redirects and hidden retries are disabled. Refusal and incomplete output take precedence over apparent JSON text. `response_source` records the returned model; no model is inferred when no usable response arrived. See [request evidence](deliverables/request-evidence.json) for an explicitly labeled fake HTTP example and the official contract references.

## Tests, evidence and limits

<!-- smoke:tests -->
```bash
set -o pipefail
env -u API_KEY python3 -m unittest discover -s tests -v 2>&1 | tee planning/test-run.txt
```

All provider replies in tests are saved mocks under `tests/mock_responses/`. Test guards reject network access and `API_KEY` lookups, including child Python entry points. The HTTP opener is injected in adapter tests. The 2,000-message exercise prints its measured duration, model/configuration and peak call count into the full test run; it establishes no latency target.

The supported sharing boundary is local SQLite/WAL on a **single host**, with a busy timeout. Multi-host and network-filesystem coordination are not supported. Functional barriers and killed-process tests do not establish unmeasured scale, durability under every storage failure, or exactly-once billing.

Grounding checks verify exact decoded-message quote substrings and presence of literal facts; they do not prove full semantic extraction accuracy, including purpose summaries or correct interpretation of every conflict/correction. Detected grounding mismatches follow user clarification Q-80: IT Service Desk / needs-information / clarify, without usable extraction. Q-43/Q-51 issue conventions are explicit prefixes; a nonempty issues array alone does not force clarification. Q-40 retains reversed dates with a clarification issue. Q-46 retains ordinary facts before an override. Q-49 clarifies withdrawals missing their ID or date.

**Q-45 remains open:** the explicit `SYSTEM OVERRIDE:` form and reported override issues are covered; indirect, quoted, negated and obfuscated forms have no agreed broad classifier. Unspecified input compatibility and business-policy cases are not silently assigned new rules. Dates are checked for YYYY-MM-DD presentation; no new calendar-error policy is inferred.

[Operational verification](deliverables/verification.md) links intended assertions, observed mock evidence and limitations. [Accuracy](deliverables/accuracy.md) includes all expected facts and observation configurations. [planning/validation.md](planning/validation.md) is a separate, initially unmarked checklist: a human must open the evidence and decide each requirement's mark. Passing tests are not those human decisions.

Submission and capture checks C18–C20 remain manual. The final reviewed commit/session pairing, verified remote delivery and Classroom receipt require the facilitator's instructions and submission authorization. These instructions do not push or submit anything.
