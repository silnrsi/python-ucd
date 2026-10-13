#!/usr/bin/python3

"""cache

A content-agnostic manager for a versioned, pickled data cache with an
offline-safe update path. Each cacheable class constructs a CacheManager,
supplying its own name, format version, a builder callback that
fetches+parses a fresh instance and a version_of callback that reports
which Unicode version an instance holds. The manager handles path
resolution, freshness checks against the current Unicode release,
versioned filenames, cleanup of stale-format files, and load/save; beyond
version_of it never inspects what it is pickling.

    from ucd.cache import CacheManager

    cache = CacheManager(
        name="ucdata",
        format_version=1,
        builder=UCD.build_from_remote,   # callable() -> pickleable instance
        version_of=lambda o: o.metadata.get("ucdversion"),
        package="ucdinfo",               # for importlib.resources bundle lookup
    )
    obj = cache.load()                   # cache -> bundle -> remote, each
                                         # checked against the latest release
"""

import os, sys, bz2, pickle, glob, re, shutil
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
import platformdirs

# Redirects to /versions/UnicodeX.Y.Z/ for the current release; the same
# release that the .../latest/... data URLs serve.
LATEST_VERSION_URL = "https://www.unicode.org/versions/latest/"

# Set (to anything but "" or "0") to never access the network: use the user
# cache if there is one, else the data shipped with the module. Intended for
# docker images etc.
OFFLINE_ENV = "UCDINFO_OFFLINE"


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


def _vtuple(v):
    """'16.0.0' -> (16, 0, 0); None if v isn't a dotted version."""
    if not v or not re.fullmatch(r"\d+(?:\.\d+)*", str(v)):
        return None
    return tuple(int(x) for x in str(v).split("."))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


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
    def __init__(self, name, format_version, builder, version_of,
                 cache_period=30, package="ucdinfo", subdir="python_ucd",
                 version_url=LATEST_VERSION_URL, timeout=3):
        """
        name          basename for the cache file, e.g. "ucdata"
        format_version integer owned by the content class; changes the filename
        builder       callable() returning a fresh, pickleable instance
        version_of    callable(obj) returning the Unicode version string obj
                      holds, or None if unknown (treated as out of date)
        cache_period  days before a cached file is rechecked against remote
        package       importlib.resources package holding bundled data, or None
        subdir        cache subdirectory name under the platform cache root
        version_url   URL that redirects to the current release's version page
        timeout       seconds to wait for the version check
        """
        self.name = name
        self.format_version = format_version
        self.builder = builder
        self.version_of = version_of
        self.cache_period = cache_period
        self.package = package
        self.subdir = subdir
        self.version_url = version_url
        self.timeout = timeout

    # ---- naming -----------------------------------------------------------

    def filename(self):
        return f"{self.name}_v{self.format_version}.pickle.bz2"

    def _offline(self):
        return (getattr(sys, "frozen", False)
                or os.environ.get(OFFLINE_ENV, "") not in ("", "0"))

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

    def remote_version(self):
        """Current Unicode release as a tuple, read from the Location header
        of version_url's redirect without following it. None if unknown."""
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(self.version_url, method="HEAD")
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                loc = resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
        except (urllib.error.URLError, OSError):
            return None
        m = re.search(r"Unicode(\d+(?:\.\d+)*)", loc or "")
        return _vtuple(m.group(1)) if m else None

    def is_current(self, obj):
        """True if obj holds the current Unicode release, False if it is
        older (or its version is unknown), None if the release can't be
        determined (e.g. no network)."""
        remote = self.remote_version()
        if remote is None:
            return None
        local = _vtuple(self.version_of(obj))
        return local is not None and local >= remote

    def _recent(self, path):
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        return datetime.now(timezone.utc) - mtime < timedelta(days=self.cache_period)

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

    def _try_load(self, path):
        if path is None:
            return None
        try:
            return self._load_file(path)
        except (OSError, EOFError, pickle.PickleError):
            return None

    def _try_update(self, fallback):
        try:
            return self.force_update()
        except (urllib.error.URLError, OSError):
            return fallback

    def _seed_cache(self):
        """No usable cache: seed it with the bundled data if that holds the
        current release, otherwise build from remote (force_update caches
        it). Either way the cache period then runs from now. If the release
        can't be determined (no network) the bundled data is used uncached,
        so the next run tries again."""
        bundled_path = self._bundled_path()
        obj = self._try_load(bundled_path)
        if self._offline():
            return obj
        current = self.is_current(obj) if obj is not None else False
        if current is False:
            return self._try_update(obj)
        if current:
            _, write_path = self.cache_path()
            try:
                shutil.copyfile(bundled_path, write_path)
                self._cleanup_old_caches(os.path.dirname(write_path))
            except OSError:
                pass
        return obj

    def load(self, localfile=None):
        """Resolve and load an instance: explicit file, else the user cache,
        seeding it first if there isn't one.

        With no cache, the bundled data is checked against the current
        Unicode release: if current it is copied into the cache, otherwise
        fresh data is built from remote and cached. A cache file younger
        than cache_period is used without any network access; an older one
        is rechecked against the current release and rebuilt if out of date.
        If the network is unavailable whatever local data exists is used.

        If UCDINFO_OFFLINE is set the network is never accessed: the cache
        is used if there is one, else the bundled data (which is then not
        copied into the cache).

        Returns the loaded object, or None if nothing is available (caller
        decides how to represent an empty instance)."""
        if localfile is not None:
            return self._load_file(localfile) if os.path.exists(localfile) else None

        read_path, _ = self.cache_path()
        obj = self._try_load(read_path)
        if obj is None:
            return self._seed_cache()
        if self._offline() or self._recent(read_path):
            return obj
        current = self.is_current(obj)
        if current is False:
            return self._try_update(obj)
        if current:
            try:
                os.utime(read_path, None)
            except OSError:
                pass
        return obj
