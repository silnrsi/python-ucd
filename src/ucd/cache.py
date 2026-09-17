#!/usr/bin/python3

"""cache

A content-agnostic manager for a versioned, pickled data cache with an
offline-safe update path. Each cacheable class constructs a CacheManager,
supplying its own name, format version, remote URL and a builder callback
that fetches+parses a fresh instance. The manager handles path resolution,
freshness checks, versioned filenames, cleanup of stale-format files, and
load/save; it never inspects what it is pickling.

    from ucd.cache import CacheManager

    cache = CacheManager(
        name="ucdata",
        format_version=1,
        remote_url="http://www.unicode.org/Public/latest/ucdxml/ucd.all.flat.zip",
        builder=UCD.build_from_remote,   # callable() -> pickleable instance
        package="ucd",                   # for importlib.resources bundle lookup
    )
    obj = cache.load()                   # fresh check -> cache -> bundle -> remote
"""

import os, sys, bz2, pickle, glob, re
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import platformdirs


def _userroot():
    """True if running as an administrative/root user (writable system cache)."""
    if sys.platform == 'win32':
        import ctypes
        try:
            return ctypes.windll.shell32.IsUserAdmin() != 0
        except Exception:
            return False
    else:
        return os.geteuid() == 0


def _varcache():
    """System-wide cache directory for privileged installs."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("ProgramData", 'C:\\ProgramData'),
                            "python_ucd", "Cache")
    elif sys.platform == "darwin":
        return "/Library/Caches/python_ucd"
    else:
        return "/var/cache/python_ucd"


class CacheManager:
    def __init__(self, name, format_version, remote_url, builder,
                 cache_period=30, package=None, subdir="python_ucd"):
        """
        name          basename for the cache file, e.g. "ucdata"
        format_version integer owned by the content class; changes the filename
        remote_url    where build_from_remote's data comes from (for HEAD/GET)
        builder       callable() returning a fresh, pickleable instance
        cache_period  days before a HEAD freshness check is made
        package       importlib.resources package holding bundled data, or None
        subdir        cache subdirectory name under the platform cache root
        """
        self.name = name
        self.format_version = format_version
        self.remote_url = remote_url
        self.builder = builder
        self.cache_period = cache_period
        self.package = package
        self.subdir = subdir

    # ---- naming -----------------------------------------------------------

    def filename(self):
        return f"{self.name}_v{self.format_version}.pickle.bz2"

    def _offline(self):
        return getattr(sys, "frozen", False)

    # ---- path resolution --------------------------------------------------

    def _cache_dirs(self):
        dirs = []
        if _userroot():
            dirs.append(_varcache())
        dirs.append(platformdirs.user_cache_dir(self.subdir))
        if not _userroot():
            dirs.append("/var/cache/" + self.subdir)   # readable system cache
        return dirs

    def cache_path(self):
        """Return (read_path_or_None, write_path). read_path is the first
        existing cache file of the current format across candidate dirs;
        write_path is where we would save a fresh one."""
        fname = self.filename()
        dirs = self._cache_dirs()

        read_path = None
        for d in dirs:
            p = os.path.join(d, fname)
            if os.path.exists(p):
                read_path = p
                break

        write_dir = dirs[0]
        try:
            os.makedirs(write_dir, exist_ok=True)
        except OSError:
            pass
        return read_path, os.path.join(write_dir, fname)

    def _bundled_path(self):
        if not self.package:
            return None
        try:
            from importlib.resources import files
            p = files(self.package) / "data" / self.filename()
            return str(p) if p.is_file() else None
        except (ModuleNotFoundError, FileNotFoundError, TypeError):
            return None

    # ---- freshness --------------------------------------------------------

    def test_update(self):
        """True if the cache should be refreshed. Younger than cache_period ->
        assume current (no network). Otherwise HEAD the remote; if not newer,
        touch the cache mtime to reset the clock and return False."""
        if self._offline():
            return False
        cache_path, _ = self.cache_path()
        if cache_path is None or not os.path.exists(cache_path):
            return True

        mtime = datetime.fromtimestamp(os.path.getmtime(cache_path),
                                       tz=timezone.utc)
        if datetime.now(timezone.utc) - mtime < timedelta(days=self.cache_period):
            return False

        req = urllib.request.Request(self.remote_url, method="HEAD")
        try:
            with urllib.request.urlopen(req) as resp:
                remote_lm = resp.headers.get("Last-Modified")
        except urllib.error.URLError:
            return False

        if remote_lm and parsedate_to_datetime(remote_lm) > mtime:
            return True

        try:
            os.utime(cache_path, None)
        except PermissionError:
            pass
        return False

    # ---- fetch / save / prune --------------------------------------------

    def force_update(self):
        """Build a fresh instance from remote, save to cache, prune stale
        formats. Save failures (read-only dir) are non-fatal; the built
        object is returned regardless."""
        obj = self.builder()
        _, write_path = self.cache_path()
        try:
            self.save(obj, write_path)
            self._cleanup_old_caches(os.path.dirname(write_path))
        except OSError:
            pass
        return obj

    def save(self, obj, localfile):
        with bz2.open(localfile, "wb") as outf:
            pickle.dump(obj, outf, protocol=4)   # 3.4+ floor for cross-version load

    def _load_file(self, localfile):
        with bz2.open(localfile, "rb") as inf:
            return pickle.load(inf)

    def _cleanup_old_caches(self, cache_dir):
        """Best-effort removal of stale-format cache files we own."""
        keep = self.filename()
        pattern = os.path.join(cache_dir, f"{re.escape(self.name)}_v*.pickle.bz2")
        exact = re.compile(rf"{re.escape(self.name)}_v\d+\.pickle\.bz2\Z")
        for path in glob.glob(pattern):
            base = os.path.basename(path)
            if base == keep or not exact.match(base):
                continue
            try:
                os.remove(path)
            except OSError:
                pass

    # ---- top-level load ---------------------------------------------------

    def load(self, localfile=None):
        """Resolve and load an instance: explicit file, else fresh-if-stale
        remote, else existing cache, else bundled data, else (if online) a
        remote build. Returns the loaded object, or None if nothing is
        available (caller decides how to represent an empty instance)."""
        if localfile is not None:
            return self._load_file(localfile) if os.path.exists(localfile) else None

        if not self._offline() and self.test_update():
            try:
                return self.force_update()
            except (urllib.error.URLError, OSError):
                pass

        read_path, _ = self.cache_path()
        path = read_path or self._bundled_path()
        if path is not None:
            try:
                return self._load_file(path)
            except (OSError, pickle.UnpickleError):
                pass

        if not self._offline():
            try:
                return self.force_update()
            except (urllib.error.URLError, OSError):
                pass
        return None
