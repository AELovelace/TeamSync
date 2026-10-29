"""Push new files from a local Box Drive folder to a TeamSync server.

Standard library only, so it runs on the workstation with a plain Python
install. Walks the folder, hashes every file, asks the server which hashes it
already has, and uploads the rest one at a time.

    python tools/push_from_box.py --root "C:\\Users\\Edith\\Box\\Singh JE Reports" ^
        --server https://teamsync.eternalhotels.com --token tsk_... [--insecure]

Environment variables TEAMSYNC_PUSH_ROOT, TEAMSYNC_PUSH_SERVER, TEAMSYNC_PUSH_TOKEN,
TEAMSYNC_PUSH_CA_BUNDLE and TEAMSYNC_PUSH_INSECURE=1 are used when the flags are
omitted. Exit code 0 on success, 1 when any upload failed, 2 on bad arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import uuid
from pathlib import Path
from urllib import error, request

SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
SKIP_SUFFIXES = (".boxnote", ".tmp", ".crdownload", ".part")


def should_skip(path: Path) -> bool:
    name = path.name.lower()
    return name in SKIP_NAMES or name.startswith(("~$", ".")) or name.endswith(SKIP_SUFFIXES)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_context(insecure: bool, ca_bundle: str | None) -> ssl.SSLContext:
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=ca_bundle) if ca_bundle else ssl.create_default_context()


class Client:
    def __init__(self, server: str, token: str, ctx: ssl.SSLContext, timeout: float = 60.0) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.ctx = ctx
        self.timeout = timeout

    def _call(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> dict:
        req = request.Request(self.server + path, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if content_type:
            req.add_header("Content-Type", content_type)
        with request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def known(self, hashes: list[str]) -> set[str]:
        """Ask which digests the server already holds. POST with a JSON body, so the
        request line stays short whatever the folder size (nginx caps it at 8 KB, which
        about 120 comma-separated digests would blow through)."""
        known: set[str] = set()
        for i in range(0, len(hashes), 1000):
            chunk = hashes[i : i + 1000]
            try:
                payload = self._call(
                    "POST", "/api/files/known", json.dumps({"hashes": chunk}).encode(), "application/json"
                )
            except error.HTTPError as exc:
                if exc.code not in (404, 405):
                    raise
                # Older server without the POST form: short GETs that fit the proxy's buffer.
                payload = {"known": []}
                for j in range(0, len(chunk), 100):
                    part = self._call("GET", "/api/files/known?hashes=" + ",".join(chunk[j : j + 100]))
                    payload["known"].extend(part.get("known", []))
            known.update(payload.get("known", []))
        return known

    def upload(self, path: Path, source_path: str, batch_folder: str | None) -> dict:
        boundary = "----TeamSync" + uuid.uuid4().hex
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )

        field("source_path", source_path)
        if batch_folder:
            field("batch_folder", batch_folder)
        filename = path.name.replace('"', "_")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
        )
        parts.append(path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)
        return self._call("POST", "/api/upload", body, f"multipart/form-data; boundary={boundary}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Push new Box files to TeamSync")
    p.add_argument("--root", default=os.environ.get("TEAMSYNC_PUSH_ROOT"))
    p.add_argument("--server", default=os.environ.get("TEAMSYNC_PUSH_SERVER"))
    p.add_argument("--token", default=os.environ.get("TEAMSYNC_PUSH_TOKEN"))
    p.add_argument("--ca-bundle", default=os.environ.get("TEAMSYNC_PUSH_CA_BUNDLE"))
    p.add_argument(
        "--insecure",
        action="store_true",
        default=os.environ.get("TEAMSYNC_PUSH_INSECURE") == "1",
        help="skip TLS verification (self-signed cert)",
    )
    p.add_argument("--dry-run", action="store_true", help="list what would be uploaded")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)

    if not (args.root and args.server and args.token):
        p.error("--root, --server and --token are required (or the TEAMSYNC_PUSH_* variables)")
    root = Path(args.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    files = sorted(f for f in root.rglob("*") if f.is_file() and not should_skip(f))
    digests = {f: sha256_of(f) for f in files}
    client = Client(args.server, args.token, make_context(args.insecure, args.ca_bundle))

    try:
        known = client.known(list(dict.fromkeys(digests.values())))
    except error.HTTPError as exc:
        print(
            f"server refused hash check: HTTP {exc.code} {exc.read().decode(errors='replace')[:200]}",
            file=sys.stderr,
        )
        return 1
    except (error.URLError, OSError) as exc:
        print(f"cannot reach {args.server}: {exc}", file=sys.stderr)
        return 1

    pending = [f for f in files if digests[f] not in known]
    if not args.quiet:
        print(
            f"{len(files)} files under {root}; {len(files) - len(pending)} already on server; {len(pending)} to upload"
        )
    if args.dry_run:
        for f in pending:
            print(f"  would upload {f.relative_to(root)}")
        return 0

    failures = 0
    for f in pending:
        rel = f.relative_to(root)
        try:
            result = client.upload(f, rel.as_posix(), rel.parent.name or None)
        except error.HTTPError as exc:
            failures += 1
            print(
                f"  FAILED {rel}: HTTP {exc.code} {exc.read().decode(errors='replace')[:200]}",
                file=sys.stderr,
            )
            continue
        except (error.URLError, OSError) as exc:
            failures += 1
            print(f"  FAILED {rel}: {exc}", file=sys.stderr)
            continue
        outcome = (result.get("files") or [{}])[0]
        if not args.quiet:
            extra = outcome.get("error") or ", ".join(outcome.get("flags") or [])
            print(f"  {outcome.get('status', '?'):9} {outcome.get('parse_status') or '-':19} {rel}  {extra}")
    if not args.quiet:
        print(f"done: {len(pending) - failures} uploaded, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
