"""Small file primitives for the hash-free local workflow.

Stamps detect ordinary file changes; they are not content identities. Revisions
and successful publication, rather than content digests, identify our artifacts.
"""
import json
import os
from pathlib import Path
import tempfile


def stamp(path):
    path = Path(path).expanduser().resolve()
    info = path.stat()
    return {"path": str(path), "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def atomic_json(path, value):
    """Publish a complete JSON document without exposing a partial write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass  # Directory fsync is not available on every filesystem.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
