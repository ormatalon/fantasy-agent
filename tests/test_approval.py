"""The Stage 5 DoD is that the approve gate cannot be bypassed, so these
tests are adversarial: each one is an attempt to get a write authorized
without a human typing the phrase for that exact payload.
"""

import pytest

from src.execution.approval import (
    CONFIRM_PHRASE,
    Approval,
    ApprovalDenied,
    ApprovalInvalid,
    ProposedWrite,
    require_approval,
    verify,
)


def a_write(payload=None) -> ProposedWrite:
    return ProposedWrite(
        action="set_lineup",
        summary="Set week 2 lineup",
        changes=["QB: Stafford -> Rodgers"],
        payload=payload if payload is not None else {"QB": "rodgers"},
    )


def silent(*_args, **_kwargs):
    pass


def test_typing_the_phrase_grants_approval():
    approval = require_approval(a_write(), prompt=lambda _: CONFIRM_PHRASE, out=silent)

    verify(approval, a_write())  # does not raise


def test_declining_denies_and_writes_nothing():
    with pytest.raises(ApprovalDenied):
        require_approval(a_write(), prompt=lambda _: "no", out=silent)


def test_a_bare_yes_is_not_enough():
    # Reflexive assent is the failure mode worth designing against.
    for reflexive in ["y", "yes", "Y", "ok", "sure", ""]:
        with pytest.raises(ApprovalDenied):
            require_approval(a_write(), prompt=lambda _, r=reflexive: r, out=silent)


def test_confirmation_is_case_sensitive():
    with pytest.raises(ApprovalDenied):
        require_approval(a_write(), prompt=lambda _: "approve", out=silent)


def test_approval_cannot_be_fabricated():
    # Without the module-private mint, constructing one is refused outright.
    with pytest.raises(ApprovalInvalid, match="cannot be constructed"):
        Approval(action="set_lineup", fingerprint="deadbeef", granted_at="now")


def test_a_forged_object_does_not_pass_verification():
    class LooksLikeApproval:
        action = "set_lineup"
        fingerprint = "whatever"
        granted_at = "now"

    with pytest.raises(ApprovalInvalid, match="Expected an Approval"):
        verify(LooksLikeApproval(), a_write())


def test_approving_one_lineup_does_not_authorize_a_different_one():
    # The core guarantee: the token is bound to the payload the human saw.
    approval = require_approval(a_write({"QB": "rodgers"}), prompt=lambda _: CONFIRM_PHRASE, out=silent)

    with pytest.raises(ApprovalInvalid, match="changed after it was approved"):
        verify(approval, a_write({"QB": "someone_else"}))


def test_approval_for_one_action_does_not_authorize_another():
    approval = require_approval(a_write(), prompt=lambda _: CONFIRM_PHRASE, out=silent)
    other = ProposedWrite(action="drop_player", summary="Drop someone", payload={"QB": "rodgers"})

    with pytest.raises(ApprovalInvalid, match="was for 'set_lineup'"):
        verify(approval, other)


def test_the_human_is_shown_what_will_change_before_confirming():
    shown = []
    require_approval(a_write(), prompt=lambda _: CONFIRM_PHRASE, out=lambda s="": shown.append(str(s)))

    text = "\n".join(shown)
    assert "APPROVAL REQUIRED" in text
    assert "QB: Stafford -> Rodgers" in text


def test_fingerprint_is_order_independent_but_value_sensitive():
    # Key order shouldn't invalidate an approval; a changed value must.
    same = ProposedWrite(action="x", summary="", payload={"b": 2, "a": 1})
    reordered = ProposedWrite(action="x", summary="", payload={"a": 1, "b": 2})
    changed = ProposedWrite(action="x", summary="", payload={"a": 1, "b": 3})

    assert same.fingerprint() == reordered.fingerprint()
    assert same.fingerprint() != changed.fingerprint()


def test_no_function_in_the_gate_accepts_a_bypass_parameter():
    # Guard against a future "just add --force" change. Inspecting signatures
    # rather than source text, so prose explaining the policy doesn't trip it.
    import inspect

    from src.execution import approval as module

    bypass_names = {"force", "skip", "skip_approval", "auto_approve", "yes", "assume_yes", "noninteractive"}
    for name, obj in vars(module).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        params = set(inspect.signature(obj).parameters)
        offending = params & bypass_names
        assert not offending, f"{name}() gained a bypass parameter: {offending}"
