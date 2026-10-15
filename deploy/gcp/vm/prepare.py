"""Builds a deployment's Keycloak realm and seed tuples from the development ones, so the two never drift apart.
The deploy (deploy.sh) runs it on the VM at every release, before anything starts:

  - the realm (deploy/gcp/keycloak/realm-acme.json): every person removed (only service accounts stay), SSL required,
    no self-registration, no password grant on any client, kl-terraform's secret a generated one, and every address
    the web app signs in and out through on this deployment's domain;
  - the seed tuples (deploy/gcp/authz/seed-tuples.json): the org's teams and which groups are their members, and no
    demo person or document; the first org admin, by the username they'll sign in with.

Settings come from the environment: KL_DOMAIN, KL_FIRST_ADMIN, KL_TERRAFORM_CLIENT_SECRET."""
import json
import os
import re
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def realm(src: dict, domain: str, terraform_secret: str) -> dict:
    out = dict(src)
    out["users"] = [u for u in src.get("users", []) if u.get("serviceAccountClientId")]  # no person, only machines
    out["sslRequired"] = "external"
    out["registrationAllowed"] = False
    web = f"https://app.{domain}"
    clients = []
    for c in src.get("clients", []):
        c = dict(c)
        c["directAccessGrantsEnabled"] = False  # no client takes a password directly
        if c.get("clientId") == "kl-terraform":
            c["secret"] = terraform_secret
        if c.get("redirectUris"):
            c["redirectUris"] = [f"{web}/auth/callback"]
        if c.get("webOrigins"):
            c["webOrigins"] = [web]
        attrs = dict(c.get("attributes") or {})
        if "post.logout.redirect.uris" in attrs:
            attrs["post.logout.redirect.uris"] = f"{web}/"
        c["attributes"] = attrs
        clients.append(c)
    out["clients"] = clients
    return out


def tuples(src: dict, admin: str) -> dict:
    """The org's structure (its teams, and which groups are their members), no demo person or document, and the admin."""
    def structural(t):
        user, _, obj = t
        return not user.startswith("user:") and not obj.startswith(("document:", "container:"))
    kept = [t for t in src["tuples"] if structural(t)]
    return {"tuples": kept + [[f"user:{admin}", "member", "org:acme"], [f"user:{admin}", "admin", "org:acme"]]}


def main() -> None:
    domain = os.environ.get("KL_DOMAIN", "").strip().lower()
    admin = os.environ.get("KL_FIRST_ADMIN", "").strip().lower()
    secret = os.environ.get("KL_TERRAFORM_CLIENT_SECRET", "")
    if not DOMAIN.fullmatch(domain) or not USERNAME.fullmatch(admin) or len(secret) < 32:
        sys.exit("prepare: KL_DOMAIN, KL_FIRST_ADMIN (a username), and KL_TERRAFORM_CLIENT_SECRET (32+ characters) are needed")
    with open(os.path.join(ROOT, "deploy", "keycloak", "realm-acme.json")) as f:
        built = realm(json.load(f), domain, secret)
    os.makedirs(os.path.join(ROOT, "deploy", "gcp", "keycloak"), exist_ok=True)
    with open(os.path.join(ROOT, "deploy", "gcp", "keycloak", "realm-acme.json"), "w") as f:
        json.dump(built, f, indent=2)
    authz = os.path.join(ROOT, "deploy", "gcp", "authz")
    os.makedirs(authz, exist_ok=True)
    for name in os.listdir(os.path.join(ROOT, "authz")):  # the model, as it is
        if name != "seed-tuples.json" and os.path.isfile(os.path.join(ROOT, "authz", name)):
            shutil.copyfile(os.path.join(ROOT, "authz", name), os.path.join(authz, name))
    with open(os.path.join(ROOT, "authz", "seed-tuples.json")) as f:
        seeded = tuples(json.load(f), admin)
    with open(os.path.join(authz, "seed-tuples.json"), "w") as f:
        json.dump(seeded, f, indent=2)
    print(f"prepare: realm for {domain} ({len(built['users'])} service account(s), no people); "
          f"{len(seeded['tuples'])} seed tuples, {admin} the org admin")


if __name__ == "__main__":
    main()
