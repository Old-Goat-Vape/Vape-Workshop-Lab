"""Capture PNG screenshots of guided V7 preview HTML files."""
from __future__ import annotations

import http.server
import os
import socket
import threading
import time

from playwright.sync_api import sync_playwright

APP_DIR = os.path.join(os.path.dirname(__file__), "..")
PREVIEW_DIR = os.path.join(APP_DIR, "static", "previews")
PREVIEWS = [
    "guided_v7_prep.html",
    "guided_v7_nicotine_tare.html",
    "guided_v7_nicotine_pour.html",
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    port = _free_port()
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    os.chdir(APP_DIR)
    thread.start()
    time.sleep(0.3)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for name in PREVIEWS:
            url = f"http://127.0.0.1:{port}/static/previews/{name}"
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(400)
            out = os.path.join(PREVIEW_DIR, name.replace(".html", ".png"))
            page.screenshot(path=out, full_page=True)
            print(f"Wrote {out}")
        browser.close()

    server.shutdown()


if __name__ == "__main__":
    main()
