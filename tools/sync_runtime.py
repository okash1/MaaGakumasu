"""Sync this source checkout into an installed MaaGakumasu runtime directory.

Usage:
    python tools/sync_runtime.py <runtime_dir>

Copies agent/, assets/resource, assets/tasks, assets/lang, assets/data and
assets/interface.json into an existing release install (one that already has
MaaGakumasu.exe, python/ and runtimes/), reusing the same functions the CI uses
in tools/install.py. The runtime's existing "version" string and the
platform-specific agent launch settings are preserved/re-applied.

Note: files deleted in the repo are NOT removed from the runtime (copytree
merge). Delete the runtime's resource/ tasks/ agent/ manually if you need a
clean sync.
"""

import json
import sys
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_dir / "tools"))

import install  # noqa: E402  (tools/install.py, upstream CI installer)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1]).resolve()
    if not (target / "interface.json").exists() or not (target / "runtimes").exists():
        print(f"error: {target} does not look like an installed MaaGakumasu runtime")
        return 1

    with open(target / "interface.json", encoding="utf-8") as f:
        current_version = json.load(f).get("version", "unknown")

    install.working_dir = repo_dir
    install.install_path = target
    install.version = current_version

    install.install_resource()
    install.install_agent()
    print(f"Synced {repo_dir} -> {target} (interface version kept at {current_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
