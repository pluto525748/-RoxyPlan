"""Synthetic exceptions only: no sockets, provider, Qt or product execution."""
import json
import socket
import ssl
import unittest
from urllib.error import HTTPError, URLError

from scripts.online_transport_error import safe_transport_error_details as details


SECRET_TEXT = (
    "FAKE_API_KEY=not-a-real-key; Authorization: Bearer FAKE_BEARER; "
    "https://example.invalid/?token=FAKE_TOKEN; C:/private/fake-person/settings.env"
)


class TransportErrorDetailsChecks(unittest.TestCase):
    def safe(self, error):
        result = details(error)
        encoded = json.dumps(result, ensure_ascii=False)
        for marker in ("FAKE_", "example.invalid", "C:/private", "Authorization", "settings.env"):
            self.assertNotIn(marker, encoded)
        self.assertLessEqual(set(result), {"reason_type", "errno", "winerror", "ssl_verify_code", "http_status"})
        self.assertTrue(all(type(v) is int for k, v in result.items() if k != "reason_type"))
        return result

    def test_urlerror_gaierror_keeps_integer_errno_without_message(self):
        error = URLError(socket.gaierror(-2, SECRET_TEXT))
        self.assertEqual(self.safe(error), {"reason_type": "gaierror", "errno": -2})

    def test_ssl_verify_error_keeps_verify_code_without_cert_or_reason(self):
        error = ssl.SSLCertVerificationError(1, SECRET_TEXT)
        error.verify_code = 62
        error.verify_message = SECRET_TEXT
        self.assertEqual(self.safe(URLError(error)), {
            "reason_type": "SSLCertVerificationError", "errno": 1, "ssl_verify_code": 62})

    def test_timeout_and_oserror_codes_are_distinct_without_filename(self):
        timeout = self.safe(URLError(TimeoutError(110, SECRET_TEXT)))
        self.assertEqual(timeout, {"reason_type": "TimeoutError", "errno": 110})
        error = OSError(5, SECRET_TEXT, "C:/private/fake-person/settings.env")
        error.winerror = 10061
        self.assertEqual(self.safe(URLError(error)), {
            "reason_type": "OSError", "errno": 5, "winerror": 10061})

    def test_text_and_unknown_exception_remain_unknown_not_text_classification(self):
        class UnknownReason(Exception):
            def __str__(self):
                raise AssertionError("exception text must never be requested")
        for reason in (SECRET_TEXT, "getaddrinfo failed " + SECRET_TEXT, UnknownReason(SECRET_TEXT)):
            with self.subTest(kind=type(reason).__name__):
                self.assertEqual(self.safe(URLError(reason)), {"reason_type": "unknown"})

    def test_boolean_text_and_oversized_codes_are_not_serialized(self):
        error = OSError(5, SECRET_TEXT)
        error.errno = True
        error.winerror = SECRET_TEXT
        self.assertEqual(self.safe(error), {"reason_type": "OSError"})
        error.errno = 2 ** 100
        self.assertEqual(self.safe(error), {"reason_type": "OSError"})
        certificate = ssl.SSLCertVerificationError(1, SECRET_TEXT)
        certificate.verify_code = "62 " + SECRET_TEXT
        self.assertEqual(self.safe(certificate), {"reason_type": "SSLCertVerificationError", "errno": 1})

    def test_nested_urlerror_is_bounded_and_cycles_stay_unknown(self):
        self.assertEqual(self.safe(URLError(URLError(socket.gaierror(-2, SECRET_TEXT)))),
                         {"reason_type": "gaierror", "errno": -2})
        cycle = URLError(SECRET_TEXT)
        cycle.reason = cycle
        self.assertEqual(self.safe(cycle), {"reason_type": "unknown"})
        nested = socket.gaierror(-2, SECRET_TEXT)
        for _ in range(8):
            nested = URLError(nested)
        self.assertEqual(self.safe(nested), {"reason_type": "unknown"})

    def test_http_status_does_not_expose_url_headers_or_message(self):
        error = HTTPError("https://example.invalid/?token=FAKE_TOKEN", 502, SECRET_TEXT,
                          {"Authorization": "Bearer FAKE_BEARER"}, None)
        self.assertEqual(self.safe(error), {"reason_type": "HTTPError", "http_status": 502})

    def test_known_subclass_uses_canonical_name_not_arbitrary_classname(self):
        class SensitiveName(OSError):
            def __str__(self):
                raise AssertionError("no string/repr fallback")
        SensitiveName.__name__ = "FAKE_API_KEY_in_classname"
        self.assertEqual(self.safe(URLError(SensitiveName(5, SECRET_TEXT))),
                         {"reason_type": "OSError", "errno": 5})


if __name__ == "__main__":
    unittest.main()
