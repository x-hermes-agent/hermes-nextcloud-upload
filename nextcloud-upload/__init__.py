"""Upload files from a local folder to Nextcloud over WebDAV, without passing the bytes through the model.

The ``nextcloud_upload`` tool streams a file that already exists on the Hermes host (for example a
sandbox outbox mounted into Hermes) straight to Nextcloud. Images, videos and archives arrive
intact, and the model only sends two paths.

Configuration (environment):
    NEXTCLOUD_HOST            Nextcloud base URL, e.g. https://cloud.example.com
    NEXTCLOUD_USERNAME        account to upload into
    NEXTCLOUD_APP_PASSWORD    app password for that account (NEXTCLOUD_PASSWORD also accepted)
    NEXTCLOUD_UPLOAD_ROOTS    folders files may be uploaded from (os.pathsep-separated)
    NEXTCLOUD_UPLOAD_MAX_MB   size cap per file (default 512)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

USER_AGENT = f"hermes-nextcloud-upload/{__version__}"
DEFAULT_MAX_MB = 512
TIMEOUT = 300

SCHEMA = {
    "name": "nextcloud_upload",
    "description": (
        "Upload a file to Nextcloud. The file must already exist in an allowed upload folder "
        "(see `source`); its bytes go straight to Nextcloud, so use this for images, videos and "
        "other binary files instead of writing them through another tool. Existing files are never "
        "overwritten unless overwrite=true. Missing destination folders are created."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Absolute path of the file in an allowed upload folder."},
            "destination": {"type": "string", "description": (
                "Nextcloud path, e.g. 'Projects/Posters/poster-1.png'. End with '/' to keep the source file name.")},
            "overwrite": {"type": "boolean", "description": "Replace an existing file. Only when the user asked for it."},
        },
        "required": ["source", "destination"],
    },
}


class UploadError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _settings() -> dict[str, Any]:
    roots = [r for r in os.environ.get("NEXTCLOUD_UPLOAD_ROOTS", "").split(os.pathsep) if r.strip()]
    return {
        "host": os.environ.get("NEXTCLOUD_HOST", "").rstrip("/"),
        "user": os.environ.get("NEXTCLOUD_USERNAME", ""),
        "password": os.environ.get("NEXTCLOUD_APP_PASSWORD") or os.environ.get("NEXTCLOUD_PASSWORD", ""),
        "roots": [Path(r).resolve() for r in roots],
        "max_bytes": int(float(os.environ.get("NEXTCLOUD_UPLOAD_MAX_MB") or DEFAULT_MAX_MB) * 1024 * 1024),
    }


def _available() -> bool:
    s = _settings()
    return bool(s["host"] and s["user"] and s["password"] and s["roots"])


def _source_file(source: str, roots: list[Path]) -> Path:
    """Resolved source path; symlinks and '..' cannot leave the allowed roots."""
    path = Path(source).expanduser().resolve()
    if not any(path == root or root in path.parents for root in roots):
        raise UploadError(f"source must be inside {', '.join(str(r) for r in roots)}")
    if not path.is_file():
        raise UploadError(f"source file not found: {path}")
    return path


def _remote_parts(destination: str, filename: str) -> list[str]:
    dest = destination.strip().replace("\\", "/")
    parts = [p for p in dest.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UploadError("destination must not contain '..'")
    if not parts or dest.endswith("/"):
        parts.append(filename)
    return parts


class _Client:
    def __init__(self, s: dict[str, Any]):
        self.base = f"{s['host']}/remote.php/dav/files/{urllib.parse.quote(s['user'])}"
        token = base64.b64encode(f"{s['user']}:{s['password']}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "User-Agent": USER_AGENT}

    def url(self, parts: list[str]) -> str:
        return self.base + "/" + "/".join(urllib.parse.quote(p) for p in parts)

    def request(self, method: str, parts: list[str], *, data: Any = None,
                headers: Optional[dict[str, str]] = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.url(parts), method=method, data=data,
                                     headers={**self.headers, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UploadError(f"Nextcloud unreachable: {exc}") from exc

    def ensure_folders(self, parts: list[str]) -> None:
        for i in range(1, len(parts)):
            status, _ = self.request("MKCOL", parts[:i])
            if status not in (201, 405):  # 405: already exists
                raise UploadError(f"could not create folder /{'/'.join(parts[:i])} (HTTP {status})", status)

    def size(self, parts: list[str]) -> Optional[int]:
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                '<d:getcontentlength/></d:prop></d:propfind>').encode()
        status, raw = self.request("PROPFIND", parts, data=body,
                                   headers={"Depth": "0", "Content-Type": "application/xml"})
        if status != 207:
            return None
        match = re.search(rb"<d:getcontentlength>(\d+)</d:getcontentlength>", raw)
        return int(match.group(1)) if match else None


def nextcloud_upload(args: dict[str, Any], **kwargs) -> str:
    try:
        return json.dumps(_upload(args or {}), ensure_ascii=False)
    except UploadError as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def _upload(args: dict[str, Any]) -> dict[str, Any]:
    s = _settings()
    if not _available():
        raise UploadError("nextcloud_upload is not configured (NEXTCLOUD_* / NEXTCLOUD_UPLOAD_ROOTS)")
    source = _source_file(str(args.get("source") or ""), s["roots"])
    size = source.stat().st_size
    if size > s["max_bytes"]:
        raise UploadError(f"file is {size} bytes; limit is {s['max_bytes']}")
    parts = _remote_parts(str(args.get("destination") or ""), source.name)
    client = _Client(s)
    client.ensure_folders(parts)

    headers = {"Content-Type": "application/octet-stream", "Content-Length": str(size)}
    if not args.get("overwrite"):
        headers["If-None-Match"] = "*"  # Nextcloud refuses (412) when the file already exists
    with source.open("rb") as fh:
        status, _ = client.request("PUT", parts, data=fh, headers=headers)
    remote = "/" + "/".join(parts)
    if status == 412:
        return {"success": False, "error": f"{remote} already exists; choose another name, or set "
                "overwrite=true only if the user asked to replace it"}
    if status not in (200, 201, 204):
        raise UploadError(f"upload of {remote} failed (HTTP {status})", status)

    stored = client.size(parts)
    if stored != size:
        raise UploadError(f"{remote} was uploaded but Nextcloud reports {stored} bytes instead of {size}")
    folder = "/" + "/".join(parts[:-1])
    return {"success": True, "path": remote, "bytes": size, "verified": True,
            "folder_url": f"{s['host']}/apps/files/?dir={urllib.parse.quote(folder)}"}


def register(ctx) -> None:
    ctx.register_tool(
        name="nextcloud_upload",
        toolset="nextcloud_upload",
        schema=SCHEMA,
        handler=nextcloud_upload,
        check_fn=_available,
        description="Upload a local file to Nextcloud over WebDAV",
        emoji="☁️",
    )
