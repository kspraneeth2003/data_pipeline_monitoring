"""GitHub App integration - read access to the repositories the user picked, and no others.

Why a GitHub App rather than "Sign in with GitHub": signing in grants the app
everything the user can see, in one go, with no per-repository question. A
GitHub App is installed *onto selected repositories*, so GitHub itself asks
"grant read access to these?" and enforces the answer. The app cannot reach a
repository it was not given, which is a guarantee rather than a promise.

Two consequences shape this module.

There is no stored credential. An installation token is minted on demand from
the app's private key, lives an hour, and is scoped to one installation. So
nothing long-lived sits in the database waiting to leak, and there is no token
for the user to re-enter later - which is what the paste-a-token flow required.

The token never leaves the server. The browser sends an installation id; the
backend turns that into a token at the moment it clones. Handing a token to the
frontend would put a credential with read access to real source code into
browser history, logs and devtools, for no gain.

Installation state is not stored either. GitHub already knows who installed the
app and on what, so that is queried live. It cannot go stale, and uninstalling
from GitHub's own settings takes effect here immediately.
"""

import logging
import time
from pathlib import Path
from typing import TypedDict

import httpx
import jwt

from app.config import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
REQUEST_TIMEOUT_SECONDS = 20

# GitHub rejects an app JWT with more than 10 minutes of life. 9 leaves room
# for clock skew without being refused.
JWT_LIFETIME_SECONDS = 9 * 60
JWT_BACKDATE_SECONDS = 60

# GitHub pages at 30 by default; 100 is the maximum and keeps a large account
# to a couple of round trips.
PAGE_SIZE = 100
MAX_PAGES = 10


class GitHubRepository(TypedDict):
    full_name: str
    clone_url: str
    private: bool
    default_branch: str
    description: str | None
    pushed_at: str | None
    installation_id: int
    account: str


class GitHubError(RuntimeError):
    """Shown to the user, so it must never contain a token or the private key."""


def is_configured() -> bool:
    return bool(settings.github_app_id and _private_key())


def _private_key() -> str | None:
    if settings.github_app_private_key.strip():
        return settings.github_app_private_key
    if settings.github_app_private_key_path:
        path = Path(settings.github_app_private_key_path).expanduser()
        if path.is_file():
            return path.read_text()
        logger.warning("GITHUB_APP_PRIVATE_KEY_PATH is set but %s does not exist", path)
    return None


def install_url() -> str | None:
    """Where to send the user to choose repositories and grant read access.

    This is GitHub's own screen, deliberately: it is the one place the consent
    is real, and a home-made imitation of it would be both less trustworthy and
    less accurate about what is being granted.
    """
    if not settings.github_app_slug:
        return None
    return f"https://github.com/apps/{settings.github_app_slug}/installations/new"


def _app_jwt() -> str:
    key = _private_key()
    if not settings.github_app_id or not key:
        raise GitHubError("GitHub is not configured on this server.")
    now = int(time.time())
    try:
        return jwt.encode(
            {
                "iat": now - JWT_BACKDATE_SECONDS,
                "exp": now + JWT_LIFETIME_SECONDS,
                "iss": settings.github_app_id,
            },
            key,
            algorithm="RS256",
        )
    except Exception as error:  # noqa: BLE001
        raise GitHubError(
            "The GitHub app private key could not be read. It should be the full "
            "PEM file, beginning with -----BEGIN RSA PRIVATE KEY-----."
        ) from error


def _get(path: str, token: str, params: dict | None = None) -> dict:
    try:
        response = httpx.get(
            f"{API_ROOT}{path}",
            headers={**API_HEADERS, "Authorization": f"Bearer {token}"},
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        raise GitHubError(f"Could not reach GitHub: {error}") from error
    return _parse(response)


def _post(path: str, token: str) -> dict:
    try:
        response = httpx.post(
            f"{API_ROOT}{path}",
            headers={**API_HEADERS, "Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        raise GitHubError(f"Could not reach GitHub: {error}") from error
    return _parse(response)


def _parse(response: httpx.Response) -> dict:
    if response.status_code == 401:
        raise GitHubError(
            "GitHub rejected the app credentials. Check GITHUB_APP_ID matches the "
            "private key, and that the server clock is correct."
        )
    if response.status_code == 404:
        raise GitHubError("GitHub returned 404 - the app may have been uninstalled.")
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str(response.json().get("message", ""))
        except Exception:  # noqa: BLE001
            pass
        raise GitHubError(f"GitHub returned {response.status_code}. {detail}".strip())
    payload = response.json()
    return payload if isinstance(payload, dict) else {"items": payload}


def list_installations() -> list[dict]:
    """Every account that has installed this app. Queried live, never cached."""
    payload = _get("/app/installations", _app_jwt(), {"per_page": PAGE_SIZE})
    installations = payload.get("items", payload if isinstance(payload, list) else [])
    return [
        {
            "id": item["id"],
            "account": (item.get("account") or {}).get("login", "unknown"),
            "repository_selection": item.get("repository_selection", "selected"),
        }
        for item in installations
    ]


def installation_token(installation_id: int) -> str:
    """A token scoped to one installation, valid for an hour.

    Minted at the moment of use and then discarded. Callers must not log it,
    return it, or persist it.
    """
    payload = _post(f"/app/installations/{installation_id}/access_tokens", _app_jwt())
    token = payload.get("token")
    if not token:
        raise GitHubError("GitHub did not return an installation token.")
    return token


def list_repositories() -> list[GitHubRepository]:
    """Every repository this app has been granted, across all installations.

    An installation that fails is skipped rather than failing the whole list -
    one revoked install should not hide every other repository the user has.
    """
    repositories: list[GitHubRepository] = []
    for installation in list_installations():
        try:
            token = installation_token(installation["id"])
        except GitHubError as error:
            logger.warning("Skipping installation %s: %s", installation["id"], error)
            continue

        for page in range(1, MAX_PAGES + 1):
            payload = _get(
                "/installation/repositories",
                token,
                {"per_page": PAGE_SIZE, "page": page},
            )
            items = payload.get("repositories", [])
            for repo in items:
                repositories.append(
                    {
                        "full_name": repo["full_name"],
                        "clone_url": repo["clone_url"],
                        "private": bool(repo.get("private")),
                        "default_branch": repo.get("default_branch") or "main",
                        "description": repo.get("description"),
                        "pushed_at": repo.get("pushed_at"),
                        "installation_id": installation["id"],
                        "account": installation["account"],
                    }
                )
            if len(items) < PAGE_SIZE:
                break

    # Most recently pushed first: the repository someone is here to connect is
    # far more likely to be one they are actively working on.
    repositories.sort(key=lambda r: r["pushed_at"] or "", reverse=True)
    return repositories


def clone_token_for(installation_id: int) -> str:
    """The token `repo.fetch_repo` should clone with. Server-side only."""
    return installation_token(installation_id)
