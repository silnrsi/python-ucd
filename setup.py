import os, sys
from setuptools import setup
from setuptools.command.build_py import build_py

class build_py_with_data(build_py):
    def run(self):
        super().run()
        if os.environ.get("UCD_SKIP_FETCH") == "1":
            self.announce("UCD_SKIP_FETCH=1: shipping without bundled data", level=2)
            return

        # Editable installs (setuptools >= 64) don't populate build_lib the
        # normal way -- write directly into the source tree instead so the
        # editable-installed package can actually find the data.
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
        if getattr(self, "editable_mode", False):
            pkg_dir = os.path.join(base_dir, "ucdinfo", "data")
            src_root = base_dir
        else:
            pkg_dir = os.path.join(self.build_lib, "ucdinfo", "data")
            src_root = self.build_lib

        os.makedirs(pkg_dir, exist_ok=True)

        if src_root not in sys.path:
            sys.path.insert(0, src_root)

        print(f"{pkg_dir=}, {src_root=}")
        try:
            from ucdinfo import UCD
            out = os.path.join(pkg_dir, UCD._cache_filename())
            self.announce("Fetching UCD data...", level=2)
            UCD.build_from_remote().save(out)
            self.announce(f"Wrote {out}", level=2)
        except (ModuleNotFoundError, ImportError):
            pass

setup(cmdclass={"build_py": build_py_with_data})
