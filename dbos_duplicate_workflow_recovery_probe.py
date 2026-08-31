from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


BILLING = r"""
from __future__ import annotations

import os
import signal

from dbos import DBOS


@DBOS.workflow()
def process(marker: str) -> str:
    with open(marker, "a", encoding="utf-8") as f:
        phase = "crash" if os.environ.get("DBOS_DUPNAME_CRASH") == "1" else "recovery"
        f.write(f"billing:{phase}\n")
    if os.environ.get("DBOS_DUPNAME_CRASH") == "1":
        os.kill(os.getpid(), signal.SIGKILL)
    return "billing"
"""


SHIPPING = r"""
from __future__ import annotations

from dbos import DBOS


@DBOS.workflow()
def process(marker: str) -> str:
    with open(marker, "a", encoding="utf-8") as f:
        f.write("shipping:recovery\n")
    return "shipping"
"""


CRASH_RUNNER = r"""
from __future__ import annotations

import os
import sys
import time

from dbos import DBOS, SetWorkflowID

import billing


db_url, marker, workflow_id = sys.argv[1:4]

DBOS(
    config={
        "name": "dupname-recovery-probe",
        "system_database_url": db_url,
        "application_version": "dupname-probe-version",
        "executor_id": "dupname-probe-executor",
        "log_level": "ERROR",
        "run_admin_server": False,
        "max_executor_threads": 1,
    }
)
DBOS.launch()

os.environ["DBOS_DUPNAME_CRASH"] = "1"
with SetWorkflowID(workflow_id):
    DBOS.start_workflow(billing.process, marker)

time.sleep(30)
"""


RECOVERY_RUNNER = r"""
from __future__ import annotations

import sys

from dbos import DBOS
from dbos import _dbos as D

db_url, marker, workflow_id, import_order = sys.argv[1:5]

if import_order == "billing_then_shipping":
    import billing
    import shipping
elif import_order == "shipping_then_billing":
    import shipping
    import billing
else:
    raise ValueError(import_order)

winner = D._dbos_global_registry.workflow_info_map["process"].__module__
print(f"registry winner before launch: {winner}")

DBOS(
    config={
        "name": "dupname-recovery-probe",
        "system_database_url": db_url,
        "application_version": "dupname-probe-version",
        "executor_id": "dupname-probe-executor",
        "log_level": "ERROR",
        "run_admin_server": False,
        "max_executor_threads": 1,
    }
)
DBOS.launch()

result = DBOS.retrieve_workflow(workflow_id).get_result()
print(f"recovered result: {result}")
with open(marker, encoding="utf-8") as f:
    print("marker:")
    print(f.read().strip())
"""


INSPECT_STATUS = r"""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

db_url, workflow_id = sys.argv[1:3]
table = "dbos.workflow_status" if db_url.startswith("postgres") else "workflow_status"
sqlalchemy_url = (
    db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if db_url.startswith("postgresql://")
    else db_url
)
engine = create_engine(sqlalchemy_url)
with engine.begin() as conn:
    row = conn.execute(
        text(
            f"select workflow_uuid, status, name, executor_id, application_version "
            f"from {table} where workflow_uuid = :workflow_id"
        ),
        {"workflow_id": workflow_id},
    ).mappings().one()
    print(dict(row))
"""


def run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, env=env)


def write(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def run_case(root: Path, db_url: str, run_id: str, import_order: str) -> bool:
    marker = root / f"{import_order}.txt"
    workflow_id = f"dupname-{run_id}-{import_order}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")

    crash = run(
        [sys.executable, str(root / "crash_runner.py"), db_url, str(marker), workflow_id],
        env=env,
    )
    print(f"\nCASE {import_order}")
    print(f"crash return code: {crash.returncode}")
    if crash.stdout.strip():
        print("crash stdout:")
        print(crash.stdout.strip())
    if crash.stderr.strip():
        print("crash stderr:")
        print(crash.stderr.strip())

    before = run(
        [sys.executable, str(root / "inspect_status.py"), db_url, workflow_id],
        env=env,
    )
    print("status before recovery:")
    print((before.stdout or before.stderr).strip())

    recovery = run(
        [
            sys.executable,
            str(root / "recovery_runner.py"),
            db_url,
            str(marker),
            workflow_id,
            import_order,
        ],
        env=env,
    )
    print(f"recovery return code: {recovery.returncode}")
    if recovery.stdout.strip():
        print("recovery stdout:")
        print(recovery.stdout.strip())
    if recovery.stderr.strip():
        print("recovery stderr:")
        print(recovery.stderr.strip())

    after = run(
        [sys.executable, str(root / "inspect_status.py"), db_url, workflow_id],
        env=env,
    )
    print("status after recovery:")
    print((after.stdout or after.stderr).strip())

    marker_text = marker.read_text(encoding="utf-8") if marker.exists() else ""
    expected = "shipping:recovery" if import_order == "billing_then_shipping" else "billing:recovery"
    return crash.returncode == -9 and expected in marker_text and recovery.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-url",
        default=os.environ.get(
            "CRASHPOINT_DBOS_URL", "postgresql://cpuser:dbos@localhost:5433/cpdbos"
        ),
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="dbos-dupname-probe-") as tmp:
        root = Path(tmp)
        write(root / "billing.py", BILLING)
        write(root / "shipping.py", SHIPPING)
        write(root / "crash_runner.py", CRASH_RUNNER)
        write(root / "recovery_runner.py", RECOVERY_RUNNER)
        write(root / "inspect_status.py", INSPECT_STATUS)

        print(f"db_url: {args.db_url}")
        run_id = root.name.replace("dbos-dupname-probe-", "")
        case_a = run_case(root, args.db_url, run_id, "billing_then_shipping")
        case_b = run_case(root, args.db_url, run_id, "shipping_then_billing")

    if case_a and case_b:
        print(
            "\nOBSERVED: one stored workflow name ('process') resumed into whichever "
            "module won the recovery process registry import order."
        )
        return 0

    print("\nNOT OBSERVED: at least one case did not match the expected recovery dispatch.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
