#!/usr/bin/env python3
"""Deploy the lean BTC collector/shadow code bundle to the always-on laptop.

This intentionally excludes local evidence stores, logs, backtest outputs, and
bulk data by default. Credentials are copied only with --copy-credentials.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = {
    ".git",
    ".benchmarks",
    ".codex_work",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "backtest_outputs",
    "data",
    "gpt_pro_packets",
    "logs",
    "venv",
}
EXCLUDED_PATTERNS = {
    "*.duckdb",
    "*.db",
    "*.jsonl",
    "*.log",
    "*.parquet",
    "*.pem",
    "*.pyc",
    "*.pyo",
    "*.env",
    "*.ipynb",
    "*.pdf",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy BTC collector code to remote Windows laptop over SSH/SFTP.")
    p.add_argument("--host", default=os.environ.get("CLAW_HOST", "100.92.9.80"))
    p.add_argument("--user", default=os.environ.get("CLAW_USER", "ClawService"))
    p.add_argument("--password", default=os.environ.get("CLAW_PASS", ""))
    p.add_argument("--remote-root", default=r"C:\Users\ClawService\Kalshi-Trading-Bot")
    p.add_argument("--copy-credentials", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def should_exclude(path: Path) -> bool:
    rel = path.relative_to(PROJECT_ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return True
    name = path.name
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_PATTERNS)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(PROJECT_ROOT):
        root_path = Path(root)
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS and not should_exclude(root_path / name)]
        for name in names:
            path = root_path / name
            if should_exclude(path):
                continue
            files.append(path)
    return sorted(files)


def remote_join(root: str, rel: Path) -> str:
    return root.rstrip("\\/") + "\\" + "\\".join(rel.parts)


def ensure_remote_dir(sftp, path: str) -> None:
    parts = path.replace("/", "\\").split("\\")
    current = parts[0]
    if current.endswith(":"):
        current += "\\"
        parts = parts[1:]
    else:
        parts = parts[1:]
    for part in parts:
        if not part:
            continue
        current = current.rstrip("\\") + "\\" + part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def load_credentials_text(remote_root: str) -> tuple[str, Path | None]:
    creds_path = PROJECT_ROOT / "credentials.env"
    if not creds_path.exists():
        raise SystemExit("credentials.env is missing locally")
    private_key_path: Path | None = None
    lines: list[str] = []
    for raw in creds_path.read_text(encoding="utf-8").splitlines():
        if raw.strip().startswith("PRIVATE_KEY_PATH="):
            _, _, value = raw.partition("=")
            private_key_path = Path(value.strip())
            remote_key = remote_join(remote_root, Path(private_key_path.name if private_key_path.name else "kalshi_private_key.pem"))
            lines.append(f"PRIVATE_KEY_PATH={remote_key}")
        else:
            lines.append(raw)
    return "\n".join(lines) + "\n", private_key_path


def main() -> int:
    args = parse_args()
    files = iter_files()
    if args.copy_credentials:
        files = [path for path in files if path.name not in {"credentials.env"}]
    print(f"deploy_files={len(files)} remote_root={args.remote_root} copy_credentials={args.copy_credentials} dry_run={args.dry_run}")
    if args.dry_run:
        for path in files[:200]:
            print(path.relative_to(PROJECT_ROOT))
        if len(files) > 200:
            print(f"... {len(files) - 200} more")
        return 0
    if not args.password:
        raise SystemExit("Set CLAW_PASS or pass --password. The password is not logged.")

    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=args.host,
        username=args.user,
        password=args.password,
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
        look_for_keys=False,
        allow_agent=False,
    )
    try:
        sftp = client.open_sftp()
        ensure_remote_dir(sftp, args.remote_root)
        made_dirs: set[str] = set()
        for path in files:
            rel = path.relative_to(PROJECT_ROOT)
            remote_path = remote_join(args.remote_root, rel)
            remote_dir = str(Path(remote_path).parent).replace("/", "\\")
            if remote_dir not in made_dirs:
                ensure_remote_dir(sftp, remote_dir)
                made_dirs.add(remote_dir)
            sftp.put(str(path), remote_path)

        if args.copy_credentials:
            creds_text, private_key_path = load_credentials_text(args.remote_root)
            remote_creds = remote_join(args.remote_root, Path("credentials.env"))
            with sftp.open(remote_creds, "w") as f:
                f.write(creds_text)
            if private_key_path is None or not private_key_path.exists():
                raise SystemExit("credentials.env references a private key path that does not exist locally")
            sftp.put(str(private_key_path), remote_join(args.remote_root, Path(private_key_path.name)))
        sftp.close()
    finally:
        client.close()
    print("deploy_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
