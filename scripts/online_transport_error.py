"""Allowlisted transport diagnostics, without exception text or private data.

Never stringify exceptions, reasons, URLs, headers, filenames or tracebacks.
These observations classify an available exception; they do not prove which
remote service or local configuration caused the failure.
"""
from __future__ import annotations

import socket
import ssl
from urllib.error import HTTPError, URLError


def _attribute(value, name):
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _code(value):
    # Exclude bool, integer subclasses and arbitrary encoded/textual values.
    return type(value) is int and -(2 ** 31) <= value < 2 ** 31


def safe_transport_error_details(error):
    """Return only canonical reason types and available plain integer codes.

    URLError.reason may be an OSError, a nested URLError, or sensitive text.
    Unknown/text reasons remain unknown; no text heuristics or repr fallback.
    """
    seen = set()
    for _ in range(4):
        if id(error) in seen:
            return {"reason_type": "unknown"}
        seen.add(id(error))
        if isinstance(error, HTTPError):
            result = {"reason_type": "HTTPError"}
            status = _attribute(error, "code")
            if _code(status) and 100 <= status <= 599:
                result["http_status"] = status
            return result
        if not isinstance(error, URLError):
            break
        reason = _attribute(error, "reason")
        if not isinstance(reason, BaseException):
            return {"reason_type": "unknown"}
        error = reason
    else:
        return {"reason_type": "unknown"}

    known = (
        (ssl.SSLCertVerificationError, "SSLCertVerificationError"),
        (ssl.SSLEOFError, "SSLEOFError"),
        (ssl.SSLZeroReturnError, "SSLZeroReturnError"),
        (ssl.SSLError, "SSLError"),
        (socket.gaierror, "gaierror"),
        (TimeoutError, "TimeoutError"),
        (ConnectionRefusedError, "ConnectionRefusedError"),
        (ConnectionResetError, "ConnectionResetError"),
        (ConnectionAbortedError, "ConnectionAbortedError"),
        (BrokenPipeError, "BrokenPipeError"),
        (OSError, "OSError"),
    )
    name = next((name for cls, name in known if isinstance(error, cls)), None)
    if name is None:
        return {"reason_type": "unknown"}
    result = {"reason_type": name}
    for field in ("errno", "winerror"):
        value = _attribute(error, field)
        if _code(value):
            result[field] = value
    if isinstance(error, ssl.SSLCertVerificationError):
        value = _attribute(error, "verify_code")
        if _code(value):
            result["ssl_verify_code"] = value
    return result
