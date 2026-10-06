"""Download the pre-built question bank and pre-trained models from the GitHub Release.

Vercel caps this function's bundle at 225 MB, well below the ~325 MB of data, so the
data is not bundled: on a cold start the server streams the four release assets
listed in deploy/release.json straight into the writable temp directory, unpacking
as it downloads (archives never touch the disk) and verifying each SHA-256. All four
download in parallel; the whole set takes a few seconds on Vercel's network.

The same code backs deploy/fetch_artifacts.py for local rehearsals.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tarfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "deploy" / "release.json"

# asset -> (how to unpack, where under the data directory)
LAYOUT = {
    "exam-bank.db.gz": ("gunzip", "exam.db"),
    "rag-index.tar.gz": ("untar", "index"),
    "topic-model.npz": ("copy", "models/topic_model.npz"),
    "embedding-model.tar.gz": ("untar", "models/potion-retrieval-32M"),
}
MARKER = ".release"


class ChecksumError(RuntimeError):
    pass


class _Hashing:
    """File-like wrapper that hashes every byte read through it."""

    def __init__(self, raw):
        self.raw = raw
        self.digest = hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        data = self.raw.read(n)
        self.digest.update(data)
        return data

    def drain(self) -> None:  # tar/gzip may stop before the final padding bytes
        for block in iter(lambda: self.read(1 << 20), b""):
            pass


def manifest(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text())


def ready(target: Path, path: Path = MANIFEST) -> bool:
    marker = target / MARKER
    return marker.is_file() and marker.read_text() == manifest(path)["tag"]


def _base_url(m: dict) -> str:
    return (os.environ.get("ARTIFACT_BASE_URL")
            or f"https://github.com/{m['repo']}/releases/download/{m['tag']}")


def _fetch_one(url: str, name: str, expected: str, target: Path) -> float:
    action, where = LAYOUT[name]
    dest = target / where
    started = time.perf_counter()
    for attempt in range(1, 4):
        try:
            if action == "untar":
                shutil.rmtree(dest, ignore_errors=True)
                dest.mkdir(parents=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=60) as resp:
                src = _Hashing(resp)
                if action == "gunzip":
                    with gzip.GzipFile(fileobj=src) as fin, dest.open("wb") as fout:
                        shutil.copyfileobj(fin, fout, 1 << 20)
                elif action == "untar":
                    with tarfile.open(fileobj=src, mode="r|gz") as tar:
                        tar.extractall(dest, filter="data")
                else:
                    with dest.open("wb") as fout:
                        shutil.copyfileobj(src, fout, 1 << 20)
                src.drain()
            if src.digest.hexdigest() != expected:
                raise ChecksumError(f"{name}: SHA-256 does not match deploy/release.json")
            return time.perf_counter() - started
        except (OSError, ChecksumError, tarfile.TarError, EOFError) as e:
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            elif dest.exists():
                dest.unlink()
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def fetch(target: Path, path: Path = MANIFEST, log=print) -> None:
    """Download and unpack every asset into `target` unless it already holds this release."""
    m = manifest(path)
    if ready(target, path):
        return
    target.mkdir(parents=True, exist_ok=True)
    base = _base_url(m)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(LAYOUT)) as pool:
        jobs = {name: pool.submit(_fetch_one, f"{base}/{name}", name, m["assets"][name]["sha256"], target)
                for name in LAYOUT}
        for name, job in jobs.items():
            seconds = job.result()
            log(f"  {name:24s} {m['assets'][name]['bytes'] / 1e6:7.1f} MB  {seconds:5.1f}s")
    (target / MARKER).write_text(m["tag"])
    log(f"data ready ({m['tag']}) in {time.perf_counter() - started:.1f}s")
