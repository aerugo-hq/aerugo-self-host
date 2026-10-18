"""Browser tests (slice D5): Chromium, driven by Playwright, uses the web app the way a person does.

The browser runs in this container, but signing in only works at the addresses the app and Keycloak are configured
with (http://localhost:3000 and http://localhost:8081), or the redirects between them wouldn't match. So before any
test runs, this file forwards those two ports, inside the container, to the web and Keycloak containers: the browser
sees exactly what yours does.
"""
import os
import socket
import threading
import time
import urllib.request

import pytest

WEB = "http://localhost:3000"
KEYCLOAK_PORT = int(os.environ.get("KL_KEYCLOAK_PORT", "8081"))
PASSWORDS = {u: f"{u}-dev-only" for u in ("alice", "bob", "carol", "dave", "olivia", "nora")}
WEB_B = "http://localhost:3001"  # a second web server beside the first (H3): a session must live in neither alone
WEB_C = "http://localhost:3002"  # a third, whose calls to the API run out of time at once (M9)
FORWARDS = {3000: (os.environ.get("KL_E2E_WEB", "web"), 3000),
            3001: (os.environ.get("KL_E2E_WEB_B", "web-b"), 3000),
            3002: (os.environ.get("KL_E2E_WEB_C", "web-c"), 3000),
            KEYCLOAK_PORT: (os.environ.get("KL_E2E_KEYCLOAK", "keycloak"), 8080)}


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


def _serve(server: socket.socket, target: tuple[str, int]) -> None:
    while True:
        client, _ = server.accept()
        try:
            upstream = socket.create_connection(target, timeout=10)
            upstream.settimeout(None)
        except OSError:
            client.close()
            continue
        for a, b in ((client, upstream), (upstream, client)):
            threading.Thread(target=_pipe, args=(a, b), daemon=True).start()


def forward(port: int, target: tuple[str, int]) -> int:
    """Listen on the container's loopback (IPv4, and IPv6 where it exists) and relay to target. Returns listeners."""
    started = 0
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            server = socket.create_server((host, port), family=family)
        except OSError:
            continue  # no IPv6 in this container
        threading.Thread(target=_serve, args=(server, target), daemon=True).start()
        started += 1
    return started


@pytest.fixture(scope="session", autouse=True)
def _forwarded_and_ready():
    for port, target in FORWARDS.items():
        assert forward(port, target), f"couldn't listen on localhost:{port}"
    deadline = time.monotonic() + 240  # Next.js compiles pages on first request, and Keycloak is slow to boot
    for url in (f"{WEB}/", f"http://localhost:{KEYCLOAK_PORT}/realms/acme/.well-known/openid-configuration"):
        while True:
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    if r.status < 500:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"{url} never answered: is the web app up (make web)?")
            time.sleep(2)


def sign_in(page, user: str) -> None:
    """Sign in as a person does: the app sends the browser to Keycloak, which sends it back signed in."""
    page.goto(f"{WEB}/auth/login")
    page.locator("#username").fill(user)
    page.locator("#password").fill(PASSWORDS[user])
    page.locator("#kc-login").click()
    page.wait_for_url(f"{WEB}/**", timeout=60_000)
    page.get_by_role("link", name="Map", exact=True).wait_for(timeout=60_000)  # exact: Home also offers "Open the map"
