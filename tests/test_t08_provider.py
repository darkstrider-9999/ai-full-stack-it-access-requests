"""T15: OpenAI Responses wire adapter using authored, fake HTTP replies only.

Fixture model IDs are synthetic, not supported-model/access observations.
The official API contract and source-derived B001 expectations are independent
of measured outputs. Every credential lookup is injected; no real key is read.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support.offline_guard import install
from access_intake.prompts import build_request
from access_intake.response_gate import extract_checked
from access_intake.sources import load_source
from access_intake.storage import Store

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests/mock_responses"
SECRET = "dummy-test-secret"
CONFIG = {
    "provider": "openai", "model": "fixture-requested-model",
    "endpoint": "https://api.openai.com/v1/responses", "timeout": 12,
    "output_limit": 4096, "temperature": 0.2,
}
B001 = json.loads((ROOT / "tests/expected/step4.json").read_text())["cases"]["B001"]["facts"]
OBSERVED_SOURCE = "openai/fixture-returned-model"


class FakeHTTP:
    """A raw HTTP boundary: captures bytes/headers without opening a socket."""
    def __init__(self, fixture="provider-completed.json", *, case=None, exception=None):
        saved = json.loads((MOCKS / fixture).read_text())
        self.response = saved["cases"][case] if case is not None else saved
        self.exception = exception
        self.calls = []

    def post(self, url, *, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": body, "timeout": timeout})
        if self.exception is not None:
            raise self.exception
        if "raw_body" in self.response:
            raw = self.response["raw_body"].encode("utf-8")
        else:
            raw = json.dumps(self.response["body"]).encode("utf-8")
        return self.response["status_code"], raw


class T15ProviderIntegration(unittest.TestCase):
    def provider(self, transport, config=None):
        from access_intake.openai_provider import ResponsesProvider

        return ResponsesProvider(CONFIG if config is None else config, credential=SECRET, transport=transport)

    def source(self):
        return load_source(ROOT / "materials/requests/IT-001.json")

    def checked(self, transport):
        return extract_checked(self.source(), self.provider(transport), attempt_id="attempt-t15", configuration=CONFIG)

    def assert_failure(self, record, code, source=None):
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["owner"], "IT Service Desk")
        self.assertEqual(record["next_action"], "retry-extraction")
        self.assertIsNone(record["extraction"])
        self.assertEqual(record["error"]["code"], code)
        self.assertTrue(record["error"]["description"].strip())
        self.assertEqual(record["error"]["attempt_id"], "attempt-t15")
        self.assertIn("retry --attempt attempt-t15", record["error"]["replay_command"])
        self.assertEqual(record["attempted_configuration"], CONFIG)
        self.assertEqual(record["response_source"], source)
        self.assertNotIn(SECRET, json.dumps(record))
        self.assertNotIn("raw private provider detail", json.dumps(record))

    def test_t15_fake_request_has_config_schema_and_untrusted_message(self):
        transport = FakeHTTP()
        record = self.checked(transport)
        self.assertEqual(len(transport.calls), 1)
        captured = transport.calls[0]
        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer " + SECRET)
        self.assertEqual(captured["headers"]["Content-Type"], "application/json")
        self.assertEqual(captured["timeout"], 12)
        self.assertIsInstance(captured["body"], bytes)
        payload = json.loads(captured["body"])
        self.assertEqual(payload["model"], "fixture-requested-model")
        self.assertEqual(payload["max_output_tokens"], 4096)
        self.assertEqual(payload["temperature"], 0.2)
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["instructions"], build_request(self.source())["instructions"])
        self.assertEqual(payload["input"], [{"role": "user", "content": [{"type": "input_text", "text": self.source().message}]}])
        self.assertEqual(payload["text"]["format"], {
            "type": "json_schema", "name": "access_request", "strict": True,
            "schema": json.loads((ROOT / "snapshot.schema.json").read_text()),
        })
        self.assertNotIn(SECRET, captured["body"].decode())
        self.assertNotIn("exported_at", payload)
        self.assertNotIn("tools", payload)
        self.assertIsNone(record["error"])
        self.assertEqual(record["response_source"], OBSERVED_SOURCE, "use returned model, not requested model")
        self.assertEqual(record["extraction"], {**B001, "supporting_quotes": [self.source().message], "issues": []})

    def test_t15_configurable_endpoint_and_optional_sampling(self):
        transport = FakeHTTP()
        config = {**CONFIG, "endpoint": "https://custom-provider.invalid/v1/responses", "temperature": None, "timeout": 3, "output_limit": 2048}
        provider = self.provider(transport, config)
        provider.extract(build_request(self.source()))
        call = transport.calls[0]
        self.assertEqual(call["url"], config["endpoint"])
        self.assertEqual(call["timeout"], 3)
        payload = json.loads(call["body"])
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["max_output_tokens"], 2048)

    def test_t15_http_transport_builds_a_real_request_with_a_fake_opener(self):
        from access_intake.openai_provider import HTTPTransport

        fixture = FakeHTTP()
        status, raw = fixture.post(CONFIG["endpoint"], headers={}, body=b"{}", timeout=12)
        response = mock.MagicMock()
        response.status = status
        response.read.return_value = raw
        response.__enter__.return_value = response
        opener = mock.Mock(return_value=response)
        transport = HTTPTransport(opener=opener)
        returned = transport.post(CONFIG["endpoint"], headers={"Authorization": "Bearer " + SECRET, "Content-Type": "application/json"}, body=b"{}", timeout=12)
        self.assertEqual(returned, (200, raw))
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, CONFIG["endpoint"])
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"{}")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
        self.assertEqual(opener.call_args.kwargs["timeout"], 12)

    def test_t15_refusal_and_interruption_precede_output_parsing(self):
        for fixture, code in (("provider-refusal.json", "refused"), ("provider-incomplete.json", "interrupted")):
            with self.subTest(fixture=fixture):
                transport = FakeHTTP(fixture)
                self.assert_failure(self.checked(transport), code, OBSERVED_SOURCE)
                self.assertEqual(len(transport.calls), 1)

    def test_t15_output_envelope_variants(self):
        failures = {
            "incomplete_message": ("interrupted", OBSERVED_SOURCE),
            "cancelled": ("interrupted", OBSERVED_SOURCE),
            "queued": ("interrupted", OBSERVED_SOURCE),
            "in_progress": ("interrupted", OBSERVED_SOURCE),
            "missing_status": ("invalid_completion", OBSERVED_SOURCE),
            "unknown_status": ("invalid_completion", OBSERVED_SOURCE),
            "missing_model": ("invalid_provider_response", None),
            "missing_output": ("invalid_provider_response", None),
            "empty_output": ("invalid_provider_response", None),
            "wrong_role": ("invalid_provider_response", None),
            "non_json_body": ("invalid_provider_response", None),
            "non_object_body": ("invalid_provider_response", None),
            "error_with_text": ("provider_error", None),
            "failed": ("provider_error", None),
        }
        for case, (code, source) in failures.items():
            with self.subTest(case=case):
                transport = FakeHTTP("provider-errors.json", case=case)
                self.assert_failure(self.checked(transport), code, source)
                self.assertEqual(len(transport.calls), 1)
        for case in ("reasoning_then_text", "split_text"):
            with self.subTest(case=case):
                record = self.checked(FakeHTTP("provider-errors.json", case=case))
                self.assertIsNone(record["error"])
                self.assertEqual(record["extraction"], {**B001, "supporting_quotes": [self.source().message], "issues": []})

    def test_t15_http_errors_and_transport_errors_are_safe_without_implicit_retries(self):
        for case, code in (("http_400", "invalid_request"), ("http_401", "authentication_error"),
                           ("http_403", "permission_denied"), ("http_429", "rate_limited"),
                           ("http_500", "unavailable"), ("http_503", "unavailable")):
            with self.subTest(case=case):
                transport = FakeHTTP("provider-errors.json", case=case)
                self.assert_failure(self.checked(transport), code)
                self.assertEqual(len(transport.calls), 1)
        for exception, code in ((TimeoutError("raw private provider detail " + SECRET), "timeout"),
                                (OSError("raw private provider detail " + SECRET), "unavailable")):
            with self.subTest(exception=type(exception).__name__):
                transport = FakeHTTP(exception=exception)
                self.assert_failure(self.checked(transport), code)
                self.assertEqual(len(transport.calls), 1)

    def test_t15_injected_credential_lookup_never_reads_process_environment(self):
        from access_intake.openai_provider import ConfigurationError, ResponsesProvider, load_api_key

        for missing in (None, "", "   "):
            with self.subTest(missing=missing):
                lookup = mock.Mock(return_value=missing)
                with self.assertRaisesRegex(ConfigurationError, "API_KEY"):
                    load_api_key(lookup=lookup)
                lookup.assert_called_once_with("API_KEY")
        lookup = mock.Mock(return_value=SECRET)
        transport = FakeHTTP()
        provider = ResponsesProvider(CONFIG, credential=load_api_key(lookup=lookup), transport=transport)
        provider.extract(build_request(self.source()))
        lookup.assert_called_once_with("API_KEY")
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer " + SECRET)
        self.assertNotIn(SECRET, repr(provider))

    def test_t15_invalid_config_fails_before_any_transport_call(self):
        from access_intake.openai_provider import ConfigurationError

        invalid = [
            {**CONFIG, "model": ""}, {key: value for key, value in CONFIG.items() if key != "model"},
            *[{**CONFIG, "timeout": value} for value in (0, -1, float("inf"), float("nan"))],
            *[{**CONFIG, "output_limit": value} for value in (0, -1, True)],
            {**CONFIG, "endpoint": "http://provider.invalid/responses"},
            {**CONFIG, "endpoint": "https://user:password@provider.invalid/responses"},
        ]
        for index, config in enumerate(invalid):
            with self.subTest(case=index):
                transport = FakeHTTP()
                with self.assertRaises(ConfigurationError):
                    self.provider(transport, config)
                self.assertEqual(transport.calls, [])

    def test_t15_request_evidence_is_redacted_and_labels_its_limits(self):
        transport = FakeHTTP()
        provider = self.provider(transport)
        request = build_request(self.source())
        evidence = provider.request_evidence(request)
        self.assertNotIn(SECRET, json.dumps(evidence))
        self.assertEqual(evidence["request"]["headers"]["Authorization"], "Bearer [REDACTED]")
        self.assertEqual(evidence["request"]["body"]["model"], "fixture-requested-model")
        self.assertEqual(evidence["evidence_kind"], "constructed_request")
        self.assertFalse(evidence["live_observation"])
        self.assertEqual(transport.calls, [], "evidence generation itself must not invoke the transport")
        for signal in ("completion", "refusal", "interruption", "http_errors", "schema_validation", "grounding"):
            self.assertTrue(evidence["response_handling"][signal])

    def test_t15_real_cli_path_uses_injected_http_and_keeps_credentials_out_of_state(self):
        from access_intake.cli import main

        transport = FakeHTTP()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.sqlite3"
            args = ["intake", str(ROOT / "materials/requests/IT-001.json"), "--provider", "openai",
                    "--model", "fixture-requested-model", "--endpoint", CONFIG["endpoint"],
                    "--timeout", "12", "--output-limit", "4096", "--temperature", "0.2", "--workers", "1", "--state", str(state)]
            log = io.StringIO()
            with mock.patch("access_intake.cli.load_api_key", return_value=SECRET), \
                 mock.patch("access_intake.cli.HTTPTransport", return_value=transport), \
                 contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)
                with Store(state) as store:
                    first = store.current_records()[0]
                self.assertEqual(main(["retry", "--attempt", first["attempt_id"], "--state", str(state)]), 0)
            self.assertEqual(len(transport.calls), 2, "unchanged intake reuses; explicit replay invokes again")
            self.assertNotIn(SECRET, log.getvalue())
            with Store(state) as store:
                records = store.current_records()
                history = store.history("IT-001.json")
                self.assertEqual(len(history), 2)
                self.assertEqual(records[0]["response_source"], OBSERVED_SOURCE)
                self.assertEqual(records[0]["configuration"]["model"], "fixture-requested-model")
                self.assertEqual(records[0]["owner"], "System Owner")
                self.assertNotIn(SECRET, repr(history))
            for file in Path(directory).glob("state.sqlite3*"):
                self.assertNotIn(SECRET.encode(), file.read_bytes())
