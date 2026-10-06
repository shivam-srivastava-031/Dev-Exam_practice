"""Build step for the Vercel backend service: download the pre-built artefacts.

Fetches the question bank and pre-trained models listed in deploy/release.json
from the project's GitHub Release, verifies each SHA-256, and unpacks them into
deploy_data/, which ships inside the function bundle. Nothing is trained during
deployment. Standard library only, so it runs before any dependency matters.

    python deploy/fetch_artifacts.py            # run from backend/
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
TARGET = BACKEND / "deploy_data"
MANIFEST = BACKEND / "deploy" / "release.json"

# asset name -> how to unpack it under deploy_data/
LAYOUT = {
    "exam-bank.db.gz": ("gunzip", "exam.db"),
    "rag-index.tar.gz": ("untar", "index"),
    "topic-model.npz": ("copy", "models/topic_model.npz"),
    "embedding-model.tar.gz": ("untar", "models/potion-retrieval-32M"),
}


def download(url: str, dest: Path, expected_sha256: str) -> None:
    for attempt in range(1, 4):
        try:
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=120) as resp, dest.open("wb") as fh:
                for block in iter(lambda: resp.read(1 << 20), b""):
                    digest.update(block)
                    fh.write(block)
            if digest.hexdigest() != expected_sha256:
                raise ValueError(f"checksum mismatch for {url}")
            return
        except (OSError, ValueError) as e:
            if attempt == 3:
                raise
            print(f"  retrying {dest.name} after: {e}", flush=True)
            time.sleep(3 * attempt)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    marker = TARGET / ".release"
    if marker.is_file() and marker.read_text() == manifest["tag"]:
        print(f"deploy_data already holds {manifest['tag']}")
        return
    # ARTIFACT_BASE_URL lets a rehearsal serve the same files locally.
    base = (os.environ.get("ARTIFACT_BASE_URL")
            or f"https://github.com/{manifest['repo']}/releases/download/{manifest['tag']}")
    TARGET.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, (action, where) in LAYOUT.items():
            info = manifest["assets"][name]
            started = time.perf_counter()
            archive = Path(tmp) / name
            download(f"{base}/{name}", archive, info["sha256"])
            dest = TARGET / where
            dest.parent.mkdir(parents=True, exist_ok=True)
            if action == "gunzip":
                with gzip.open(archive, "rb") as fin, dest.open("wb") as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
            elif action == "untar":
                shutil.rmtree(dest, ignore_errors=True)
                dest.mkdir(parents=True)
                with tarfile.open(archive) as tar:
                    tar.extractall(dest, filter="data")
            else:
                shutil.copyfile(archive, dest)
            archive.unlink()
            print(f"  {name:24s} {info['bytes'] / 1e6:7.1f} MB  {time.perf_counter() - started:5.1f}s", flush=True)
    marker.write_text(manifest["tag"])
    print(f"deploy_data ready ({manifest['tag']})")


if __name__ == "__main__":
    sys.exit(main())
