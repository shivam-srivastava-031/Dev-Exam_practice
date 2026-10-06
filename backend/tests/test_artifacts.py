"""The cold-start downloader against a local HTTP server serving tiny release assets."""
import gzip
import hashlib
import io
import json
import tarfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from app import artifacts


def _tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture()
def release(tmp_path):
    """Four small assets plus a manifest, served over HTTP like the GitHub Release."""
    served = tmp_path / "served"
    served.mkdir()
    npz = io.BytesIO()
    np.savez(npz, idf=np.ones(3))
    assets = {
        "exam-bank.db.gz": gzip.compress(b"SQLite format 3\x00 pretend bank"),
        "rag-index.tar.gz": _tar_gz({"vectors.npy": b"v", "meta.json": b"{}"}),
        "topic-model.npz": npz.getvalue(),
        "embedding-model.tar.gz": _tar_gz({"config.json": b"{}", "model.safetensors": b"w"}),
    }
    for name, data in assets.items():
        (served / name).write_bytes(data)
    manifest = {"repo": "x/y", "tag": "data-test", "assets": {
        name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in assets.items()}}
    manifest_path = tmp_path / "release.json"
    manifest_path.write_text(json.dumps(manifest))

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(served)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{server.server_address[1]}", "manifest": manifest_path, "served": served}
    server.shutdown()


def test_fetch_streams_unpacks_and_marks(release, tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_BASE_URL", release["url"])
    target = tmp_path / "data"
    artifacts.fetch(target, release["manifest"], log=lambda *_: None)
    assert (target / "exam.db").read_bytes() == b"SQLite format 3\x00 pretend bank"
    assert (target / "index" / "vectors.npy").read_bytes() == b"v"
    assert (target / "models" / "potion-retrieval-32M" / "model.safetensors").read_bytes() == b"w"
    assert np.load(target / "models" / "topic_model.npz")["idf"].tolist() == [1, 1, 1]
    assert artifacts.ready(target, release["manifest"])
    assert not list(target.rglob("*.gz"))  # archives are unpacked while streaming, never stored

    (target / "exam.db").write_bytes(b"local changes")
    artifacts.fetch(target, release["manifest"], log=lambda *_: None)  # same release: nothing re-downloaded
    assert (target / "exam.db").read_bytes() == b"local changes"


def test_tampered_asset_is_rejected_and_cleaned_up(release, tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_BASE_URL", release["url"])
    monkeypatch.setattr(artifacts.time, "sleep", lambda s: None)
    (release["served"] / "topic-model.npz").write_bytes(b"not what the manifest says")
    target = tmp_path / "data"
    with pytest.raises(artifacts.ChecksumError):
        artifacts.fetch(target, release["manifest"], log=lambda *_: None)
    assert not (target / "models" / "topic_model.npz").exists()
    assert not artifacts.ready(target, release["manifest"])
