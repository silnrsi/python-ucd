#!/usr/bin/python3
import re, os, argparse
from struct import pack, unpack
import pickle, bz2, zipfile, io
from .cache import CacheManager

__all__ = ['DUCET', 'get_sortkey']
FORMAT_VERSION = "0.1"

def _rebuild_ducet(items=None, implicits=None, metadata=None):
    obj = dict.__new__(DUCET)
    if items is not None:
        obj.update(items)
    obj.implicits = implicits if implicits is not None else []
    obj.metadata = metadata if metadata is not None else {}
    return obj


class SortKey(list):
    keyslots = {"primary": 0, "secondary": 1, "tertiary": 2}
    def __init__(self, *a):
        super().__init__(*a)

    def __getattr__(self, key):
        i = self.keyslots.get(key, -1)
        if i >= 0:
            return super().__getitem__(i)

    def __str__(self):
        res = []
        for k in self:
            res.append(".".join("{:04X}".format(*unpack(">H", e)) for e in (bytes(a) for a in zip(k[::2], k[1::2]))))
        return str(res)


class DUCET(dict):
    _remote_url = "http://www.unicode.org/Public/UCA/latest/allkeys.txt"

    def __new__(cls, localfile=None, cache_period=30):
        cache = cls._make_cache(cache_period)
        if localfile is None:
            obj = cache.load()
            if obj is None:
                obj = _rebuild_ducet()
            return obj
        # explicit file: .xml/.zip are parsed in __init__; a pickled .bz2
        # is loaded here so __init__ can no-op on it.
        if localfile.endswith(".bz2"):
            loaded = cache.load(localfile=localfile)
            if loaded is not None:
                return loaded
        obj = _rebuild_ducet()
        return obj

    def __init__(self, localfile=None, cache_period=30):
        if not hasattr(self, "metadata"):
            self.metadata = {}
        if localfile is None or localfile.endswith(".bz2"):
            return
        elif localfile.endswith(".txt"):
            with open(localfile) as inf:
                self._loadducet(inf)
        elif localfile.endswith('.zip'):
            with zipfile.ZipFile(localfile, 'r') as z:
                firstf = z.namelist()[0]
                with z.open(firstf) as inf:
                    self._loadducet(inf)

    def __reduce__(self):
        return (_rebuild_ducet, (dict(self), self.implicits, getattr(self, "metadata", {})))

    @classmethod
    def _make_cache(cls, cache_period=30):
        return CacheManager(
            name="ducetdata",
            format_version=FORMAT_VERSION,
            remote_url=cls._remote_url,
            builder=cls.build_from_remote,
            cache_period=cache_period,
        )

    @classmethod
    def force_update(cls):
        cache = cls._make_cache()
        return cache.force_update()

    @classmethod
    def build_from_remote(cls):
        """Fetch the remote zip and return a fresh instance. Touches no cache;
        called by CacheManager.force_update / the build hook."""
        import urllib.request
        with urllib.request.urlopen(cls._remote_url) as resp:
            data = resp.read().decode("utf-8")
        obj = _rebuild_ducet()
        with io.StringIO(data) as inf:
            obj._loadducet(inf)
        return obj

    def save(self, localfile):
        if localfile.endswith(".bz2"):
            with bz2.open(localfile, "wb") as outf:
                pickle.dump(self, outf, protocol=4)
        elif localfile.endswith(".pickle"):
            with open(localfile, "wb") as outf:
                pickle.dump(self, outf, protocol=4)
        else:
            raise ValueError("localfile must end in .bz2 or .pickle")

    @classmethod
    def _cache_filename(cls):
        return f"ducetdata_pickle_{FORMAT_VERSION}.bz2"

    @classmethod
    def _bundled_path(cls):
        try:
            from importlib.resources import files
            p = files("ucdinfo") / "data" / cls._cache_filename()
            return str(p) if p.is_file() else None
        except (ModuleNotFoundError, FileNotFoundError, TypeError):
            return None

    def _loadducet(self, inf, file_date=None):
        self.implicits = []
        for l in inf.readlines():
            line = l.split("#", 1)[0].rstrip()
            if not line or line.startswith("@version"):
                continue
            if line.startswith("@implicitweights "):
                chrange, base = line[17:].split(";")
                start, end = chrange.split("..")
                self.implicits.append((int(start, 16), int(end, 16), int(base, 16)))
                continue
            k, v = line.split(";", 1)
            key = "".join(chr(int(x, 16)) for x in k.rstrip().split())
            vals = []
            vs = re.findall(r"\[([.*])([0-9a-fA-F]{4})\.([0-9a-fA-F]{4})\.([0-9a-fA-F]{4})\]\s*", v.lstrip())
            for vm in vs:
                vals.append(b"".join(pack(">H", int(x, 16)) for x in vm[1:]))
            self[key] = b"".join(vals)

    def lookup(self, s):
        return self[s]

    def sortkey(self, txt):
        res = []
        colls = []
        currk = ""
        for c in txt:
            if currk+c in self:
                currk += c
                continue
            if not currk:
                continue
            colls.append(self.lookup(currk))
            currk = c
        if currk:
            colls.append(self.lookup(currk))
        for i in range(3):
            res.append(b"".join(bytes(a) for k in colls for a in zip(k[2*i::6], k[2*i+1::6])))
        return SortKey(res)

local_ducet = None
def _get_local_ducet():
    global local_ducet
    if local_ducet is None:
        local_ducet = DUCET()
    return local_ducet

def get_sortkey(s):
    return _get_local_ducet().sortkey(s)

def load_ducet(filename):
    """ Ensures the global ducet is loaded into memory """
    _get_local_ducet().__init__(localfile=filename)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("code",nargs="*",help="USVs of string for key")
    args = parser.parse_args()

    s = "".join((chr(int(x, 16)) for x in args.code))
    k = get_sortkey(s)
    print(f"{s}: {k}")

if __name__ == "__main__":
    main()
