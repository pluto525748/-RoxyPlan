"""Offline accounting checks; never open the live ledger or call a provider."""
import json

import pytest

from scripts.online_acceptance_budget import SharedBudget


def budget(tmp_path, *, cap=3_000_000, known=56_773, held=50_000):
    path = tmp_path / "ledger.json"
    state = {
        "schema_version": 1,
        "caps": {"http": 1500, "reported_token_stop": cap},
        "seed": {"http": 22, "reported_tokens": known},
        "http_reserved": 23, "reported_tokens": known,
        "unknown_usage": 1, "incomplete_requests": 1,
        "in_flight": None, "halt_reason": None, "active_stage": None,
        "stages": [],
        "requests": [{"id": "http-00023", "status": "http_error_unknown_charge",
                      "reported_tokens": None, "admission_headroom": 20_000,
                      "dispatch_started": True}],
        "unknown_token_reservations": {"http-00023": {
            "tokens": held, "authorization": "synthetic-owner-choice"}},
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    return SharedBudget(path, {"http": 60, "reported_tokens_stop": 200_000},
                        used_http=23, used_tokens=known, remaining_http=1477,
                        remaining_tokens=cap-known, stage="new-independent-stage",
                        source_files={}, source_check=lambda: True)


def test_hold_reduces_spendable_budget_without_changing_reported_usage(tmp_path):
    b = budget(tmp_path)
    try:
        r = b.report()
        assert r["shared_http_reserved"] == 23
        assert r["shared_reported_total_tokens"] == 56_773
        assert r["reserved_unknown_tokens"] == 50_000
        assert r["remaining_budget_tokens_after_reserve"] == 2_893_227
        assert b.state["unknown_usage"] == b.state["incomplete_requests"] == 1
        assert b.state["requests"][0]["reported_tokens"] is None
    finally:
        b.close()


def test_reserved_allowance_cannot_be_spent_by_next_request(tmp_path):
    b = budget(tmp_path, cap=100_000, known=60_000, held=30_000)
    try:
        assert b.report()["remaining_reported_tokens"] == 40_000
        assert not b.reserve()
        assert b.http == 0 and b.state["http_reserved"] == 23
        assert b.stop_reason == "shared_token_headroom"
    finally:
        b.close()


def test_new_success_keeps_old_unknown_and_its_hold(tmp_path):
    b = budget(tmp_path)
    try:
        assert b.reserve()
        assert b.dispatched(b.request_id)
        assert b.observe({"usage": {"total_tokens": 2500}}, request_id=b.request_id)
        assert b.state["reported_tokens"] == 59_273
        assert b.state["unknown_usage"] == 1
        assert b.report()["reserved_unknown_tokens"] == 50_000
    finally:
        b.close()


def test_new_unknown_still_latches_stop_instead_of_reusing_authorization(tmp_path):
    b = budget(tmp_path)
    try:
        assert b.reserve()
        assert b.dispatched(b.request_id)
        assert not b.observe({}, event="http_error", request_id=b.request_id)
        assert b.state["halt_reason"] == "http_error_unknown_charge"
        assert b.state["unknown_usage"] == 2
        assert not b.reserve()
        assert b.report()["reserved_unknown_tokens"] == 50_000
    finally:
        b.close()


@pytest.mark.parametrize("held", [True, 19_999, "50000"])
def test_invalid_or_underfunded_hold_fails_closed(tmp_path, held):
    with pytest.raises(ValueError, match="hold lacks"):
        budget(tmp_path, held=held)
