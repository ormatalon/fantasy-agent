"""The human-approve gate. Nothing writes to Sleeper without passing through here.

PLAN.md's Stage 5 DoD is not "the automation works" — it is "the approve gate
cannot be bypassed". So the gate is structural, not a convention:

- `Approval` cannot be constructed outside this module (its __init__ demands a
  module-private sentinel), so a caller cannot fabricate one.
- An approval is bound to a **hash of the exact payload** shown to the human.
  Approving one lineup does not authorize writing a different one - a
  swapped payload fails verification even with a genuine token.
- There is deliberately **no --force / --yes flag anywhere**. Per PLAN.md the
  gate stays mandatory "for a long while"; adding a bypass is the one change
  this module exists to prevent.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

CONFIRM_PHRASE = "APPROVE"

# Construction guard: only this module holds it, so `Approval(...)` elsewhere
# raises rather than minting authorization.
_MINT = object()


class ApprovalDenied(RuntimeError):
    pass


class ApprovalInvalid(RuntimeError):
    pass


def payload_fingerprint(payload) -> str:
    """Stable hash of what the human was actually shown."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Approval:
    action: str
    fingerprint: str
    granted_at: str

    def __init__(self, action: str, fingerprint: str, granted_at: str, _mint=None):
        if _mint is not _MINT:
            raise ApprovalInvalid(
                "Approval cannot be constructed directly - it is only issued by "
                "require_approval() after a human confirms."
            )
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "fingerprint", fingerprint)
        object.__setattr__(self, "granted_at", granted_at)


@dataclass
class ProposedWrite:
    action: str
    summary: str
    changes: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        return payload_fingerprint({"action": self.action, "payload": self.payload})


def require_approval(write: ProposedWrite, prompt=input, out=print) -> Approval:
    """Show exactly what will change, then demand a typed confirmation.

    Raises ApprovalDenied unless the human types CONFIRM_PHRASE exactly. A
    bare "y" is not accepted - this guards a write to a real account, and
    reflexive assent is the failure mode worth designing against.
    """
    out("")
    out("=" * 60)
    out(f"APPROVAL REQUIRED: {write.action}")
    out("=" * 60)
    out(write.summary)
    if write.changes:
        out("")
        out("This will change:")
        for line in write.changes:
            out(f"  {line}")
    out("")
    out(f"Type {CONFIRM_PHRASE} to authorize, anything else to cancel.")

    answer = (prompt("> ") or "").strip()
    if answer != CONFIRM_PHRASE:
        raise ApprovalDenied("Not approved - nothing was written.")

    return Approval(
        action=write.action,
        fingerprint=write.fingerprint(),
        granted_at=datetime.now(timezone.utc).isoformat(),
        _mint=_MINT,
    )


def verify(approval: Approval, write: ProposedWrite) -> None:
    """Called immediately before a write actually happens. Raises unless this
    approval was issued for exactly this action and payload."""
    if not isinstance(approval, Approval):
        raise ApprovalInvalid(f"Expected an Approval, got {type(approval).__name__}.")
    if approval.action != write.action:
        raise ApprovalInvalid(
            f"Approval was for '{approval.action}', not '{write.action}'."
        )
    if approval.fingerprint != write.fingerprint():
        raise ApprovalInvalid(
            "The move changed after it was approved - refusing to write. "
            "Re-run and approve the current proposal."
        )
