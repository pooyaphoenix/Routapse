"""Config store. Redis for multi-replica deployments, JSON file for a single node."""
import copy, json, os, threading
from urllib.parse import urlparse

from .config import settings


class FileStore:
    """JSON file store. Reads reuse the parsed file until its identity on disk changes, so edits made by
    another process (or by hand) are picked up on the next call; writes always start from a fresh parse."""

    def __init__(self, path: str):
        self.path, self.lock = path, threading.Lock()
        self._cache = None  # (path, signature, parsed)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _read(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _signature(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size

    def _load(self) -> dict:
        """Parsed contents for reading only; callers must not mutate it."""
        sig, cached = self._signature(), self._cache
        if cached and cached[0] == self.path and cached[1] == sig:
            return cached[2]
        data = self._read()
        self._cache = (self.path, sig, data)
        return data

    def _save(self, d: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, self.path)

    def list(self, coll):
        return copy.deepcopy(list(self._load().get(coll, {}).values()))

    def get(self, coll, id_):
        return copy.deepcopy(self._load().get(coll, {}).get(id_))

    def put(self, coll, id_, data):
        with self.lock:
            d = self._read()
            d.setdefault(coll, {})[id_] = data
            self._save(d)

    def delete(self, coll, id_):
        with self.lock:
            d = self._read()
            d.get(coll, {}).pop(id_, None)
            self._save(d)


class RedisStore:
    def __init__(self, url: str):
        import redis
        self.r = redis.Redis.from_url(url, decode_responses=True)

    def list(self, coll):
        return [json.loads(v) for v in self.r.hvals(f"routapse:{coll}")]

    def get(self, coll, id_):
        v = self.r.hget(f"routapse:{coll}", id_)
        return json.loads(v) if v else None

    def put(self, coll, id_, data):
        self.r.hset(f"routapse:{coll}", id_, json.dumps(data))

    def delete(self, coll, id_):
        self.r.hdel(f"routapse:{coll}", id_)


def make_store():
    u = settings.store_url
    if u.startswith("redis"):
        return RedisStore(u)
    return FileStore(urlparse(u).path if u.startswith("file:") else u)


store = make_store()
