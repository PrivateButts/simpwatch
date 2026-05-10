import json
import os
import secrets
import socket
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from pathlib import Path


AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


def _load_dotenv() -> None:
    """Load .env key/value pairs into process environment if unset.

    For this tool, .env should be the source of truth to avoid accidentally
    using stale exported variables from a prior shell session.
    """

    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    dotenv_path = next((path for path in candidates if path.is_file()), None)
    if dotenv_path is None:
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        # Remove matching quotes around values.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ[key] = value


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    print(f"Missing required env var: {name}", file=sys.stderr)
    raise SystemExit(1)


def _normalize_scopes(raw: str) -> str:
    scopes = " ".join(part for part in raw.replace(",", " ").split() if part)
    return scopes or "user:bot user:read:chat user:write:chat"


def _exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")

    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _validate_token(access_token: str) -> dict:
    if not access_token:
        return {}

    request = urllib.request.Request(
        VALIDATE_URL,
        headers={"Authorization": f"OAuth {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    _load_dotenv()

    client_id = _required_env("TWITCH_CLIENT_ID")
    client_secret = _required_env("TWITCH_CLIENT_SECRET")
    redirect_uri = (
        os.getenv("TWITCH_TOKEN_REDIRECT_URI", "").strip()
        or "http://localhost:8917/callback"
    )
    scope = _normalize_scopes(
        os.getenv("TWITCH_TOKEN_SCOPES", "user:bot user:read:chat user:write:chat")
    )
    callback_timeout_seconds = int(
        os.getenv("TWITCH_TOKEN_CALLBACK_TIMEOUT_SECONDS", "300")
    )

    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or not parsed.hostname or not parsed.port:
        print(
            "TWITCH_TOKEN_REDIRECT_URI must be an http:// URL with explicit host and port, "
            "for example http://localhost:8917/callback",
            file=sys.stderr,
        )
        return 1

    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        print(
            "TWITCH_TOKEN_REDIRECT_URI must target this machine (localhost/127.0.0.1/::1). "
            "Do not use external redirect sites for this local flow.",
            file=sys.stderr,
        )
        return 1

    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    authorize_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    print("Open this URL in your browser:")
    print(authorize_url)
    print()
    print(
        f"Waiting for callback on {parsed.hostname}:{parsed.port}{parsed.path} "
        f"(timeout: {callback_timeout_seconds}s) ..."
    )

    class CallbackHandler(BaseHTTPRequestHandler):
        result: dict[str, str] = {}

        def log_message(self, _format: str, *_args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            if url.path != parsed.path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            query = urllib.parse.parse_qs(url.query)
            code = query.get("code", [""])[0]
            returned_state = query.get("state", [""])[0]
            error = query.get("error", [""])[0]
            error_description = query.get("error_description", [""])[0]

            if error:
                CallbackHandler.result = {
                    "error": error,
                    "error_description": error_description,
                }
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Twitch authorization failed. Return to your terminal.")
                return

            if returned_state != state:
                CallbackHandler.result = {
                    "error": "state_mismatch",
                    "error_description": "Returned state did not match.",
                }
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"State mismatch. Return to your terminal.")
                return

            if not code:
                CallbackHandler.result = {
                    "error": "missing_code",
                    "error_description": "No authorization code in callback.",
                }
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code. Return to your terminal.")
                return

            CallbackHandler.result = {"code": code}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization received. You can close this tab.")

    try:
        server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
    except OSError as exc:
        if exc.errno == 98:
            print(
                f"Callback port {parsed.port} is already in use. Stop the other process "
                "or set TWITCH_TOKEN_REDIRECT_URI to a different localhost port, then "
                "register that exact URI in your Twitch app settings.",
                file=sys.stderr,
            )
            return 1
        raise
    try:
        server.socket.settimeout(1.0)
        deadline = time.monotonic() + max(callback_timeout_seconds, 1)
        while time.monotonic() < deadline and not CallbackHandler.result:
            try:
                server.handle_request()
            except (TimeoutError, socket.timeout):
                continue
    finally:
        server.server_close()

    if not CallbackHandler.result:
        print(
            "Timed out waiting for OAuth callback. Run `just twitch-token` again after "
            "verifying the redirect URI configured in your Twitch application exactly "
            f"matches: {redirect_uri}",
            file=sys.stderr,
        )
        return 1

    if "error" in CallbackHandler.result:
        print(
            "OAuth callback failed: "
            + CallbackHandler.result.get("error", "unknown_error")
            + " "
            + CallbackHandler.result.get("error_description", ""),
            file=sys.stderr,
        )
        return 1

    code = CallbackHandler.result.get("code", "")
    if not code:
        print("No code captured from callback.", file=sys.stderr)
        return 1

    try:
        token_data = _exchange_code_for_tokens(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
    except Exception as exc:
        print(f"Token exchange failed: {exc}", file=sys.stderr)
        return 1

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")

    validation = {}
    try:
        validation = _validate_token(access_token)
    except Exception as exc:
        print(f"Token validate failed (non-fatal): {exc}", file=sys.stderr)

    print()
    print("Use these values in .env:")
    print(f"TWITCH_BOT_USERNAME={validation.get('login', '')}")
    print(f"TWITCH_BOT_ID={validation.get('user_id', '')}")
    print(f"TWITCH_BOT_ACCESS_TOKEN={access_token}")
    print(f"TWITCH_BOT_REFRESH_TOKEN={refresh_token}")
    print()
    print("OAuth format access token (for TWITCH_OAUTH_TOKEN if needed by other tooling):")
    print(f"oauth:{access_token}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
