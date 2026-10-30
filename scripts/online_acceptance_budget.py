"""Durable, single-owner accounting for sequential real-provider stages.

Only API-reported tokens are usage. Reservations are conservative admission
headroom, not a claim that the final response cannot exceed a token threshold.
This module never imports the product, reads credentials, or sends HTTP.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def add_arguments(parser):
    parser.add_argument("--budget-ledger", required=True)
    parser.add_argument("--used-http", required=True, type=int)
    parser.add_argument("--used-reported-tokens", required=True, type=int)
    parser.add_argument("--remaining-http", required=True, type=int)
    parser.add_argument("--remaining-reported-tokens", required=True, type=int)
    parser.add_argument("--request-token-reserve", type=int, default=20000)
    parser.add_argument("--parent-seconds", type=int, default=600)


class SharedBudget:
    """One locked ledger across stages, including already consumed allowance.

    A pre-network reservation remains charged if the process disappears: it
    may already have reached the provider. Stale locks are never auto-deleted.
    Unknown usage, HTTP errors and interrupted requests latch the ledger shut.
    """

    def __init__(self, path, limits, *, used_http, used_tokens, remaining_http,
                 remaining_tokens, stage, source_files, source_check,
                 token_reserve=20000):
        values = (used_http, used_tokens, remaining_http, remaining_tokens, token_reserve)
        if any(type(v) is not int or v < 0 for v in values) or token_reserve < 1:
            raise ValueError("explicit nonnegative accounting and positive headroom required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_fd = None
        self.limits = dict(limits)
        self.http = self.tokens = self.unknown_usage = self.completed_responses = 0
        self.token_reserve = token_reserve
        self.source_check = source_check
        self.stop_reason = None
        self.request_id = None
        self.state = None
        try:
            self.lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(self.lock_fd, str(os.getpid()).encode("ascii"))
            os.fsync(self.lock_fd)
            caps = {"http": used_http + remaining_http,
                    "reported_token_stop": used_tokens + remaining_tokens}
            if self.path.exists():
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
                if self.state["schema_version"] != 1 or self.state["caps"] != caps:
                    raise ValueError("ledger identity/caps differ; never reset an existing budget")
                if self.state["halt_reason"] or self.state["in_flight"]:
                    raise ValueError("ledger halted or request unresolved; human reconciliation required")
                if (self.state["http_reserved"], self.state["reported_tokens"]) != (used_http, used_tokens):
                    raise ValueError("stale cumulative/remaining allowance; read the shared ledger")
                if self.state.get("active_stage") is not None:
                    raise ValueError("previous stage did not close; human reconciliation required")
            else:
                self.state = {"schema_version": 1, "caps": caps,
                              "seed": {"http": used_http, "reported_tokens": used_tokens},
                              "http_reserved": used_http, "reported_tokens": used_tokens,
                              "unknown_usage": 0, "incomplete_requests": 0,
                              "in_flight": None, "halt_reason": None,
                              "active_stage": None, "stages": [], "requests": []}
            if any(s["id"] == str(stage) for s in self.state["stages"]):
                raise ValueError("stage identity already used; keep first results")
            self._unknown_token_reserve()
            if not self.source_check():
                raise ValueError("source differs from reviewed freeze before stage start")
            self.stage = {"id": str(stage), "source_files": source_files,
                          "start_http": used_http, "start_reported_tokens": used_tokens,
                          "limits": self.limits, "token_reserve": token_reserve,
                          "status": "running"}
            self.state["stages"].append(self.stage)
            self.state["active_stage"] = str(stage)
            self._save()
        except BaseException:
            self._unlock()
            raise

    def _save(self):
        atomic_json(self.path, self.state)

    def _unlock(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
            self.lock_path.unlink()

    def can_start_turn(self):
        if self.stop_reason or self.state["halt_reason"]:
            return False
        if not self.source_check():
            self.halt("source_changed_during_stage")
            return False
        return self._allow(self.token_reserve)

    def _unknown_token_reserve(self):
        """Owner-authorized holds are not API-reported usage or refunds."""
        holds = self.state.get("unknown_token_reservations", {})
        if not isinstance(holds, dict):
            raise ValueError("invalid unknown-request reservations")
        requests = {item["id"]: item for item in self.state["requests"]}
        total = 0
        for request_id, hold in holds.items():
            request = requests.get(request_id, {})
            tokens = hold.get("tokens") if isinstance(hold, dict) else None
            if (request.get("status") not in {
                    "http_error_unknown_charge", "response_unknown_usage", "interrupted_unknown_charge"}
                    or request.get("reported_tokens") is not None
                    or type(tokens) is not int or tokens < 1
                    or tokens < request.get("admission_headroom", 0)
                    or not hold.get("authorization")):
                raise ValueError("unknown-request hold lacks matching request/authorization/headroom")
            total += tokens
        return total

    def _allow(self, headroom):
        if self.http >= self.limits["http"]:
            self.stop_reason = "stage_http_limit"
        elif self.tokens + headroom > self.limits["reported_tokens_stop"]:
            self.stop_reason = "stage_token_headroom"
        elif self.state["http_reserved"] >= self.state["caps"]["http"]:
            self.stop_reason = "shared_http_limit"
        elif (self.state["reported_tokens"] + self._unknown_token_reserve() + headroom
              > self.state["caps"]["reported_token_stop"]):
            self.stop_reason = "shared_token_headroom"
        return self.stop_reason is None

    def reserve(self, request=None):
        if not self.can_start_turn():
            return False
        if self.state["in_flight"] is not None:
            self.halt("overlapping_request")
            return False
        # This is admission headroom only. It is never written as actual usage.
        request = request or {}
        headroom = max(self.token_reserve,
                       len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
                       + int(request.get("max_tokens", 0)) + 1024)
        if not self._allow(headroom):
            return False
        self.http += 1
        self.state["http_reserved"] += 1
        self.request_id = f"http-{self.state['http_reserved']:05}"
        item = {"id": self.request_id, "stage": self.stage["id"],
                "status": "reserved_possibly_sent", "admission_headroom": headroom,
                "dispatch_started": False, "reported_tokens": None}
        self.state["requests"].append(item)
        self.state["in_flight"] = self.request_id
        self._save()  # durable before the parent grants network permission
        return True

    def dispatched(self, request_id):
        if request_id != self.state["in_flight"]:
            self.halt("dispatch_request_identity_mismatch")
            return False
        self.state["requests"][-1]["dispatch_started"] = True
        self._save()
        return True

    def observe(self, response, *, event="http_response", request_id=None):
        if not self.state["in_flight"] or (request_id and request_id != self.state["in_flight"]):
            self.halt("response_request_identity_mismatch")
            return False
        item = self.state["requests"][-1]
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        if event == "http_response":
            self.completed_responses += 1
        if event != "http_response" or type(total) is not int or total < 0:
            self.unknown_usage += 1
            self.state["unknown_usage"] += 1
            if event != "http_response":
                self.state["incomplete_requests"] += 1
            item["status"] = "http_error_unknown_charge" if event != "http_response" else "response_unknown_usage"
            self.state["in_flight"] = None
            self.halt(item["status"])
            return False
        self.tokens += total
        self.state["reported_tokens"] += total
        item.update(status="response_received", reported_tokens=total)
        self.state["in_flight"] = None
        self._save()
        return True

    def halt(self, reason):
        self.stop_reason = self.stop_reason or reason
        self.state["halt_reason"] = self.state["halt_reason"] or reason
        if self.state["in_flight"]:
            item = self.state["requests"][-1]
            item["status"] = "interrupted_unknown_charge"
            self.state["in_flight"] = None
            self.state["unknown_usage"] += 1
            self.state["incomplete_requests"] += 1
            self.unknown_usage += 1
        self._save()

    def report(self):
        return {"http_reserved": self.http, "reported_total_tokens": self.tokens,
                "responses_with_unknown_usage": self.unknown_usage,
                "requests_without_completed_response": self.http - self.completed_responses,
                "token_values_are_api_reported_not_estimates": True,
                "ledger": str(self.path), "shared_http_reserved": self.state["http_reserved"],
                "shared_reported_total_tokens": self.state["reported_tokens"],
                "remaining_http": max(0, self.state["caps"]["http"] - self.state["http_reserved"]),
                "remaining_reported_tokens": max(0, self.state["caps"]["reported_token_stop"] - self.state["reported_tokens"]),
                "reserved_unknown_tokens": self._unknown_token_reserve(),
                "remaining_budget_tokens_after_reserve": max(0, self.state["caps"]["reported_token_stop"]
                    - self.state["reported_tokens"] - self._unknown_token_reserve()),
                "unknown_reserve_is_not_reported_usage": True,
                "halt_reason": self.state["halt_reason"], "stage_stop_reason": self.stop_reason,
                "token_limit_kind": "stop_threshold_with_conservative_headroom_not_absolute_cap"}

    def close(self):
        try:
            if self.state["in_flight"]:
                self.halt("stage_closed_with_unresolved_request")
            self.stage.update(status=self.state["halt_reason"] or self.stop_reason or "closed",
                              end_http=self.state["http_reserved"],
                              end_reported_tokens=self.state["reported_tokens"])
            self.state["active_stage"] = None
            self._save()
        finally:
            self._unlock()


def open_for_stage(args, limits, *, root, stage, source_files, source_check):
    path = Path(args.budget_ledger).resolve()
    runs = (Path(root) / "test_workspace/runs").resolve()
    if not path.is_relative_to(runs) or path == runs:
        raise ValueError("ledger must be inside this tree's ignored test_workspace/runs")
    if not 1 <= args.parent_seconds <= 600:
        raise ValueError("one online stage must fit a 1..600 second slot")
    return SharedBudget(path, limits, used_http=args.used_http,
                        used_tokens=args.used_reported_tokens,
                        remaining_http=args.remaining_http,
                        remaining_tokens=args.remaining_reported_tokens,
                        stage=stage, source_files=source_files, source_check=source_check,
                        token_reserve=args.request_token_reserve)
