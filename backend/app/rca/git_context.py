import subprocess
from pathlib import Path
from typing import TypedDict

from app.rca.object_repo_map import EMPTY_REPO_CONTEXT, RepoContext

# Where the app's own pipeline lives. Used only for projects that predate repo
# ingestion, whose repo_path is null but whose DDL is this repo's snowflake/.
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
FIELD_SEP = "\x1f"


class GitCommit(TypedDict):
    hash: str
    author_name: str
    author_email: str
    date: str
    subject: str


class GitContext(TypedDict):
    object: str
    file_path: str | None
    recent_commits: list[GitCommit]
    targeted_commit: GitCommit | None


def _parse_log(stdout: str) -> list[GitCommit]:
    commits: list[GitCommit] = []
    for line in stdout.splitlines():
        if not line:
            continue
        h, author_name, author_email, date, subject = line.split(FIELD_SEP)
        commits.append(
            {"hash": h, "author_name": author_name, "author_email": author_email, "date": date, "subject": subject}
        )
    return commits


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10
    )
    return result.stdout


def gather_git_context(
    object_name: str,
    keyword: str | None = None,
    repo: RepoContext = EMPTY_REPO_CONTEXT,
) -> GitContext:
    """Gathers git history for the file mapped to `object_name`. `keyword`
    (e.g. a column name) narrows the search to the commit that most recently
    added/removed that token, via git's pickaxe search (`-S`)."""
    file_path = repo.resolve(object_name)
    if not file_path:
        return {"object": object_name, "file_path": None, "recent_commits": [], "targeted_commit": None}

    root = Path(repo.root) if repo.root else DEFAULT_REPO_ROOT
    if not (root / ".git").exists():
        return {"object": object_name, "file_path": file_path, "recent_commits": [], "targeted_commit": None}

    fmt = f"--format=%H{FIELD_SEP}%an{FIELD_SEP}%ae{FIELD_SEP}%ad{FIELD_SEP}%s"

    recent_commits: list[GitCommit] = []
    try:
        stdout = _run_git(["log", "-n", "5", fmt, "--date=iso", "--", file_path], root)
        recent_commits = _parse_log(stdout)
    except Exception:
        pass

    targeted_commit: GitCommit | None = None
    if keyword:
        try:
            stdout = _run_git(["log", "-n", "1", f"-S{keyword}", fmt, "--date=iso", "--", file_path], root)
            commits = _parse_log(stdout)
            targeted_commit = commits[0] if commits else None
        except Exception:
            pass

    return {
        "object": object_name,
        "file_path": file_path,
        "recent_commits": recent_commits,
        "targeted_commit": targeted_commit,
    }
