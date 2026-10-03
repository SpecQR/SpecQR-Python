"""Small stdlib-only PEP 517 backend for this pure-Python distribution.

No build or runtime dependency is required. Editable builds intentionally are
not provided; install the wheel or use PYTHONPATH=src during development.
"""
from __future__ import annotations
import base64
import csv
import hashlib
import io
from pathlib import Path
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
NAME = PROJECT["name"].replace("-", "_")
VERSION = PROJECT["version"]
DIST_INFO = f"{NAME}-{VERSION}.dist-info"


def _metadata() -> bytes:
    head = ["Metadata-Version: 2.4", f"Name: {PROJECT['name']}", f"Version: {VERSION}",
            f"Summary: {PROJECT['description']}", f"Requires-Python: {PROJECT['requires-python']}",
            "License-Expression: MIT", "License-File: LICENSE", "Author: SpecQR contributors",
            "Description-Content-Type: text/markdown"]
    head += [f"Project-URL: {name}, {url}" for name, url in PROJECT["urls"].items()]
    head += [f"Classifier: {item}" for item in PROJECT["classifiers"]]
    return ("\n".join(head) + "\n\n" + (ROOT / "README.md").read_text("utf-8")).encode("utf-8")


def _info_files() -> dict[str, bytes]:
    return {"METADATA": _metadata(),
            "WHEEL": b"Wheel-Version: 1.0\nGenerator: SpecQR-stdlib-backend\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            "entry_points.txt": b"[console_scripts]\nspecqr = specqr.__main__:main\n",
            "licenses/LICENSE": (ROOT / "LICENSE").read_bytes()}


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    destination = Path(metadata_directory) / DIST_INFO
    for name, data in _info_files().items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return DIST_INFO


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    files = {path.relative_to(ROOT / "src").as_posix(): path.read_bytes()
             for path in sorted((ROOT / "src" / "specqr").rglob("*"))
             if path.is_file() and path.suffix in (".py", ".typed") and "__pycache__" not in path.parts}
    files.update({f"{DIST_INFO}/{name}": data for name, data in _info_files().items()})
    records = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        records.append((name, "sha256=" + digest, str(len(data))))
    records.append((f"{DIST_INFO}/RECORD", "", ""))
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\n").writerows(records)
    files[f"{DIST_INFO}/RECORD"] = buf.getvalue().encode("utf-8")
    filename = f"{NAME}-{VERSION}-py3-none-any.whl"
    target = Path(wheel_directory)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target / filename, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return filename


def build_sdist(sdist_directory, config_settings=None):
    filename = f"{NAME}-{VERSION}.tar.gz"
    target = Path(sdist_directory)
    target.mkdir(parents=True, exist_ok=True)
    allowed = {"src", "tests", "tools", "docs", "examples", ".github"}
    root_files = {"pyproject.toml", "build_backend.py", "README.md", "LICENSE", "NOTICE", "CONTRIBUTING.md", "SECURITY.md", ".gitignore"}
    with tarfile.open(target / filename, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if not path.is_file() or not (rel.parts[0] in allowed or str(rel) in root_files):
                continue
            if any(p in {"__pycache__", "node_modules", ".venv", ".cache", "reports"} for p in rel.parts) or path.suffix == ".pyc":
                continue
            info = archive.gettarinfo(str(path), arcname=f"{NAME}-{VERSION}/{rel.as_posix()}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 1767225600
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    return filename
