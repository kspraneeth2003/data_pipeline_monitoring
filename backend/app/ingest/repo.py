"""Fetches a repository to local disk so its DDL can be read and its history queried.

Two constraints shape everything here.

First, a repository URL is user input that reaches `git`. Every call passes an
argument list (never a shell string), the scheme is allowlisted, and anything
that could be read as an option is rejected outright - `--upload-pack=...` in a
URL position is remote code execution, not a URL.

Second, an access token is a secret that git will happily persist. Cloning with
credentials in the URL leaves them in `.git/config` forever, where the next
`git remote -v`, error message or log line spills them. So the token is passed
for exactly one command and the remote is rewritten to the clean URL before
anything else touches the checkout.
"""

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote, urlparse, urlunparse

from app.config import settings

ALLOWED_SCHEMES = {"https", "http"}
CLONE_TIMEOUT_SECONDS = 180

# Enough history for RCA's pickaxe search (`git log -S<column>`) to find the
# commit that introduced a column. A shallow clone would make that search
# silently return nothing, which reads as "no one changed this" - the most
# misleading answer RCA can give.
CLONE_ARGS = ["--quiet"]


class RepoCheckout(TypedDict):
    url: str
    path: str
    ref: str
    commit: str
    subject: str


class RepoError(RuntimeError):
    """A repository could not be fetched. The message is shown to the user, so
    it must never contain the access token."""


def normalize_repo_url(raw: str) -> str:
    """Validates a repository URL and returns it without credentials.

    Rejects anything that is not a plain http(s) URL. `file://`, `ssh://` and
    bare paths are refused rather than normalized: this runs inside a request
    handler, and a server that clones local paths on request will read
    directories it was never meant to see.
    """
    url = (raw or "").strip()
    if not url:
        raise RepoError("Enter a repository URL.")
    if url.startswith("-"):
        raise RepoError("That does not look like a repository URL.")

    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise RepoError(
            f"Only {' and '.join(sorted(ALLOWED_SCHEMES))} repository URLs are supported "
            "(got "
            f"{parsed.scheme or 'no scheme'})."
        )
    if not parsed.netloc:
        raise RepoError("That URL is missing a host.")

    # Drop any user:password already in the URL - the token field is the only
    # supported way to authenticate, and echoing an inline password back into
    # error messages or the stored project row is how secrets leak.
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path, "", parsed.query, ""))


def _authenticated_url(url: str, token: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse(
        (parsed.scheme, f"{quote(token, safe='')}@{host}", parsed.path, "", parsed.query, "")
    )


def _cache_dir() -> Path:
    path = Path(settings.repo_cache_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def checkout_path(url: str) -> Path:
    """One stable directory per repository URL, so re-ingesting fetches rather
    than re-clones."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    name = re.sub(r"[^a-zA-Z0-9]+", "-", url.rsplit("/", 1)[-1].removesuffix(".git")).strip("-")
    return _cache_dir() / f"{name or 'repo'}-{digest}"


def _run_git(args: list[str], cwd: Path | None = None, redact: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise RepoError("git is not installed on the server.") from error
    except subprocess.TimeoutExpired as error:
        raise RepoError(f"git timed out after {CLONE_TIMEOUT_SECONDS}s.") from error

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip() or f"git exited {result.returncode}"
        if redact:
            message = message.replace(redact, "***")
        raise RepoError(message.splitlines()[-1][:400])
    return result.stdout


def fetch_repo(url: str, token: str | None = None, ref: str | None = None) -> RepoCheckout:
    """Clones or updates `url` and returns where it landed and what it is pinned to."""
    clean_url = normalize_repo_url(url)
    remote = _authenticated_url(clean_url, token) if token else clean_url
    destination = checkout_path(clean_url)

    if (destination / ".git").is_dir():
        try:
            _run_git(["remote", "set-url", "origin", remote], cwd=destination, redact=token)
            _run_git(["fetch", "--prune", "origin"], cwd=destination, redact=token)
        except RepoError:
            # A cache directory in any broken state is disposable - the remote
            # is the source of truth, so fall back to a clean clone.
            shutil.rmtree(destination, ignore_errors=True)
        finally:
            if (destination / ".git").is_dir():
                _run_git(["remote", "set-url", "origin", clean_url], cwd=destination)

    if not (destination / ".git").is_dir():
        shutil.rmtree(destination, ignore_errors=True)
        try:
            _run_git(
                ["clone", *CLONE_ARGS, "--", remote, str(destination)],
                redact=token,
            )
        finally:
            if (destination / ".git").is_dir():
                # Before anything else can read it, strip the token back out of
                # .git/config so it cannot resurface in a log or an error.
                _run_git(["remote", "set-url", "origin", clean_url], cwd=destination)

    target = ref.strip() if ref and ref.strip() else _default_branch(destination)
    try:
        _run_git(["checkout", "--force", target], cwd=destination)
        _run_git(["reset", "--hard", f"origin/{target}"], cwd=destination)
    except RepoError:
        # A tag or a bare commit has no origin/<ref> to reset to; the checkout
        # above is already correct in that case.
        pass

    commit = _run_git(["rev-parse", "HEAD"], cwd=destination).strip()
    subject = _run_git(["log", "-1", "--format=%s"], cwd=destination).strip()

    return {
        "url": clean_url,
        "path": str(destination),
        "ref": target,
        "commit": commit,
        "subject": subject,
    }


def _default_branch(repo: Path) -> str:
    try:
        head = _run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo).strip()
        return head.split("/", 1)[1] if "/" in head else head
    except RepoError:
        return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip() or "main"
