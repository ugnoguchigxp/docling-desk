"""Release packaging retains only assets reachable from the published HTML entries.

The live source bundle keeps older hashes for open viewers. Filtering copies in
build/ or the sdist file list must never remove those live source files.
"""

from pathlib import Path

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


def active_assets(bundle: Path) -> set[str]:
    assets = bundle / "assets"
    candidates = {p.name: p for p in assets.iterdir() if p.is_file()} if assets.is_dir() else {}
    seen: set[str] = set()
    queue = [bundle / name for name in ("index.html", "embed.html", "print.html")]
    while queue:
        path = queue.pop()
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, target in candidates.items():
            if name not in seen and name in text:
                seen.add(name)
                queue.append(target)
    return seen


class BuildPy(build_py):
    def run(self) -> None:
        super().run()
        bundle = Path(self.build_lib) / "docling_desk/resources/static/frontend"
        keep = active_assets(bundle)
        if (bundle / "assets").is_dir():
            for path in (bundle / "assets").iterdir():
                if path.is_file() and path.name not in keep:
                    path.unlink()


class SourceDist(sdist):
    def get_file_list(self) -> None:
        super().get_file_list()
        bundle = Path("src/docling_desk/resources/static/frontend")
        keep = active_assets(bundle)
        prefix = bundle.as_posix() + "/assets/"
        self.filelist.files = [
            p for p in self.filelist.files if not p.startswith(prefix) or Path(p).name in keep
        ]
