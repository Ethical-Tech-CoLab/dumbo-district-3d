#!/usr/bin/env python3
"""Sync the Manhattan Bridge module payload from the bridge repository.

This module consumes another team's geometry and must never author it. That rule is easy to state
and easy to break by hand, so the copy is a script: it takes the bridge repository's published
output verbatim, refuses anything it does not recognise, and reports exactly what changed.

Why this exists at all. The bridge team shipped new geometry -- remodelled tower arches and finials
-- under the *same* `module_version`, so nothing in the contract could tell a consumer its copy had
been superseded. The district went on rendering an export four hours older than the one the bridge
team was looking at, and the only symptom was that the bridge looked subtly wrong. This script makes
the drift visible and one command to fix; `--check` makes it a build gate.

See DUMBO-SCOPE.md for the anti-duplication rule this enforces.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST = REPO_ROOT / "viewer" / "public" / "modules" / "manhattan-bridge"
DEFAULT_SOURCE = REPO_ROOT.parent / "manhattan-bridge-3d" / "viewer" / "public"

# Exactly the files this module is entitled to consume. Everything else in the bridge repository --
# its CAD, its photogrammetry, its own viewer's working files -- stays there.
PAYLOAD = [
    "bridge-manifest.json",
    "assets/bridge.lod0.glb",
    "assets/bridge.lod2.glb",
    "bridge/asset-registry.json",
    "bridge/lod.json",
    "bridge/metadata.json",
    "frames/nyc-harbor-enu.json",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="bridge repo viewer/public")
    parser.add_argument("--check", action="store_true", help="report drift and exit non-zero; copy nothing")
    args = parser.parse_args()

    source: Path = args.source
    if not (source / "bridge-manifest.json").exists():
        print(f"FAIL: no bridge-manifest.json under {source}")
        print("      pass --source <path to manhattan-bridge-3d/viewer/public>")
        return 1

    theirs = json.loads((source / "bridge-manifest.json").read_text("utf-8"))
    ours_path = DEST / "bridge-manifest.json"
    ours = json.loads(ours_path.read_text("utf-8")) if ours_path.exists() else {}

    print(f"source : {source}")
    print(f"version: theirs {theirs.get('module_version')} · ours {ours.get('module_version') or '(absent)'}")
    print(
        f"built  : theirs {(theirs.get('provenance') or {}).get('generated_at')} · "
        f"ours {(ours.get('provenance') or {}).get('generated_at') or '(absent)'}"
    )

    if theirs.get("placement") != ours.get("placement") and ours:
        print("NOTE: the published placement changed. The bridge will move in the district scene.")

    changed: list[str] = []
    missing: list[str] = []
    for relative in PAYLOAD:
        src = source / relative
        dst = DEST / relative
        if not src.exists():
            missing.append(relative)
            continue
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            changed.append(relative)

    for relative in missing:
        print(f"  MISSING upstream: {relative}")
    for relative in changed:
        src, dst = source / relative, DEST / relative
        before = digest(dst)[:12] if dst.exists() else "absent"
        print(f"  stale: {relative}  {before} -> {digest(src)[:12]}  ({src.stat().st_size:,} bytes)")

    if not changed and not missing:
        print("OK: the district's copy matches the bridge team's published output")
        return 0

    if args.check:
        print(f"\nFAIL: {len(changed)} file(s) stale. Run scripts/sync_bridge_module.py to update.")
        return 1

    for relative in changed:
        dst = DEST / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, dst)
    print(f"\nsynced {len(changed)} file(s) from the bridge module")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
