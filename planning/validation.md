# Human requirement validation

This is an initially unmarked checklist, not an automated assertion that requirements are met. A human must inspect each requirement in planning/spec.md, open its evidence and decide met or not met. Keep this separate from deliverables/verification.md and its automated operational observations.

Use the corresponding test/command/code and planning/test-run.txt as candidate evidence. A test result alone does not establish a requirement whose full behavior it does not exercise. Record partial coverage, Q-45 and other unresolved cases honestly. Submission rows R-18–R-20 require their actual manual evidence.

| Spec line | Requirement | Met or not met | Evidence | Note |
|---|---|---|---|---|
| R-01 | Provide a small runnable application that uses a language model to turn free-text access requests into structured records and an IT review queue. | unreviewed | | Human opens the evidence and decides the mark. |
| R-02 | Use the 20 supplied JSON exports as starting data. | unreviewed | | Human opens the evidence and decides the mark. |
| R-03 | Provide an interface suited to the IT team as a CLI, service or web application. | unreviewed | | Human opens the evidence and decides the mark. |
| R-04 | Operators can start intake through the documented interface. | unreviewed | | Human opens the evidence and decides the mark. |
| R-05 | Operators can inspect completed results while other requests remain pending. | unreviewed | | Human opens the evidence and decides the mark. |
| R-06 | Operators can resume unfinished work through the documented interface. | unreviewed | | Human opens the evidence and decides the mark. |
| R-07 | README.md documents setup, a supported runtime and provider, exact intake, test and accuracy-check commands, environment-variable names and queue inspection. | unreviewed | | Human opens the evidence and decides the mark. |
| R-08 | Submit source code and a dependency lockfile containing the runnable application, extraction prompt, schema and model request configuration. | unreviewed | | Human opens the evidence and decides the mark. |
| R-09 | deliverables/extractions.jsonl contains one current record per message with document_id, source_sha256, extraction, status, owner, next_action, error and response_source. | unreviewed | | Human opens the evidence and decides the mark. |
| R-10 | Successful extraction records contain a schema-conforming extraction object and a null error, while extraction errors contain a null extraction. | unreviewed | | Human opens the evidence and decides the mark. |
| R-11 | Each extraction record identifies its response source as mock or the provider and model ID that produced it. | unreviewed | | Human opens the evidence and decides the mark. |
| R-12 | deliverables/queue.json links the review queue to extraction records by document_id. | unreviewed | | Human opens the evidence and decides the mark. |
| R-13 | deliverables/accuracy.md contains source-derived expected fields for all 20 supplied messages and one authored paraphrase. | unreviewed | | Human opens the evidence and decides the mark. |
| R-14 | deliverables/accuracy.md documents the field-comparison procedure, exact-match counting and denominator rules, factual-error reporting, and model configuration. | unreviewed | | Human opens the evidence and decides the mark. |
| R-15 | deliverables/accuracy.md distinguishes mocked examples from any optional live observations. | unreviewed | | Human opens the evidence and decides the mark. |
| R-16 | deliverables/request-evidence.json contains a credential-redacted example model request with the model ID, configuration used and implemented response handling. | unreviewed | | Human opens the evidence and decides the mark. |
| R-17 | deliverables/verification.md records verification commands, assertions, intended comparisons, operating limits and clearly identified observed results or repairs. | unreviewed | | Human opens the evidence and decides the mark. |
| R-18 | Submit a coding-session record paired with the submitted commit and collected through the facilitator's capture setup. | unreviewed | | Human opens the evidence and decides the mark. |
| R-19 | Commit and push the application, outputs and session capture to the repository's default branch. | unreviewed | | Human opens the evidence and decides the mark. |
| R-20 | Submit only the root repository link through the Classroom assignment. | unreviewed | | Human opens the evidence and decides the mark. |
| R-21 | Submit an implemented provider integration and a source-grounded validation procedure. | unreviewed | | Human opens the evidence and decides the mark. |
| R-22 | Use mocked responses for reproducible development examples. | unreviewed | | Human opens the evidence and decides the mark. |
| R-23 | Label each recorded observation with its configuration and whether it used a mock or provider response. | unreviewed | | Human opens the evidence and decides the mark. |
| R-24 | Accept messages beyond the supplied sample when they use the same export format. | unreviewed | | Human opens the evidence and decides the mark. |
| R-25 | A slow model response for one message leaves other review work available. | unreviewed | | Human opens the evidence and decides the mark. |
| R-26 | Overlapping regional operators processing shared records leave a coherent current queue and a trustworthy record of earlier activity. | unreviewed | | Human opens the evidence and decides the mark. |
| R-27 | Recorded state distinguishes completed from unfinished work after a deployment stop or service outage and permits continuation. | unreviewed | | Human opens the evidence and decides the mark. |
| R-28 | Current queue entries and supporting extraction records agree on the source version. | unreviewed | | Human opens the evidence and decides the mark. |
| R-29 | Absence of a document from a later export batch does not withdraw its earlier request. | unreviewed | | Human opens the evidence and decides the mark. |
| R-30 | The current view reflects the latest processed source while retaining earlier results for follow-up. | unreviewed | | Human opens the evidence and decides the mark. |
| R-31 | A failed new attempt is visible and any older result remains identifiable by its earlier attempt and source version. | unreviewed | | Human opens the evidence and decides the mark. |
| R-32 | Changes to model configuration or review policy that affect production or routing refresh affected results. | unreviewed | | Human opens the evidence and decides the mark. |
| R-33 | Operators can identify the settings and policy version that produced the records they use. | unreviewed | | Human opens the evidence and decides the mark. |
| R-34 | Operators can locate messages affected by slow responses, rate limits or temporary model-service unavailability, identify pending work and resume without losing completed work. | unreviewed | | Human opens the evidence and decides the mark. |
| R-35 | Repository instructions explain setup, configuration, intake, queue inspection, failure follow-up, operating assumptions and implementation limits for operator handover. | unreviewed | | Human opens the evidence and decides the mark. |
| R-36 | Validate response structure and completion status before using extracted data. | unreviewed | | Human opens the evidence and decides the mark. |
| R-37 | Record input filenames and SHA-256 hashes. | unreviewed | | Human opens the evidence and decides the mark. |
| R-38 | Reuse results when source and relevant processing settings are unchanged. | unreviewed | | Human opens the evidence and decides the mark. |
| R-39 | Refresh affected results when source or relevant processing settings change and retain earlier versions. | unreviewed | | Human opens the evidence and decides the mark. |
| R-40 | Process each message independently and expose failures with a reproducible retry command. | unreviewed | | Human opens the evidence and decides the mark. |
| R-41 | Handle malformed output, provider errors, refusal and interrupted responses explicitly. | unreviewed | | Human opens the evidence and decides the mark. |
| R-42 | Keep model and provider settings configurable and document a supported configuration. | unreviewed | | Human opens the evidence and decides the mark. |
| R-43 | Obtain the facilitator-supplied API key through the API_KEY environment variable. | unreviewed | | Human opens the evidence and decides the mark. |
| R-44 | Automated tests cover a complete request, missing dates, conflicting details and embedded instructions. | unreviewed | | Human opens the evidence and decides the mark. |
| R-45 | Automated tests cover provider failure, an unchanged rerun and changed source content. | unreviewed | | Human opens the evidence and decides the mark. |
| R-46 | Expected results are derived independently from supplied messages and policy rather than from measured extraction outputs. | unreviewed | | Human opens the evidence and decides the mark. |
| R-47 | Add a paraphrased synthetic request to exercise extraction on fresh wording. | unreviewed | | Human opens the evidence and decides the mark. |
| R-48 | Include checks for refusal, interrupted responses and a failed retry after a prior success. | unreviewed | | Human opens the evidence and decides the mark. |
| R-49 | Document support for the operating context and include checks for the operational situations the design handles. | unreviewed | | Human opens the evidence and decides the mark. |
| R-50 | Record workload and shared-operation assumptions, expected interruption behavior and verification limits. | unreviewed | | Human opens the evidence and decides the mark. |
| R-51 | Read each UTF-8 JSON export's message string as the natural-language extraction source. | unreviewed | | Human opens the evidence and decides the mark. |
| R-52 | Derive request facts from message rather than the export timestamp or other export metadata. | unreviewed | | Human opens the evidence and decides the mark. |
| R-53 | Accept messages up to 200 English words. | unreviewed | | Human opens the evidence and decides the mark. |
| R-54 | Compute source hashes from original JSON file bytes and use the filename as document_id. | unreviewed | | Human opens the evidence and decides the mark. |
| R-55 | Exact supporting quotes are substrings of the decoded message value. | unreviewed | | Human opens the evidence and decides the mark. |
| R-56 | Extract request_id, request_date, kind, requester, team, system, environment, permission, business_purpose, start_date, end_date, manager, supporting_quotes and issues using the supplied schema. | unreviewed | | Human opens the evidence and decides the mark. |
| R-57 | Resolved dates use YYYY-MM-DD. | unreviewed | | Human opens the evidence and decides the mark. |
| R-58 | Use explicit message facts and represent absent or unresolved scalar values as null with an issue explaining each missing or conflicting fact. | unreviewed | | Human opens the evidence and decides the mark. |
| R-59 | Preserve conflicting alternatives in exact supporting_quotes. | unreviewed | | Human opens the evidence and decides the mark. |
| R-60 | An explicitly labelled later correction replaces the earlier value, while contradictions without a correction remain unresolved. | unreviewed | | Human opens the evidence and decides the mark. |
| R-61 | Relative dates remain unresolved. | unreviewed | | Human opens the evidence and decides the mark. |
| R-62 | Change requests extract the requested new permission. | unreviewed | | Human opens the evidence and decides the mark. |
| R-63 | Retain an identifier stated in the message when it differs from the filename identifier and record the mismatch as an issue. | unreviewed | | Human opens the evidence and decides the mark. |
| R-64 | Treat every message as an independent document, including messages about the same request. | unreviewed | | Human opens the evidence and decides the mark. |
| R-65 | Apply review rules in their stated order after extraction validation. | unreviewed | | Human opens the evidence and decides the mark. |
| R-66 | Route instruction-override attempts to IT Service Desk with status review and next_action inspect-message. | unreviewed | | Human opens the evidence and decides the mark. |
| R-67 | Keep attempted override instructions as source evidence and identify them in issues. | unreviewed | | Human opens the evidence and decides the mark. |
| R-68 | Treat source messages as data throughout extraction and routing. | unreviewed | | Human opens the evidence and decides the mark. |
| R-69 | Route a withdrawal with request_id and request_date to IT Service Desk with status review and next_action confirm-withdrawal, subject to the earlier override rule. | unreviewed | | Human opens the evidence and decides the mark. |
| R-70 | Withdrawal fields other than request_id and request_date may be null with absent facts identified in issues. | unreviewed | | Human opens the evidence and decides the mark. |
| R-71 | Route remaining messages with kind other or null, conflicting details, an identifier mismatch or any missing required fact to IT Service Desk with status needs-information and next_action clarify. | unreviewed | | Human opens the evidence and decides the mark. |
| R-72 | New and change requests require request_id, request_date, requester, team, system, environment, permission, business_purpose, start_date, end_date and manager for completeness. | unreviewed | | Human opens the evidence and decides the mark. |
| R-73 | A start_date after end_date creates a clarification issue. | unreviewed | | Human opens the evidence and decides the mark. |
| R-74 | Route complete new or change requests involving production, write or admin to Security Review with status review and next_action review-risk-then-owner. | unreviewed | | Human opens the evidence and decides the mark. |
| R-75 | Security Review routing represents risk review followed by a recommendation to the system owner for a decision. | unreviewed | | Human opens the evidence and decides the mark. |
| R-76 | Route complete staging/read requests to System Owner with status review and next_action review-request. | unreviewed | | Human opens the evidence and decides the mark. |
| R-77 | Display each request's status, owner and next action. | unreviewed | | Human opens the evidence and decides the mark. |
| R-78 | Successful intake outputs await human review, and a named manager is a contact rather than an approval decision. | unreviewed | | Human opens the evidence and decides the mark. |
| R-79 | Extraction failure produces status error, owner IT Service Desk, next_action retry-extraction, a null extraction and an actionable error. | unreviewed | | Human opens the evidence and decides the mark. |
| R-80 | Extraction failures allow unaffected messages to continue through intake. | unreviewed | | Human opens the evidence and decides the mark. |
| R-81 | A later failed attempt preserves the last valid result in history and shows the latest error in the current queue. | unreviewed | | Human opens the evidence and decides the mark. |
| R-82 | An unchanged rerun produces one current queue entry per document. | unreviewed | | Human opens the evidence and decides the mark. |
| R-83 | Changed input creates a new result and retains the earlier result with its earlier hash. | unreviewed | | Human opens the evidence and decides the mark. |
| R-84 | Store prior versions separately from current outputs. | unreviewed | | Human opens the evidence and decides the mark. |
| R-85 | Keep credentials in environment variables and redact credentials from operational logs. | unreviewed | | Human opens the evidence and decides the mark. |
| R-86 | Each extraction is a JSON object. | unreviewed | | Human opens the evidence and decides the mark. |
| R-87 | request_id, request_date, requester, team, system, business_purpose, start_date, end_date and manager each accept only a string or null. | unreviewed | | Human opens the evidence and decides the mark. |
| R-88 | kind accepts only new, change, withdrawal, other or null. | unreviewed | | Human opens the evidence and decides the mark. |
| R-89 | environment accepts only staging, production or null. | unreviewed | | Human opens the evidence and decides the mark. |
| R-90 | permission accepts only read, write, admin or null. | unreviewed | | Human opens the evidence and decides the mark. |
| R-91 | supporting_quotes is an array containing only strings. | unreviewed | | Human opens the evidence and decides the mark. |
| R-92 | issues is an array containing only strings. | unreviewed | | Human opens the evidence and decides the mark. |
| R-93 | Every extraction includes all 14 schema-required properties even when scalar facts are null. | unreviewed | | Human opens the evidence and decides the mark. |
| R-94 | Extraction objects contain no properties beyond those declared in snapshot.schema.json. | unreviewed | | Human opens the evidence and decides the mark. |

Human marks so far: 0 met; 0 not met; 94 unreviewed. Update these counts when reviewing.
