"""Authenticated-edge metadata service for the read-only production pilot."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os

from .pilot_canary import read_secret_file
from .postgresql import PostgreSQLPilotStore


def build_store_from_environment() -> PostgreSQLPilotStore:
    if os.environ.get("AOS_AUTH_PROXY") != "cloud-run-iam":
        raise RuntimeError("pilot service requires the Cloud Run IAM boundary")
    tenant_id = os.environ.get("AOS_TENANT_ID", "")
    business_id = os.environ.get("AOS_BUSINESS_ID", "")
    if not tenant_id or not business_id:
        raise RuntimeError("pilot service scope is missing")
    return PostgreSQLPilotStore(
        read_secret_file(os.environ.get("AOS_POSTGRES_DSN_FILE")),
        tenant_id=tenant_id,
        business_id=business_id,
    )


def handler_for(store: PostgreSQLPilotStore) -> type[BaseHTTPRequestHandler]:
    class PilotHandler(BaseHTTPRequestHandler):
        server_version = "AgentOSPilot/1"

        def log_message(self, format: str, *args: object) -> None:
            # Request metadata is emitted by the authenticated Cloud Run edge.
            return

        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send(200, {"status": "alive"})
                return
            if self.path == "/readyz":
                status = store.schema_status()
                self._send(200 if status["migration_valid"] else 503, status)
                return
            if self.path == "/":
                try:
                    self._send(200, store.pilot_snapshot())
                except Exception:
                    self._send(503, {"status": "unavailable"})
                return
            self._send(404, {"status": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            self._send(405, {"status": "method_not_allowed"})

        do_DELETE = do_POST
        do_PATCH = do_POST
        do_PUT = do_POST

    return PilotHandler


def main() -> None:
    store = build_store_from_environment()
    status = store.schema_status()
    if not status["migration_valid"]:
        raise RuntimeError("pilot database is not ready")
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), handler_for(store)).serve_forever()


if __name__ == "__main__":
    main()
