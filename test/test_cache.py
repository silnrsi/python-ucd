import os, bz2, pickle, shutil, socket, tempfile, threading, unittest
import http.server, urllib.error
from unittest import mock
from ucdinfo.cache import CacheManager, _vtuple, OFFLINE_ENV
from ucdinfo import DUCET


class Data(dict):
    """Stand-in for a cached dataset: just carries a version."""
    def __init__(self, version):
        super().__init__()
        self.metadata = {"version": version}


def _write(path, obj):
    with bz2.open(path, "wb") as outf:
        pickle.dump(obj, outf)


class CacheTests(unittest.TestCase):
    """Exercise CacheManager.load() with the network calls replaced: the
    current release is self.release (None means unreachable) and builds
    return Data(self.release)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.cachedir = os.path.join(self.tmpdir, "cache")
        self.bundle = os.path.join(self.tmpdir, "bundle.pickle.bz2")
        self.release = "18.0.0"
        self.checks = 0
        self.builds = 0
        self.build_error = None
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(OFFLINE_ENV, None)

    def _remote_version(self):
        self.checks += 1
        return _vtuple(self.release)

    def _build(self):
        self.builds += 1
        if self.build_error is not None:
            raise self.build_error
        return Data(self.release)

    def manager(self):
        c = CacheManager("test", 1, self._build,
                         lambda o: o.metadata.get("version"), package=None)
        c._cache_dirs = lambda: [self.cachedir]
        c._bundled_path = lambda: self.bundle if os.path.exists(self.bundle) else None
        c.remote_version = self._remote_version
        return c

    def cachefile(self):
        return os.path.join(self.cachedir, "test_v1.pickle.bz2")

    def set_bundle(self, version):
        _write(self.bundle, Data(version))

    def set_cache(self, version, age_days=0):
        os.makedirs(self.cachedir, exist_ok=True)
        _write(self.cachefile(), Data(version))
        if age_days:
            t = os.path.getmtime(self.cachefile()) - age_days * 86400
            os.utime(self.cachefile(), (t, t))

    def cached_version(self):
        with bz2.open(self.cachefile(), "rb") as inf:
            return pickle.load(inf).metadata["version"]

    def assertLoads(self, version):
        obj = self.manager().load()
        self.assertIsNotNone(obj)
        self.assertEqual(obj.metadata["version"], version)

    # ---- first run: no cache -----------------------------------------------

    def test_seed_from_current_bundle(self):
        self.set_bundle("18.0.0")
        self.assertLoads("18.0.0")
        self.assertEqual(self.builds, 0)
        self.assertEqual(self.cached_version(), "18.0.0")

    def test_seed_from_stale_bundle_downloads(self):
        self.set_bundle("17.0.0")
        self.assertLoads("18.0.0")
        self.assertEqual(self.builds, 1)
        self.assertEqual(self.cached_version(), "18.0.0")

    def test_seed_without_bundle_downloads(self):
        self.assertLoads("18.0.0")
        self.assertEqual(self.checks, 0)
        self.assertEqual(self.builds, 1)
        self.assertEqual(self.cached_version(), "18.0.0")

    def test_seed_unknown_bundle_version_downloads(self):
        self.set_bundle(None)
        self.assertLoads("18.0.0")
        self.assertEqual(self.builds, 1)

    def test_seed_release_unreachable_uses_bundle_uncached(self):
        self.set_bundle("17.0.0")
        self.release = None
        self.assertLoads("17.0.0")
        self.assertEqual(self.builds, 0)
        self.assertFalse(os.path.exists(self.cachefile()))

    def test_seed_download_fails_uses_bundle(self):
        self.set_bundle("17.0.0")
        self.build_error = urllib.error.URLError("down")
        self.assertLoads("17.0.0")

    def test_seed_removes_stale_format_cache(self):
        os.makedirs(self.cachedir)
        old = os.path.join(self.cachedir, "test_v0.pickle.bz2")
        _write(old, Data("15.0.0"))
        self.set_bundle("18.0.0")
        self.assertLoads("18.0.0")
        self.assertFalse(os.path.exists(old))

    def test_seeded_cache_is_not_rechecked(self):
        self.set_bundle("18.0.0")
        self.manager().load()
        self.release = "19.0.0"
        self.assertLoads("18.0.0")
        self.assertEqual(self.checks, 1)

    # ---- existing cache ----------------------------------------------------

    def test_recent_cache_skips_check(self):
        self.set_cache("17.0.0", age_days=29)
        self.assertLoads("17.0.0")
        self.assertEqual(self.checks, 0)

    def test_old_current_cache_is_touched(self):
        self.set_cache("18.0.0", age_days=31)
        self.assertLoads("18.0.0")
        self.assertEqual(self.builds, 0)
        self.assertLoads("18.0.0")
        self.assertEqual(self.checks, 1)

    def test_old_stale_cache_is_rebuilt(self):
        self.set_cache("17.0.0", age_days=31)
        self.assertLoads("18.0.0")
        self.assertEqual(self.cached_version(), "18.0.0")

    def test_old_cache_release_unreachable(self):
        self.set_cache("17.0.0", age_days=31)
        self.release = None
        self.assertLoads("17.0.0")
        self.assertEqual(self.builds, 0)

    def test_cache_preferred_over_bundle(self):
        self.set_cache("17.0.0")
        self.set_bundle("18.0.0")
        self.assertLoads("17.0.0")

    def test_corrupt_cache_reseeds(self):
        os.makedirs(self.cachedir)
        with open(self.cachefile(), "wb") as outf:
            outf.write(b"not a pickle")
        self.set_bundle("18.0.0")
        self.assertLoads("18.0.0")
        self.assertEqual(self.cached_version(), "18.0.0")

    # ---- UCDINFO_OFFLINE ---------------------------------------------------

    def test_offline_uses_old_cache(self):
        os.environ[OFFLINE_ENV] = "1"
        self.set_cache("17.0.0", age_days=365)
        self.assertLoads("17.0.0")
        self.assertEqual((self.checks, self.builds), (0, 0))

    def test_offline_uses_bundle_uncached(self):
        os.environ[OFFLINE_ENV] = "1"
        self.set_bundle("17.0.0")
        self.assertLoads("17.0.0")
        self.assertEqual((self.checks, self.builds), (0, 0))
        self.assertFalse(os.path.exists(self.cachefile()))

    def test_offline_nothing_available(self):
        os.environ[OFFLINE_ENV] = "1"
        self.assertIsNone(self.manager().load())
        self.assertEqual(self.builds, 0)

    def test_offline_zero_means_online(self):
        os.environ[OFFLINE_ENV] = "0"
        self.set_bundle("17.0.0")
        self.assertLoads("18.0.0")

    # ---- explicit file -----------------------------------------------------

    def test_localfile(self):
        path = os.path.join(self.tmpdir, "mine.pickle.bz2")
        _write(path, Data("16.0.0"))
        obj = self.manager().load(localfile=path)
        self.assertEqual(obj.metadata["version"], "16.0.0")
        self.assertEqual((self.checks, self.builds), (0, 0))


class RemoteVersionTests(unittest.TestCase):
    """remote_version() against a local server standing in for
    unicode.org/versions/latest/."""

    def setUp(self):
        self.location = "/versions/Unicode18.0.0/"
        test = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(302 if test.location else 200)
                if test.location:
                    self.send_header("Location", test.location)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/versions/latest/"

    def manager(self, url):
        return CacheManager("test", 1, None, None, version_url=url, timeout=2)

    def test_redirect(self):
        self.assertEqual(self.manager(self.url).remote_version(), (18, 0, 0))

    def test_absolute_redirect(self):
        self.location = "https://www.unicode.org/versions/Unicode17.0.0/"
        self.assertEqual(self.manager(self.url).remote_version(), (17, 0, 0))

    def test_no_redirect(self):
        self.location = None
        self.assertIsNone(self.manager(self.url).remote_version())

    def test_unreachable(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.assertIsNone(self.manager(f"http://127.0.0.1:{port}/").remote_version())


class VersionTests(unittest.TestCase):

    def test_vtuple(self):
        self.assertEqual(_vtuple("16.0.0"), (16, 0, 0))
        self.assertEqual(_vtuple("9"), (9,))
        self.assertIsNone(_vtuple(None))
        self.assertIsNone(_vtuple(""))
        self.assertIsNone(_vtuple("16.0.0a"))
        self.assertLess(_vtuple("9.0.0"), _vtuple("10.0.0"))

    def test_ducet_records_version(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as outf:
            outf.write("@version 18.0.0\n0041 ; [.2075.0020.0008] # A\n")
        self.addCleanup(os.remove, outf.name)
        d = DUCET(outf.name)
        self.assertEqual(d.metadata["version"], "18.0.0")
        self.assertIn("A", d)


if __name__ == "__main__":
    unittest.main()
