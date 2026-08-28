"""Idempotency-key derivation for the idempotent effect arm.

Ported from durable-agent-outbox `idempotency.ts`. A key must be a function of WHAT the action is,
never of HOW MANY TIMES it has been tried or WHO tried it - so a re-run after a crash derives the
SAME key and the ledger dedups it. The classic bug bakes a per-attempt field (`attempt`, `retry`,
`epoch`, `nonce`, `timestamp`, a delivery id) in the key, which makes every retry a fresh key and
the dedup a no-op. `assert_no_forbidden_identity_fields` throws at derivation time if any such field
appears at any depth, so the bug cannot be written by accident.

Impure only in that it hashes; it is not the pure model and is not covered by the purity contract.
"""

from __future__ import annotations

from typing import Any

from ..canonical import canonicalize, sha256_hex

_KEY_SCHEME = "cp1key"

# A key that varies per attempt / lease / delivery is not idempotent. Two sets, to catch variants
# like "retryCount" without false-positiving legit fields like "runtime": the first is matched as a
# SUBSTRING of the normalized field name, the second only as an EXACT normalized match.
_FORBIDDEN_SUBSTRING = frozenset(
    {
        "attempt", "retry", "epoch", "nonce", "deliveryid", "requestid", "messageid", "traceid",
        "spanid", "correlationid", "idempotencyattempt",
    }
)
_FORBIDDEN_EXACT = frozenset(
    {"lease", "owner", "worker", "timestamp", "time", "now", "sequence", "seq", "random", "uuid"}
)


class ForbiddenIdentityField(ValueError):
    """A per-attempt / per-delivery field leaked into an idempotency key."""


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def assert_no_forbidden_identity_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ForbiddenIdentityField(f"non-string key at {path}")
            nk = _norm(k)
            if nk in _FORBIDDEN_EXACT or any(tok in nk for tok in _FORBIDDEN_SUBSTRING):
                raise ForbiddenIdentityField(
                    f"forbidden identity field {k!r} at {path}: a key must not vary per attempt"
                )
            assert_no_forbidden_identity_fields(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            assert_no_forbidden_identity_fields(item, f"{path}[{i}]")


def derive_idempotency_key(
    namespace: str, subject: str, intent_version: int, payload: dict[str, Any]
) -> str:
    """A stable, content-derived key: cp1key_<64 hex>. Raises if the payload carries a
    per-attempt field."""
    assert_no_forbidden_identity_fields(payload)
    envelope = {"n": namespace, "s": subject, "v": intent_version, "p": payload}
    return f"{_KEY_SCHEME}_{sha256_hex(_KEY_SCHEME + ':' + canonicalize(envelope))}"
