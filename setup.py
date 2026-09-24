import os, sys
from setuptools import setup
from setuptools.command.build_py import build_py

class build_py_with_data(build_py):
    def run(self):
        super().run()
        pkg_dir = os.path.join(self.build_lib, "ucd", "data")
        os.makedirs(pkg_dir, exist_ok=True)

        if os.environ.get("UCD_SKIP_FETCH") == "1":
            self.announce("UCD_SKIP_FETCH=1: shipping wheel without bundled data",
                          level=2)
            return

        sys.path.insert(0, self.build_lib)
        from ucdinfo import UCD
        out = os.path.join(pkg_dir, UCD._cache_filename())
        self.announce("Fetching UCD data for wheel...", level=2)
        UCD.build_from_remote().save(out)
        self.announce(f"Wrote {out}", level=2)


setup(cmdclass={"build_py": build_py_with_data})
