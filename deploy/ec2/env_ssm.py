"""Move CeDesk's settings to and from SSM Parameter Store.

    uv run python deploy/ec2/env_ssm.py push --prefix /cevanos/prod/cedesk/ --env-file prod.env
    uv run python deploy/ec2/env_ssm.py check --prefix /cevanos/prod/cedesk/ --env-file prod.env
    uv run python deploy/ec2/env_ssm.py pull --prefix /cevanos/prod/cedesk/ --out .env

One SecureString parameter per variable, named <prefix><KEY>. `pull` is what the deploy
runs: it writes the file atomically, mode 600, and never prints a value. `push` is the
one-time load from a filled-in copy of deploy/ec2/env.example, run from a machine that
holds admin credentials (the file never has to reach the server); `check` compares the
two, printing names only.

`pull` reads with the box's instance role. `push` and `check` use whatever AWS
credentials the shell has; nothing here takes a key as an argument.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import boto3

KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Standard-tier parameters are free and hold 4 KB; a longer value needs Advanced.
STANDARD_LIMIT = 4096


def _client(region: str | None):  # type: ignore[no-untyped-def]
    return boto3.client("ssm", region_name=region or os.environ.get("AWS_REGION") or "us-east-1")


def _prefix(raw: str) -> str:
    if not raw.startswith("/"):
        sys.exit("--prefix must start with /")
    return raw if raw.endswith("/") else raw + "/"


def _fetch(prefix: str, region: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    pages = (
        _client(region)
        .get_paginator("get_parameters_by_path")
        .paginate(Path=prefix, Recursive=False, WithDecryption=True)
    )
    for page in pages:
        for p in page["Parameters"]:
            values[p["Name"][len(prefix) :]] = p["Value"]
    return values


def _one_line(key: str, value: str) -> str:
    """The value as it can sit between single quotes in an env_file.

    Single quotes are literal to docker compose's env_file: no ${...}
    interpolation, no escapes, so a value can hold neither a quote nor a newline.
    A JSON document written over several lines (CEDESK_PUSH_FCM_SERVICE_ACCOUNT) is the same
    document on one, which is all its readers see after json.loads.
    """
    if "'" not in value and "\n" not in value:
        return value
    try:
        compact = json.dumps(json.loads(value), separators=(",", ":"), ensure_ascii=False)
    except json.JSONDecodeError:
        compact = value
    if "'" in compact or "\n" in compact:
        sys.exit(f"{key}'s value holds a quote or a newline; store it as a file instead")
    return compact


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        return bool(json.loads(a) == json.loads(b))
    except json.JSONDecodeError:
        return False


def pull(prefix: str, out: Path, region: str | None) -> None:
    values = _fetch(prefix, region)
    if not values:
        sys.exit(f"no parameters under {prefix}; refusing to write an empty {out}")

    lines = []
    for key in sorted(values):
        if not KEY.match(key):
            sys.exit(f"parameter {prefix}{key} is not a valid variable name")
        lines.append(f"{key}='{_one_line(key, values[key])}'")

    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".env.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "# Written by deploy/ec2/env_ssm.py from "
                + prefix
                + ". Edit the parameters, not this file.\n"
            )
            f.write("\n".join(lines) + "\n")
        Path(tmp).chmod(0o600)
        Path(tmp).replace(out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    print(f"wrote {len(lines)} variables from {prefix} to {out}")


def push(prefix: str, env_file: Path, region: str | None, overwrite: bool) -> None:
    from dotenv import dotenv_values

    values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    ssm = _client(region)
    for key, value in values.items():
        if not KEY.match(key):
            sys.exit(f"{key} is not a valid variable name")
        if value == "":
            # SSM refuses empty values; an unset variable reads the same to the app.
            print(f"skipped {key}: empty")
            continue
        ssm.put_parameter(
            Name=prefix + key,
            Value=value,
            Type="SecureString",
            Overwrite=overwrite,
            Tier="Advanced" if len(value.encode()) > STANDARD_LIMIT else "Standard",
        )
        print(f"stored {prefix}{key}")


def check(prefix: str, env_file: Path, region: str | None) -> None:
    """Exit 0 when Parameter Store reads the same as `env_file`. Names only, never values."""
    from dotenv import dotenv_values

    local = {k: v for k, v in dotenv_values(env_file).items() if v}
    stored = _fetch(prefix, region)
    for key, value in stored.items():
        _one_line(key, value)  # what `pull` would refuse, refused here first
    missing = sorted(local.keys() - stored.keys())
    extra = sorted(stored.keys() - local.keys())
    differ = sorted(k for k in local.keys() & stored.keys() if not _same(local[k], stored[k]))
    for label, names in (
        ("in .env, not stored", missing),
        ("stored, not in .env", extra),
        ("different", differ),
    ):
        if names:
            print(f"{label}: {', '.join(names)}")
    if missing or extra or differ:
        sys.exit(1)
    print(f"match: {len(stored)} variables under {prefix} read the same as {env_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_pull = sub.add_parser("pull")
    p_pull.add_argument("--prefix", required=True)
    p_pull.add_argument("--out", type=Path, default=Path(".env"))
    p_pull.add_argument("--region")
    p_push = sub.add_parser("push")
    p_push.add_argument("--prefix", required=True)
    p_push.add_argument("--env-file", type=Path, required=True)
    p_push.add_argument("--region")
    p_push.add_argument("--overwrite", action="store_true")
    p_check = sub.add_parser("check")
    p_check.add_argument("--prefix", required=True)
    p_check.add_argument("--env-file", type=Path, default=Path(".env"))
    p_check.add_argument("--region")
    args = parser.parse_args()

    if args.command == "pull":
        pull(_prefix(args.prefix), args.out, args.region)
    elif args.command == "check":
        check(_prefix(args.prefix), args.env_file, args.region)
    else:
        push(_prefix(args.prefix), args.env_file, args.region, args.overwrite)


if __name__ == "__main__":
    main()
