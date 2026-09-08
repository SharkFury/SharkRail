"""Bounded HTTP interface for the asynchronous Job service."""

from __future__ import annotations

import hmac
import ipaddress
import json
import socket
import socketserver
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .config import is_loopback_listener
from .server import JobService
from .store import AdmissionLimited, IdempotencyConflict, JobNotFound, StoreError


class JobHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: JobService) -> None:
        if not is_loopback_listener(address[0]):
            raise ValueError(
                "Job HTTP server must bind to loopback; use a same-host TLS reverse proxy"
            )
        self.service = service
        try:
            address_value = ipaddress.ip_address(address[0])
        except ValueError:
            address_value = None
        if address_value is not None and address_value.version == 6:
            self.address_family = socket.AF_INET6
        request_limit = min(
            service.config.admission.max_concurrent_requests,
            service.config.control.api_workers,
        )
        self.request_slots = threading.BoundedSemaphore(request_limit)
        self.submission_slots = threading.BoundedSemaphore(max(1, request_limit - 1))
        super().__init__(address, JobRequestHandler)

    def server_bind(self) -> None:
        """Bind without HTTPServer's potentially blocking reverse-DNS lookup."""

        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.request_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            request.settimeout(self.service.config.server.request_timeout_seconds)
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


class JobRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SharkRail"
    _tenant_id: str

    @property
    def job_server(self) -> JobHTTPServer:
        assert isinstance(self.server, JobHTTPServer)
        return self.server

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health/state":
            if not self._authorized_admin():
                return
        elif path in {"/health/live", "/health/ready"}:
            if not self._authorized_probe():
                return
        elif not self._authorized_tenant():
            return
        self._do_get()

    def do_POST(self) -> None:
        if not self._authorized_tenant():
            return
        self._do_post()

    def _do_get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health/live":
            health = self.job_server.service.health()
            self._json(
                HTTPStatus.OK if health["live"] else HTTPStatus.SERVICE_UNAVAILABLE,
                {"live": health["live"]},
            )
            return
        if parsed.path == "/health/ready":
            health = self.job_server.service.health()
            self._json(
                HTTPStatus.OK if health["ready"] else HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ready": health["ready"],
                    "degraded": health["degraded"],
                    "reason": health["reason"],
                },
            )
            return
        if parsed.path == "/health/state":
            self._json(HTTPStatus.OK, self.job_server.service.health())
            return
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 3 or parts[:2] != ["v1", "jobs"]:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        job_id = parts[2]
        tenant = self._tenant_id
        try:
            if len(parts) == 3:
                job = self.job_server.service.get(job_id, tenant)
                self._json(HTTPStatus.OK, job.to_dict())
                return
            if len(parts) == 4 and parts[3] == "result":
                result = self.job_server.service.result(job_id, tenant)
                self._json(HTTPStatus.OK, result)
                return
            if len(parts) == 4 and parts[3] == "output":
                query = parse_qs(parsed.query)
                stream = query.get("stream", ["stdout"])[0]
                data = self.job_server.service.read_output(job_id, stream, tenant)
                self._bytes(HTTPStatus.OK, data)
                return
        except JobNotFound:
            self._json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
            return
        except StoreError as err:
            self._json(HTTPStatus.CONFLICT, {"error": str(err)})
            return
        except (OSError, ValueError) as err:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(err)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _do_post(self) -> None:
        parsed = urlparse(self.path)
        tenant = self._tenant_id
        if parsed.path == "/v1/jobs":
            if not self.job_server.submission_slots.acquire(blocking=False):
                self._json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"error": "submission capacity full"},
                    headers={"Retry-After": "1"},
                )
                return
            key = self.headers.get("Idempotency-Key")
            if not key:
                self.job_server.submission_slots.release()
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Idempotency-Key header is required"},
                )
                return
            try:
                payload = self._read_json()
                job, created = self.job_server.service.submit(tenant, key, payload)
            except AdmissionLimited as err:
                self._json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"error": str(err)},
                    headers={"Retry-After": "1"},
                )
                return
            except IdempotencyConflict as err:
                self._json(HTTPStatus.CONFLICT, {"error": str(err)})
                return
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ) as err:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(err)})
                return
            finally:
                self.job_server.submission_slots.release()
            self._json(
                HTTPStatus.ACCEPTED if created else HTTPStatus.OK,
                job.to_dict(),
                headers={"Location": f"/v1/jobs/{job.id}"},
            )
            return
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["v1", "jobs"] and parts[3] == "cancel":
            try:
                job = self.job_server.service.cancel(parts[2], tenant)
            except JobNotFound:
                self._json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            self._json(HTTPStatus.ACCEPTED, job.to_dict())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as err:
            raise ValueError("invalid Content-Length") from err
        maximum = self.job_server.service.config.server.request_body_max_bytes
        if length < 0 or length > maximum:
            raise ValueError(f"request body exceeds {maximum} bytes")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("request body must be a JSON object")
        return value

    def _authorized_tenant(self) -> bool:
        settings = self.job_server.service.config.server
        credentials: list[tuple[str, str]] = list(settings.tenant_tokens.items())
        if settings.auth_token is not None:
            credentials.append(("default", settings.auth_token))

        if not credentials:
            tenant = "default"
            claimed = self.headers.get("X-SharkRail-Tenant")
            if claimed not in {None, tenant}:
                self._json(HTTPStatus.FORBIDDEN, {"error": "tenant identity mismatch"})
                return False
            self._tenant_id = tenant
            return True

        supplied = self.headers.get("Authorization", "")
        matched: Optional[str] = None
        for tenant, token in credentials:
            if self._bearer_matches(supplied, token):
                matched = tenant
        if matched is not None:
            claimed = self.headers.get("X-SharkRail-Tenant")
            if claimed not in {None, matched}:
                self._json(HTTPStatus.FORBIDDEN, {"error": "tenant identity mismatch"})
                return False
            self._tenant_id = matched
            return True
        self._json(
            HTTPStatus.UNAUTHORIZED,
            {"error": "unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        )
        return False

    def _authorized_probe(self) -> bool:
        settings = self.job_server.service.config.server
        supplied = self.headers.get("Authorization", "")
        if settings.admin_token is not None and self._bearer_matches(
            supplied, settings.admin_token
        ):
            return True
        return self._authorized_tenant()

    def _authorized_admin(self) -> bool:
        settings = self.job_server.service.config.server
        expected = settings.admin_token
        if expected is None:
            if settings.auth_token is None and not settings.tenant_tokens:
                return True
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": "administrative health access is not configured"},
            )
            return False
        supplied = self.headers.get("Authorization", "")
        if self._bearer_matches(supplied, expected):
            return True
        self._json(
            HTTPStatus.UNAUTHORIZED,
            {"error": "unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        )
        return False

    @staticmethod
    def _bearer_matches(supplied: str, token: str) -> bool:
        try:
            supplied_bytes = supplied.encode("latin-1")
            expected_bytes = f"Bearer {token}".encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(supplied_bytes, expected_bytes)

    def _json(
        self,
        status: HTTPStatus,
        value: object,
        *,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._bytes(
            status,
            body,
            content_type="application/json; charset=utf-8",
            headers=headers,
        )

    def _bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if self.job_server.service.config.durability == "volatile":
            self.send_header("X-SharkRail-Durability", "volatile")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Route request logs through application logging instead of stderr.
        return


def serve_http(service: JobService) -> None:
    """Run the HTTP server until interrupted."""

    server = JobHTTPServer(
        (service.config.server.host, service.config.server.port), service
    )
    service.start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        service.close()
