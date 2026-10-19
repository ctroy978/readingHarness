from __future__ import annotations

import io
import re
import tarfile
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from server.catalog import LESSON_NAME

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_LESSON_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000


def normalize_repository(value: str) -> str:
    value = value.strip().removesuffix(".git").rstrip("/")
    if value.startswith(("https://", "http://")):
        value = urlparse(value).path.strip("/")
    if not REPOSITORY.fullmatch(value):
        raise RuntimeError("GITHUB_REPO must be owner/repository or its GitHub URL")
    return value


def parse_lesson_files(archive: bytes, lesson_path: str = "") -> dict[str, bytes]:
    prefix = PurePosixPath(lesson_path.strip("/")) if lesson_path.strip("/") else None
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for index, member in enumerate(tar):
                if index >= MAX_ARCHIVE_MEMBERS:
                    raise RuntimeError("GitHub repository archive has too many files")
                if not member.isfile():
                    continue
                member_path = PurePosixPath(member.name)
                if len(member_path.parts) < 2:
                    continue
                relative = PurePosixPath(*member_path.parts[1:])
                if prefix is not None:
                    try:
                        relative = relative.relative_to(prefix)
                    except ValueError:
                        continue
                if len(relative.parts) != 1 or not LESSON_NAME.fullmatch(relative.name):
                    continue
                if relative.name in files:
                    raise RuntimeError(f"Duplicate lesson filename in GitHub archive: {relative.name}")
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                content = extracted.read(MAX_LESSON_BYTES + 1)
                if len(content) > MAX_LESSON_BYTES:
                    raise RuntimeError(f"Lesson file is too large: {relative.name}")
                files[relative.name] = content
    except tarfile.TarError as exc:
        raise RuntimeError("GitHub returned an invalid repository archive") from exc
    if not files:
        raise RuntimeError("No lesson JSON files found in the GitHub repository path")
    return files


def fetch_lesson_files(repo: str, token: str, ref: str, lesson_path: str) -> dict[str, bytes]:
    repo = normalize_repository(repo)
    url = f"https://api.github.com/repos/{repo}/tarball/{quote(ref, safe='')}"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "reading-harness-server",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
    except HTTPError as exc:
        raise RuntimeError(f"GitHub lesson download failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError("GitHub lesson download failed") from exc
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("GitHub repository archive is too large")
    return parse_lesson_files(archive, lesson_path)
