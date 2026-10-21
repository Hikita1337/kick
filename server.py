from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "public"
PORT = int(os.environ.get("PORT", "10000"))
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{1,50}$")


def get_kick_playback_url(channel_slug: str) -> dict:
    """Fetch the current public Kick channel playback URL without logging it."""
    api_url = f"https://kick.com/api/v2/channels/{channel_slug}"
    request = Request(
        api_url,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        },
        method="GET",
    )
    with urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError("Kick channel API returned an unexpected status")
        payload = json.loads(response.read(2_000_000).decode("utf-8"))

    playback_url = payload.get("playback_url")
    livestream = payload.get("livestream")
    if not isinstance(playback_url, str) or not playback_url:
        raise RuntimeError("Kick did not provide a playback URL; check that the channel is live")
    parsed = urlsplit(playback_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Kick returned an invalid playback URL")

    return {"live": bool(livestream), "channel": channel_slug, "playback_url": playback_url}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if urlsplit(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/playback-url":
            params = parse_qs(urlsplit(self.path).query)
            channel = (params.get("channel") or ["Serpens2025"])[0].strip()
            if not CHANNEL_RE.fullmatch(channel):
                self.send_json(400, {"error": "Invalid Kick channel name"})
                return
            try:
                result = get_kick_playback_url(channel)
                # Do not print the signed URL or its query parameters to logs.
                self.send_json(200, result)
            except HTTPError as exc:
                self.send_json(502, {"error": f"Kick API HTTP {exc.code}"})
            except (URLError, TimeoutError):
                self.send_json(502, {"error": "Could not reach Kick API; try again"})
            except (ValueError, json.JSONDecodeError):
                self.send_json(502, {"error": "Kick returned an unreadable response"})
            except RuntimeError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception:
                self.send_json(502, {"error": "Unable to obtain Kick playback URL"})
            return

        return super().do_GET()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
