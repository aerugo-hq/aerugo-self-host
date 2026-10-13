"""One-click GitHub App registration through GitHub's manifest flow (dev helper).

    make github-app ORG=<your-github-org>

Open the printed URL and click the button. GitHub creates the App with exactly the permissions
below, owned by the org (a private App can only be installed on the account that owns it), then
redirects back here. We exchange the one-time code for the App id and private key and write them
to .secrets/ (gitignored). M1 moves these credentials into the secrets backend.
"""
import html
import json
import os
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

ORG = os.environ.get("GITHUB_ORG", "").strip()
HOST_PORT = int(os.environ.get("KL_SETUP_PORT", "8765"))  # port on your machine (redirect target)
LISTEN_PORT = 8765                                        # port inside the container
OUT_DIR = os.environ.get("KL_SECRETS_DIR", "/run/kl-secrets")
STATE = secrets.token_urlsafe(24)                         # CSRF protection for the round trip

# Least privilege for the connector: read content, read repo metadata (incl. collaborators),
# read org membership (teams). Webhooks stay off until M1's webhook slice.
PERMISSIONS = {"contents": "read", "metadata": "read", "members": "read"}
# The curator (KL_APP_KIND=curator) opens compiler proposals as PRs, so it writes, which is why it's a
# separate App: install it on your *-knowledge repos and nothing else.
KIND = os.environ.get("KL_APP_KIND", "connector")
if KIND == "curator":
    PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}
PREFIX = "curator-app" if KIND == "curator" else "github-app"


def manifest() -> dict:
    return {
        "name": (f"{ORG}-kl-curator" if KIND == "curator" else f"{ORG}-knowledge-layer")[:34],
        "url": f"https://github.com/{ORG}",
        "description": ("knowledge-layer curator (dev): opens reviewed-before-merge pull requests with compiler drafts. "
                        "Install it only on *-knowledge repos.") if KIND == "curator" else
                       "knowledge-layer connector (dev): reads repository content and mirrors repository and team permissions.",
        "public": False,
        "redirect_url": f"http://localhost:{HOST_PORT}/callback",
        "hook_attributes": {"url": "https://example.invalid/kl/github/webhook", "active": False},
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }


def page(body: str) -> bytes:
    return (
        "<!doctype html><meta charset='utf-8'><title>knowledge-layer · GitHub App</title>"
        "<body style='font-family:system-ui,sans-serif;max-width:640px;margin:48px auto;line-height:1.5'>"
        f"{body}</body>"
    ).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console readable
        pass

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            action = f"https://github.com/organizations/{ORG}/settings/apps/new?state={STATE}"
            perms = ", ".join(f"{k}: {v}" for k, v in PERMISSIONS.items())
            self._send(200, page(
                f"<h2>Create the knowledge-layer GitHub App on <code>{html.escape(ORG)}</code></h2>"
                f"<p>Permissions: <code>{html.escape(perms)}</code>. Webhooks are off for now. "
                "You need to be an owner of the org. GitHub will let you rename the App if the name is taken.</p>"
                f"<form action='{html.escape(action)}' method='post'>"
                f"<input type='hidden' name='manifest' value='{html.escape(json.dumps(manifest()))}'>"
                "<button style='font-size:16px;padding:10px 18px'>Create GitHub App</button></form>"
            ))
            return
        if url.path == "/callback":
            q = parse_qs(url.query)
            code, state = q.get("code", [""])[0], q.get("state", [""])[0]
            if not secrets.compare_digest(state, STATE) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", code):
                self._send(400, page("<h2>State or code mismatch.</h2><p>Start again from the setup page.</p>"))
                return
            r = httpx.post(f"https://api.github.com/app-manifests/{code}/conversions", timeout=30,
                           headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
            if r.status_code != 201:
                self._send(502, page(f"<h2>GitHub refused the exchange ({r.status_code}).</h2>"
                                     f"<pre>{html.escape(r.text[:500])}</pre>"))
                return
            app = r.json()
            write_credentials(app)
            install = f"{app['html_url']}/installations/new"
            self._send(200, page(
                f"<h2>Created <code>{html.escape(app['name'])}</code> (App ID {app['id']})</h2>"
                "<p>Credentials are saved in <code>.secrets/</code>. Last step: install it on your test repos.</p>"
                f"<p><a href='{html.escape(install)}' style='font-size:16px'>Install the App →</a></p>"
                "<p>Then run <code>make smoke</code>. The GitHub check should pass. You can close this tab.</p>"
            ))
            print(f"\nCreated GitHub App '{app['name']}' (id {app['id']}). Install it here:\n  {install}\n")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._send(404, page("<h2>Not found</h2>"))


def write_credentials(app: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    meta = {k: app.get(k) for k in ("id", "slug", "name", "client_id", "html_url")}
    meta["owner"] = (app.get("owner") or {}).get("login")
    files = {
        f"{PREFIX}.pem": app["pem"],
        f"{PREFIX}.json": json.dumps(meta, indent=2) + "\n",
        f"{PREFIX}-secrets.json": json.dumps(
            {"client_secret": app.get("client_secret"), "webhook_secret": app.get("webhook_secret")}, indent=2) + "\n",
    }
    for name, content in files.items():
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", newline="\n") as f:
            f.write(content)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # some bind mounts (Windows) ignore modes


def main() -> None:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", ORG):
        sys.exit("Set the org: make github-app ORG=<your-github-org>")
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"\nOpen http://localhost:{HOST_PORT} and click 'Create GitHub App' "
          f"(creates it on the '{ORG}' org). Ctrl+C to cancel.\n", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
