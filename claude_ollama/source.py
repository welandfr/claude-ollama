"""Identity of the code a proxy is running.

A long-lived proxy — a systemd unit especially — keeps serving whatever it was
started with. Config drift is caught by comparing .env values, but a code change
leaves no trace in those, so the running process is fingerprinted instead and
compared with the checkout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def fingerprint() -> str:
    """Short digest of this package's sources. Changes whenever any module does."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
