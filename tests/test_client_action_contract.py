from datetime import datetime, timedelta, timezone

import pytest

from modules.contracts import ClientAction


def test_client_action_round_trip_has_runtime_identity():
    first = ClientAction(
        "play_dance",
        {"dance_id": None},
        request_id="req_1",
        conversation_id="conversation_1",
    )
    second = ClientAction("play_dance", {"dance_id": None})
    restored = ClientAction.from_dict(first.to_dict())

    assert first.action_id != second.action_id
    assert first.idempotency_key != second.idempotency_key
    assert restored.to_dict() == first.to_dict()
    assert restored.status == "requested"


def test_client_action_rejects_process_local_arguments():
    with pytest.raises(TypeError):
        ClientAction("play_dance", {"callback": object()})


def test_client_action_can_carry_expiry():
    expires = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    action = ClientAction("nod", expires_at=expires)
    assert action.expires_at == expires
