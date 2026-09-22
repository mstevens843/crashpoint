"""Adoptable assertion over a fresh public-path capture; expected RED on v1.8.1.

Run the documented real-process capture first, then this assertion. The evidence verifier
must accept the capture before contract assertions run. This test asks for an unresolved
crash outcome to require reconciliation; the maintainer must choose the recovery policy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from crashpoint.harness.reality_layer_verify import verify_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    bundle = parser.parse_args().bundle
    verified = verify_bundle(bundle)
    failures = []
    for name, result in verified["summary"]["findings"].items():
        if name.startswith(("pre_crash-", "post_crash-")) and (
            result["status"] in {"STARTED", "UNKNOWN"}
            and result["reconciliation_required"] is not True
        ):
            failures.append(
                {
                    "trial": name,
                    "status": result["status"],
                    "reconciliation_required": result["reconciliation_required"],
                }
            )
    print(json.dumps({"evidence_valid": True, "contract_failures": failures}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
