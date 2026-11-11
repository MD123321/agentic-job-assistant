"""Tests for localhost, origin, token, and response-data boundaries."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from application_assistant.local_service.ai import AIConfig
from application_assistant.local_service.server import (
    ServiceConfig,
    create_server,
    extension_origin,
    load_or_create_token,
)


EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"


class ServerSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        config = ServiceConfig(
            profile={"personal": {"email": "candidate@example.com"}},
            allowed_origin=extension_origin(EXTENSION_ID),
            token="local-test-token-with-more-than-32-characters",
            ai_config=AIConfig(enabled=False, model="test", timeout_seconds=1),
        )
        self.server = create_server("127.0.0.1", 0, config)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, origin: str, token: str) -> Request:
        body = json.dumps(
            {
                "fields": [
                    {
                        "field_id": "field-0",
                        "dom_index": 0,
                        "label": "Email",
                        "field_type": "email",
                    }
                ],
                "page": {},
            }
        ).encode()
        return Request(
            f"{self.base_url}/plan",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Origin": origin,
                "X-Jobbagent-Token": token,
            },
            method="POST",
        )

    def test_accepts_only_configured_extension_origin_and_token(self) -> None:
        request = self.request(
            extension_origin(EXTENSION_ID),
            "local-test-token-with-more-than-32-characters",
        )
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
            status = response.status

        self.assertEqual(status, 200)
        self.assertEqual(payload["fields"][0]["value"], "candidate@example.com")
        self.assertEqual(payload["submission"], "manual_only")
        self.assertNotIn("OPENAI_API_KEY", json.dumps(payload))

    def test_rejects_other_web_origins(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.request("https://malicious.example", self.server.service_config.token))

        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_rejects_wrong_local_token(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.request(extension_origin(EXTENSION_ID), "wrong-token"))

        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_refuses_non_localhost_binding(self) -> None:
        with self.assertRaises(ValueError):
            create_server("0.0.0.0", 0, self.server.service_config)

    def test_creates_private_persistent_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.txt"
            first = load_or_create_token(path)
            second = load_or_create_token(path)

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_reloads_local_profile_for_each_plan_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            profile_path.write_text(
                json.dumps({"personal": {"email": "first@example.com"}}),
                encoding="utf-8",
            )
            config = ServiceConfig(
                profile={},
                allowed_origin=extension_origin(EXTENSION_ID),
                token="another-local-token-with-more-than-32-characters",
                ai_config=AIConfig(enabled=False, model="test", timeout_seconds=1),
                profile_path=profile_path,
            )
            server = create_server("127.0.0.1", 0, config)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"

            def planned_email() -> str:
                body = json.dumps(
                    {
                        "fields": [
                            {
                                "field_id": "field-0",
                                "dom_index": 0,
                                "label": "Email",
                                "field_type": "email",
                            }
                        ],
                        "page": {},
                    }
                ).encode()
                request = Request(
                    f"{base_url}/plan",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Origin": extension_origin(EXTENSION_ID),
                        "X-Jobbagent-Token": config.token,
                    },
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    return json.load(response)["fields"][0]["value"]

            try:
                self.assertEqual(planned_email(), "first@example.com")
                profile_path.write_text(
                    json.dumps({"personal": {"email": "second@example.com"}}),
                    encoding="utf-8",
                )
                self.assertEqual(planned_email(), "second@example.com")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_serves_only_allowlisted_document_from_profile_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(
                json.dumps({"documents": {"standard_cv": "cv.pdf"}}),
                encoding="utf-8",
            )
            (root / "cv.pdf").write_bytes(b"private-pdf-content")
            token = "document-token-with-more-than-32-characters"
            config = ServiceConfig(
                profile={},
                allowed_origin=extension_origin(EXTENSION_ID),
                token=token,
                ai_config=AIConfig(enabled=False, model="test", timeout_seconds=1),
                profile_path=profile_path,
            )
            server = create_server("127.0.0.1", 0, config)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = Request(
                f"http://127.0.0.1:{server.server_port}/document",
                data=json.dumps({"document_key": "standard_cv"}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Origin": config.allowed_origin,
                    "X-Jobbagent-Token": token,
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=2) as response:
                    payload = json.load(response)
                self.assertEqual(payload["filename"], "cv.pdf")
                self.assertEqual(base64.b64decode(payload["base64"]), b"private-pdf-content")
                self.assertNotIn(str(root), json.dumps(payload))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_rejects_unknown_document_key(self) -> None:
        request = Request(
            f"{self.base_url}/document",
            data=json.dumps({"document_key": "passport"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": self.server.service_config.allowed_origin,
                "X-Jobbagent-Token": self.server.service_config.token,
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
