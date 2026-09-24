"""Print publication links only after verifying the actual pushed commit and its contents."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "https://github.com/mstevens843/crashpoint"
BRANCH = "experiment/reality-layer-recovery-1822"
DIRECTORY = "handoff/reality-layer-recovery"


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True, timeout=30).strip()


def main() -> None:
    if git("branch", "--show-current") != BRANCH:
        raise SystemExit("Expected the prepared recovery branch; links remain UNPUBLISHED.")
    for option in [(), ("--push",)]:
        if git("remote", "get-url", *option, "origin") != REPO + ".git":
            raise SystemExit("Unexpected origin; links remain UNPUBLISHED.")
    sha = git("rev-parse", "HEAD")
    remote = git("ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}").splitlines()
    if remote != [f"{sha}\trefs/heads/{BRANCH}"]:
        raise SystemExit("The actual remote branch does not point to local HEAD. Push first.")
    listing = git("show", f"{sha}:{DIRECTORY}/publication-files.txt").splitlines()
    if not listing:
        raise SystemExit("Committed publication list is empty.")
    for name in listing:
        committed = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", f"{sha}:{name}"], timeout=30
        )
        if (ROOT / name).read_bytes() != committed:
            raise SystemExit(f"Working bytes differ from the pushed commit: {name}")
    draft = git("show", f"{sha}:{DIRECTORY}/github-comment-draft.md")
    for label, kind, path in [
        ("Report", "blob", "results/15-reality-layer-recovery-followup.md"),
        ("Evidence", "tree", "evidence/reality_layer/recovery-1822-20260924"),
    ]:
        git("cat-file", "-e", f"{sha}:{path}")
        old = f"[{label} - UNPUBLISHED](../../{path})"
        if draft.count(old) != 1:
            raise SystemExit(
                "Unexpected committed draft link; nothing was published by this script."
            )
        draft = draft.replace(old, f"[{label}]({REPO}/{kind}/{sha}/{path})")
    print(draft)


if __name__ == "__main__":
    main()
