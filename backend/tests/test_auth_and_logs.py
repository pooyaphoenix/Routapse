import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app import auth, reqlog


class AuthTests(unittest.TestCase):
    def test_empty_token_disables_auth(self):
        with patch.object(auth.settings, "admin_token", ""):
            auth.admin_auth(None)

    def test_valid_bearer_token_is_accepted(self):
        with patch.object(auth.settings, "gateway_key", "secret"):
            auth.gateway_auth("Bearer secret")

    def test_missing_or_wrong_token_is_rejected(self):
        with patch.object(auth.settings, "admin_token", "secret"):
            for header in (None, "Bearer wrong", "secret-without-prefix"):
                with self.subTest(header=header), self.assertRaises(HTTPException) as error:
                    auth.admin_auth(header)
                self.assertEqual(error.exception.status_code, 401)


class RequestLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = patch.object(reqlog.settings, "log_dir", self.dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def event(self, **changes):
        return {
            "source": "gateway",
            "router_id": "support",
            "status": "ok",
            "request": {"messages": [{"role": "user", "content": "hello"}]},
            "response": {"text": "world", "usage": {}},
            **changes,
        }

    def test_event_is_appended_as_json_line(self):
        reqlog.log_event(self.event())
        reqlog.log_event(self.event(source="studio"))
        files = os.listdir(self.dir.name)
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.dir.name, files[0])) as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual([x["source"] for x in lines], ["gateway", "studio"])
        self.assertTrue(all("id" in x and "ts" in x for x in lines))

    def test_bodies_can_be_disabled(self):
        with patch.object(reqlog.settings, "log_bodies", False):
            reqlog.log_event(self.event())
        entry = reqlog.read_logs()[0]
        self.assertEqual(entry["request"], {"messages": 1, "chars": 5})
        self.assertEqual(entry["response"]["chars"], 5)
        self.assertNotIn("hello", json.dumps(entry))

    def test_read_logs_returns_newest_first_with_filters_and_offset(self):
        for i in range(3):
            reqlog.log_event(self.event(note=f"entry-{i}"))
        reqlog.log_event(self.event(source="studio", router_id="other", note="needle"))
        self.assertEqual(reqlog.read_logs()[0]["note"], "needle")
        self.assertEqual([x["note"] for x in reqlog.read_logs(source="gateway")], ["entry-2", "entry-1", "entry-0"])
        self.assertEqual(len(reqlog.read_logs(router_id="other")), 1)
        self.assertEqual(reqlog.read_logs(q="NEEDLE")[0]["note"], "needle")
        self.assertEqual([x["note"] for x in reqlog.read_logs(limit=2, offset=1)], ["entry-2", "entry-1"])

    def test_corrupt_lines_are_skipped(self):
        reqlog.log_event(self.event(note="good"))
        path = os.path.join(self.dir.name, os.listdir(self.dir.name)[0])
        with open(path, "a") as f:
            f.write("not json\n")
        self.assertEqual([x["note"] for x in reqlog.read_logs()], ["good"])

    def test_unwritable_log_dir_does_not_raise(self):
        with patch.object(reqlog.settings, "log_dir", os.path.join(self.dir.name, "file")):
            open(reqlog.settings.log_dir, "w").close()
            reqlog.log_event(self.event())

    def test_info_lists_files(self):
        reqlog.log_event(self.event())
        info = reqlog.info()
        self.assertEqual(len(info["files"]), 1)
        self.assertTrue(info["bodies"])

    def write_day(self, day, *notes):
        with open(os.path.join(self.dir.name, f"requests-{day}.jsonl"), "w", encoding="utf-8") as f:
            for note in notes:
                f.write(json.dumps({"note": note, "source": "gateway"}, ensure_ascii=False) + "\n")

    def test_reverse_reader_is_correct_across_chunk_boundaries_and_multibyte_text(self):
        notes = [f"سلام-{i}-" + "é" * (i % 7) for i in range(200)]
        self.write_day("2026-01-01", *notes)
        with patch.object(reqlog, "CHUNK", 13):
            got = [x["note"] for x in reqlog.read_logs(limit=500)]
        self.assertEqual(got, notes[::-1])

    def test_reverse_reader_drops_only_the_cut_line_when_over_the_byte_cap(self):
        notes = [f"entry-{i:03d}" for i in range(100)]
        self.write_day("2026-01-01", *notes)
        path = os.path.join(self.dir.name, "requests-2026-01-01.jsonl")
        lines = list(reqlog._reverse_lines(path, max_bytes=300))
        parsed = [json.loads(line)["note"] for line in lines]
        self.assertEqual(parsed, notes[::-1][: len(parsed)])
        self.assertTrue(0 < len(parsed) < 100)

    def test_small_page_reads_little_of_a_large_file(self):
        self.write_day("2026-01-01", *[f"entry-{i}" for i in range(20000)])
        path = os.path.join(self.dir.name, "requests-2026-01-01.jsonl")
        reads = []
        real_open = open

        def counting_open(p, *a, **kw):
            f = real_open(p, *a, **kw)
            if p == path:
                original = f.read
                f.read = lambda n=-1: (reads.append(n), original(n))[1]
            return f

        with patch("builtins.open", counting_open):
            self.assertEqual(len(reqlog.read_logs(limit=5)), 5)
        self.assertLessEqual(sum(reads), reqlog.CHUNK)

    def test_empty_and_unterminated_files_are_handled(self):
        self.write_day("2026-01-01")
        with open(os.path.join(self.dir.name, "requests-2026-01-02.jsonl"), "w") as f:
            f.write(json.dumps({"note": "no-newline"}))
        self.assertEqual([x["note"] for x in reqlog.read_logs()], ["no-newline"])

    def test_retention_is_off_by_default(self):
        self.write_day("2000-01-01", "ancient")
        self.assertEqual(reqlog.prune(), 0)
        self.assertEqual(len(os.listdir(self.dir.name)), 1)

    def test_prune_removes_only_expired_log_files(self):
        for day in ("2026-03-01", "2026-03-09", "2026-03-10", "2026-03-11"):
            self.write_day(day, "x")
        open(os.path.join(self.dir.name, "notes.txt"), "w").close()
        with patch.object(reqlog.settings, "log_retention_days", 2):
            removed = reqlog.prune(datetime(2026, 3, 12, 8, tzinfo=timezone.utc))
        self.assertEqual(removed, 2)
        self.assertEqual(sorted(os.listdir(self.dir.name)),
                         ["notes.txt", "requests-2026-03-10.jsonl", "requests-2026-03-11.jsonl"])

    def test_log_event_prunes_at_most_once_per_interval(self):
        self.write_day("2000-01-01", "ancient")
        with patch.object(reqlog.settings, "log_retention_days", 1), patch.object(reqlog, "_last_prune", float("-inf")):
            reqlog.log_event(self.event())
            self.assertNotIn("requests-2000-01-01.jsonl", os.listdir(self.dir.name))
            self.write_day("1999-01-01", "ancient")
            reqlog.log_event(self.event())
            self.assertIn("requests-1999-01-01.jsonl", os.listdir(self.dir.name))


if __name__ == "__main__":
    unittest.main()
