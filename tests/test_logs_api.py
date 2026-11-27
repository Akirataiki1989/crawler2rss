import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from utils.logs_api import LogsApiClient, make_event, validate_event


class LogsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.spool = str(Path(self.temp.name) / "spool.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_event_id_keeps_original_payload(self):
        client = LogsApiClient(None, None, self.spool)
        first = make_event("INFO", "crawler.test", "first")
        second = {**first, "message": "replacement"}
        client._store(first, immediate=False)
        client._store(second, immediate=False)
        self.assertEqual(client.pending(), [first])

    def test_spool_eviction_prefers_oldest_lowest_level_and_protects_errors(self):
        client = LogsApiClient(None, None, self.spool, max_events=2)
        events = [make_event(level, "crawler.test", level) for level in ("INFO", "WARNING", "ERROR")]
        for event in events:
            client._store(event, immediate=False)
        self.assertEqual({event["level"] for event in client.pending()}, {"WARNING", "ERROR"})

    def test_payload_validation_rejects_unknown_fields_and_non_utc(self):
        event = make_event("INFO", "crawler.hello", "hello")
        with self.assertRaises(ValueError):
            validate_event({**event, "internal_secret": "no"})
        normalized = make_event("INFO", "crawler.hello", "hello", occurred_at="2026-01-01T08:00:00+08:00")
        self.assertEqual(normalized["occurred_at"], "2026-01-01T00:00:00.000Z")
        with self.assertRaises(ValueError):
            validate_event({**event, "occurred_at": "2026-01-01T00:00:00+08:00"})
        with self.assertRaises(ValueError):
            make_event("NOTICE", "crawler.hello", "hello")
        with self.assertRaises(ValueError):
            make_event("INFO", "Upper.Case", "invalid event name")

    def test_flush_requires_token_and_deletes_only_acknowledged_ids(self):
        opener = Mock()
        client = LogsApiClient("https://logs.invalid/ingest", None, self.spool, opener=opener)
        event1 = make_event("INFO", "crawler.one", "one")
        event2 = make_event("INFO", "crawler.two", "two")
        client._store(event1, immediate=False)
        client._store(event2, immediate=False)
        self.assertEqual(client.flush(), 0)
        opener.assert_not_called()

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"accepted_event_ids": [event1["event_id"]], "duplicate_event_ids": []}).encode()

        opener.return_value = Response()
        client.token = "test-token"
        self.assertEqual(client.flush(), 1)
        self.assertEqual(client.pending(), [event2])
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        body = json.loads(request.data)
        self.assertEqual(set(body), {"logs"})
        self.assertEqual(body["logs"], [event1, event2])

    def test_emit_builds_v1_log_with_optional_fields(self):
        client = LogsApiClient(None, None, self.spool)
        client.emit("ERROR", "crawler.fetch.failed", "request failed", immediate=False,
                    run_id="run-1", context={"url": "https://example.test"}, error_type="TimeoutError")
        log = client.pending()[0]
        self.assertEqual(log["schema_version"], 1)
        self.assertEqual(log["event"], "crawler.fetch.failed")
        self.assertEqual(log["occurred_at"][-1], "Z")
        self.assertEqual(log["context"], {"url": "https://example.test"})
        self.assertFalse({"timestamp", "logger", "metadata", "scraper"} & set(log))


if __name__ == "__main__":
    unittest.main()
