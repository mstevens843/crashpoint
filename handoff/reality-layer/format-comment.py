"""After the user's push, print the complete reply with actual commit-pinned artifact URLs."""

from __future__ import annotations

import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
repo = "https://github.com/mstevens843/crashpoint"
branch = "experiment/reality-layer-crash-readback"


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


if git("branch", "--show-current") != branch:
    raise SystemExit("Run from the prepared experiment branch.")
if git("remote", "get-url", "--push", "origin") != repo + ".git":
    raise SystemExit("origin no longer matches the verified user-owned repository.")
sha = git("rev-parse", "HEAD")
remote = git("ls-remote", "origin", f"refs/heads/{branch}").split()
if not remote or remote[0] != sha:
    raise SystemExit("Push this commit first; no publication URLs have been asserted.")
paths = {
    "REPORT_URL": ("blob", "results/14-reality-layer-crash-readback.md"),
    "EVIDENCE_URL": ("tree", "evidence/reality_layer/reality-layer-retention-fixed-20260922"),
    "REPRODUCE_URL": ("blob", "handoff/reality-layer/REPRODUCE.md"),
}
text = (root / "handoff/reality-layer/github-comment-draft.md").read_text()
for key, (kind, path) in paths.items():
    git("cat-file", "-e", f"{sha}:{path}")
    text = text.replace("{{" + key + "}}", f"{repo}/{kind}/{sha}/{path}")
print(text, end="")
