import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.store import FileStore


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "nested", "store.json")
        self.store = FileStore(self.path)

    def parses(self):
        return patch("app.store.json.load", wraps=json.load)

    def test_put_get_list_delete_round_trip(self):
        self.assertEqual(self.store.list("models"), [])
        self.assertIsNone(self.store.get("models", "a"))
        self.store.put("models", "a", {"id": "a"})
        self.store.put("models", "b", {"id": "b"})
        self.assertEqual(self.store.get("models", "a"), {"id": "a"})
        self.assertEqual(len(self.store.list("models")), 2)
        self.store.delete("models", "a")
        self.store.delete("models", "missing")
        self.assertEqual(self.store.list("models"), [{"id": "b"}])

    def test_unchanged_file_is_parsed_once(self):
        self.store.put("models", "a", {"id": "a"})
        with self.parses() as load:
            for _ in range(5):
                self.store.get("models", "a")
                self.store.list("models")
        self.assertEqual(load.call_count, 1)

    def test_write_is_visible_to_the_next_read(self):
        self.store.put("models", "a", {"v": 1})
        self.assertEqual(self.store.get("models", "a"), {"v": 1})
        self.store.put("models", "a", {"v": 2})
        self.assertEqual(self.store.get("models", "a"), {"v": 2})
        self.store.delete("models", "a")
        self.assertIsNone(self.store.get("models", "a"))

    def test_changes_made_by_another_store_instance_are_seen(self):
        self.store.put("models", "a", {"v": 1})
        self.assertEqual(self.store.get("models", "a"), {"v": 1})
        FileStore(self.path).put("models", "a", {"v": 2})
        self.assertEqual(self.store.get("models", "a"), {"v": 2})

    def test_in_place_edit_of_the_file_is_seen(self):
        self.store.put("models", "a", {"v": 1})
        self.store.get("models", "a")
        with open(self.path, "w") as f:
            json.dump({"models": {"a": {"v": 22222}}}, f)
        self.assertEqual(self.store.get("models", "a"), {"v": 22222})

    def test_file_removed_after_being_cached_reads_as_empty(self):
        self.store.put("models", "a", {"v": 1})
        self.store.get("models", "a")
        os.remove(self.path)
        self.assertIsNone(self.store.get("models", "a"))

    def test_switching_path_does_not_reuse_the_cache(self):
        self.store.put("models", "a", {"v": 1})
        self.store.get("models", "a")
        self.store.path = os.path.join(self.dir.name, "other.json")
        self.assertIsNone(self.store.get("models", "a"))

    def test_callers_cannot_corrupt_the_cache_by_mutating_results(self):
        self.store.put("providers", "p", {"api_key": "secret", "tags": ["x"]})
        self.store.get("providers", "p")["api_key"] = "***"
        self.store.list("providers")[0]["tags"].append("y")
        self.assertEqual(self.store.get("providers", "p"), {"api_key": "secret", "tags": ["x"]})

    def test_put_does_not_alias_the_caller_dict(self):
        data = {"v": 1}
        self.store.put("models", "a", data)
        self.store.get("models", "a")
        data["v"] = 99
        self.assertEqual(self.store.get("models", "a"), {"v": 1})


if __name__ == "__main__":
    unittest.main()
