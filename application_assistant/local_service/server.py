"""Localhost-only HTTP service for the Chrome application assistant."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import stat
from typing import Any

from .ai import AIConfig
from .planner import build_plan
from .profile import ALLOWED_DOCUMENT_KEYS, document_filename, load_candidate_profile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "application_assistant" / "local_data" / "candidate_profile.json"
DEFAULT_TOKEN_PATH = PROJECT_ROOT / "application_assistant" / "local_data" / "service_token.txt"
MAX_REQUEST_BYTES = 200_000
MAX_DOCUMENT_BYTES = 10_000_000
ALLOWED_DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx"}


@dataclass(frozen=True)
class ServiceConfig:
    profile: dict[str, Any]
    allowed_origin: str
    token: str
    ai_config: AIConfig
    profile_path: Path | None = None


def extension_origin(extension_id: str) -> str:
    """Build and validate the one Chrome extension origin allowed by the service."""
    extension_id = extension_id.strip().lower()
    if len(extension_id) != 32 or any(character not in "abcdefghijklmnop" for character in extension_id):
        raise ValueError("Chrome-utvidelses-ID-en må være 32 tegn fra a til p.")
    return f"chrome-extension://{extension_id}"


def load_or_create_token(path: Path) -> str:
    """Create a private local bearer token without placing it in Git."""
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise RuntimeError(f"Lokalt tjenestetoken er ugyldig: {path}")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return token


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class ApplicationAssistantHandler(BaseHTTPRequestHandler):
    """Serve health and planning endpoints without logging request bodies."""

    server_version = "JobbagentLocal/1"

    @property
    def config(self) -> ServiceConfig:
        return self.server.service_config  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        print(f"Lokal tjeneste: {self.command} {self.path}")

    def _origin_allowed(self) -> bool:
        return self.headers.get("Origin", "") == self.config.allowed_origin

    def _token_allowed(self) -> bool:
        provided = self.headers.get("X-Jobbagent-Token", "")
        return secrets.compare_digest(provided, self.config.token)

    def _send_json(self, status: int, value: dict[str, Any], cors: bool = False) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", self.config.allowed_origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"error": "Nettleserkilden er ikke tillatt."})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.config.allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Jobbagent-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(404, {"error": "Ukjent endepunkt."})
            return
        if not self._origin_allowed() or not self._token_allowed():
            self._send_json(403, {"error": "Tilgang avvist."})
            return
        self._send_json(
            200,
            {"status": "ok", "ai_enabled": self.config.ai_config.enabled},
            cors=True,
        )

    def do_POST(self) -> None:
        if self.path not in {"/plan", "/document"}:
            self._send_json(404, {"error": "Ukjent endepunkt."})
            return
        if not self._origin_allowed() or not self._token_allowed():
            self._send_json(403, {"error": "Tilgang avvist."})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Forespørselen er tom eller for stor."}, cors=True)
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            if self.path == "/document":
                self._send_document(payload)
                return
            profile = (
                load_candidate_profile(self.config.profile_path)
                if self.config.profile_path
                else self.config.profile
            )
            result = build_plan(payload, profile, self.config.ai_config)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self._send_json(400, {"error": str(error)}, cors=True)
            return
        except Exception:
            self._send_json(
                500,
                {"error": "Planleggingen feilet uten at kandidatdata ble logget."},
                cors=True,
            )
            return
        self._send_json(200, result.payload(), cors=True)

    def _send_document(self, payload: Any) -> None:
        """Return one allowlisted local document without exposing its path."""
        if not isinstance(payload, dict):
            raise ValueError("Dokumentforespørselen må være et objekt.")
        document_key = str(payload.get("document_key") or "")
        if document_key not in ALLOWED_DOCUMENT_KEYS:
            raise ValueError("Dokumenttypen er ikke tillatt.")
        if not self.config.profile_path:
            raise ValueError("Lokal dokumentmappe er ikke konfigurert.")

        profile = load_candidate_profile(self.config.profile_path)
        filename = document_filename(profile, document_key)
        if not filename:
            raise ValueError("Dokumentet er ikke konfigurert.")
        root = self.config.profile_path.parent.resolve()
        path = (root / filename).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("Dokumentet finnes ikke i den lokale kandidatmappen.")
        if path.suffix.lower() not in ALLOWED_DOCUMENT_SUFFIXES:
            raise ValueError("Dokumentformatet er ikke tillatt.")
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError("Dokumentet er større enn grensen på 10 MB.")

        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._send_json(
            200,
            {
                "document_key": document_key,
                "filename": filename,
                "mime_type": mime_type,
                "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            },
            cors=True,
        )


class ApplicationAssistantServer(ThreadingHTTPServer):
    service_config: ServiceConfig


def create_server(host: str, port: int, config: ServiceConfig) -> ApplicationAssistantServer:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Tjenesten kan bare lytte på localhost.")
    server = ApplicationAssistantServer((host, port), ApplicationAssistantHandler)
    server.service_config = config
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start Jobbagents lokale søknadsassistent.")
    parser.add_argument("--extension-id", required=True, help="ID fra chrome://extensions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = load_candidate_profile(args.profile)
    token = load_or_create_token(args.token_file)
    config = ServiceConfig(
        profile=profile,
        allowed_origin=extension_origin(args.extension_id),
        token=token,
        ai_config=AIConfig.from_env(),
        profile_path=args.profile,
    )
    server = create_server(args.host, args.port, config)
    print(f"Jobbagent lytter på http://{args.host}:{args.port}")
    print(f"Tillatt utvidelse: {args.extension_id}")
    print(f"Lokalt token: {args.token_file} (kopier innholdet til utvidelsen)")
    print("Tjenesten kan planlegge og foreslå, men kan ikke sende en søknad.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJobbagent er stoppet.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
