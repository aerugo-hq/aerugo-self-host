"""Principal ids are the currency of authorization. They go into RLS context as a
comma-joined list, so anything that could split, widen, or wildcard a principal is rejected.

  user:<id>   group:<id>   org:<slug>   come from the IdP token (prefixes are added by code)
  ext:<source>.<id>                     a source identity, e.g. ext:github.octocat. It reaches a
                                        caller only through a principal_aliases link.
  svc:<name>                            an internal service (e.g. svc:github-webhook). It holds no
                                        grants on content; tokens can never produce it.

Teams a transaction may touch (db.scoped's teams, after the API checked them in OpenFGA) go into RLS context the same
way, as slugs (is_team)."""
import re

_PRINCIPAL = re.compile(r"^(user|group|org|ext|svc):[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")


def is_valid(principal: str) -> bool:
    return bool(_PRINCIPAL.fullmatch(principal))


_TEAM = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def is_team(slug: str) -> bool:
    return bool(_TEAM.fullmatch(slug))
