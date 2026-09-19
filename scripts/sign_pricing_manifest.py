#!/usr/bin/env python3
"""Sign the canonical pricing manifest with an Ed25519 repository secret."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical_payload(manifest: dict) -> bytes:
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    return json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    key_text = os.environ.get("PRICING_MANIFEST_SIGNING_KEY")
    if not key_text:
        raise RuntimeError("PRICING_MANIFEST_SIGNING_KEY is required to publish pricing data")
    try:
        key_bytes = base64.b64decode(key_text, validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    except ValueError as error:
        raise RuntimeError("PRICING_MANIFEST_SIGNING_KEY must be a base64 Ed25519 private key") from error

    path = Path(args.root).resolve() / "pricing_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("pricing_manifest.json must be an object")
    signature = private_key.sign(canonical_payload(manifest))
    manifest["signature"] = {
        "algorithm": "ed25519",
        "keyId": "pricing-manifest-v1",
        "value": base64.b64encode(signature).decode("ascii"),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print("Signed pricing_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
