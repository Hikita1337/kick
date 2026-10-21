from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urljoin, urlunsplit
from urllib.request import Request, urlopen
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "public"
PORT = int(os.environ.get("PORT", "10000"))
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


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
    is_live = bool(livestream)

    # Kick may keep a playback URL in the channel response while the channel is offline.
    # Never send an offline URL to the player.
    if not is_live:
        return {"live": False, "channel": channel_slug, "playback_url": None}

    if not isinstance(playback_url, str) or not playback_url:
        raise RuntimeError("Kick reports the channel as live but did not provide a playback URL")
    parsed = urlsplit(playback_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Kick returned an invalid playback URL")

    # The channel API can briefly report livestream metadata after the CDN
    # master playlist has already disappeared. Probe it before reporting playback
    # as ready. Never log the signed URL or its query parameters.
    probe = Request(
        playback_url,
        headers={
            "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, */*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        },
        method="GET",
    )
    try:
        with urlopen(probe, timeout=8) as response:
            sample = response.read(4096)
            if response.status != 200:
                raise RuntimeError(
                    f"Kick API reports live, but CDN playlist returned HTTP {response.status}"
                )
            if b"#EXTM3U" not in sample:
                raise RuntimeError(
                    "Kick API reports live, but CDN response is not an HLS master playlist"
                )
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                "Kick API reports live, but the CDN master playlist returned HTTP 404. "
                "The stream may have just ended or Kick's live metadata may be stale; retry."
            ) from None
        raise RuntimeError(
            f"Kick API reports live, but CDN playlist check returned HTTP {exc.code}"
        ) from None
    except (URLError, TimeoutError):
        raise RuntimeError(
            "Kick API reports live, but the CDN playlist could not be reached from the server"
        ) from None

    return {"live": True, "channel": channel_slug, "playback_url": playback_url}


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


    def do_POST(self):
        if urlsplit(self.path).path != "/api/resolve-playlist":
            self.send_json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 128 * 1024:
                self.send_json(413, {"error": "invalid_body_size"})
                return
            payload = json.loads(self.rfile.read(length))
            master = payload.get("playbackUrl", "")
            ua = payload.get("userAgent", "")
            parsed = urlsplit(master if isinstance(master, str) else "")
            allowed_master_host = "fa723fc1b171.us-west-2.playback.live-video.net"
            if (parsed.scheme != "https" or parsed.hostname != allowed_master_host
                    or not parsed.path.endswith(".m3u8")):
                self.send_json(400, {"error": "unsupported_playback_url"})
                return
            if (not isinstance(ua, str) or not ua or len(ua) > 1024
                    or "\r" in ua or "\n" in ua):
                self.send_json(400, {"error": "invalid_user_agent"})
                return
            request = Request(master, headers={
                "User-Agent": ua,
                "Accept": "application/x-mpegURL, application/vnd.apple.mpegurl, */*",
                "Cache-Control": "no-cache",
            }, method="GET")
            with urlopen(request, timeout=12) as response:
                if response.status != 200:
                    self.send_json(502, {"error": "upstream_http", "status": response.status})
                    return
                text = response.read(1024 * 1024).decode("utf-8", "replace")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            variants = []
            for i, line in enumerate(lines):
                if not line.startswith("#EXT-X-STREAM-INF:"):
                    continue
                uri = next((x for x in lines[i + 1:] if not x.startswith("#")), None)
                if not uri:
                    continue
                attrs = line.split(":", 1)[1]
                resolution = re.search(r"(?:^|,)RESOLUTION=(\d+)x(\d+)", attrs)
                bandwidth = re.search(r"(?:^|,)BANDWIDTH=(\d+)", attrs)
                fps = re.search(r"(?:^|,)FRAME-RATE=([\d.]+)", attrs)
                child = urljoin(master, uri)
                cp = urlsplit(child)
                host = (cp.hostname or "").lower()
                allowed_child = (
                    host.endswith(".cloudfront.hls.live-video.net")
                    or host.endswith(".playback.live-video.net")
                    or host.endswith(".playlist.live-video.net")
                )
                if cp.scheme != "https" or not allowed_child:
                    continue
                if not cp.query and parsed.query:
                    child = urlunsplit(cp._replace(query=parsed.query))
                width = int(resolution.group(1)) if resolution else 0
                height = int(resolution.group(2)) if resolution else 0
                variants.append({
                    "url": child, "width": width, "height": height,
                    "bandwidth": int(bandwidth.group(1)) if bandwidth else 0,
                    "fps": float(fps.group(1)) if fps else None,
                    "label": f"{height}p" if height else "Unknown quality",
                })
            if not variants:
                self.send_json(502, {"error": "no_variants_in_master_playlist"})
                return
            variants.sort(key=lambda item: (item["height"], item["bandwidth"]), reverse=True)
            preferred = next(
                (item for height in (480, 720, 360) for item in variants if item["height"] == height),
                variants[0],
            )
            print("resolver: upstream=200 variants=%s selected=%s" % (
                ",".join(item["label"] for item in variants), preferred["label"]
            ), flush=True)
            self.send_json(200, {"ok": True, "isMediaPlaylist": False,
                                 "variants": variants, "preferred": preferred})
        except HTTPError as exc:
            self.send_json(502, {"error": "upstream_http", "status": exc.code})
        except (URLError, TimeoutError):
            self.send_json(502, {"error": "upstream_unreachable"})
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid_json"})
        except Exception as exc:
            print("resolver error: %s" % type(exc).__name__, flush=True)
            self.send_json(502, {"error": "resolver_failed", "detail": type(exc).__name__})

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
            channel = (params.get("channel") or ["good-girl21"])[0].strip()
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
