#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cryptography"]
# ///
"""agent-browser-with-cookies CLI — extract cookies for a host into an agent-browser state file.

    cookies.py list-profiles [--url U | --domain D] [--json]
    cookies.py extract (--url U | --domain D) [--profile P | --auto]
                       [--out FILE] [--include-expired] [--no-fallback]
                       [--engine auto|self|get-cookie]

`extract` decrypts the Chrome cookie store for the target host (one Touch ID tap;
the underlying Safe Storage read is silent after a first-run Always Allow), and
falls back to @mherod/get-cookie across all browsers when Chrome has nothing. It
writes a Playwright-style ``{"cookies":[...],"origins":[]}`` file (0600) and prints
its path as the last stdout line; a human summary goes to stderr. Load it with:

    agent-browser --session abwc --state "$(cookies.py extract --url <U> | tail -1)" open <U>

`list-profiles` only reads plaintext host_keys, so it never decrypts and never
prompts. macOS + Chrome ``v10`` cookies; authorized local use on your own machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from cookies_lib import crypto, domains, getcookie, keychain, profiles, serialize

SWIFT_SRC = Path(__file__).resolve().parent / "touchid_gate.swift"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cookies.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("list-profiles", help="list Chrome profiles + applicable cookie counts (no decrypt)")
    target = lp.add_mutually_exclusive_group()
    target.add_argument("--url", help="target URL (host is derived from it)")
    target.add_argument("--domain", help="target host/domain")
    lp.add_argument("--json", action="store_true", help="machine-readable output")

    ex = sub.add_parser("extract", help="extract cookies for a host → agent-browser --state JSON")
    tgt = ex.add_mutually_exclusive_group(required=True)
    tgt.add_argument("--url", help="target URL (host + scheme derived from it)")
    tgt.add_argument("--domain", help="target host/domain")
    prof = ex.add_mutually_exclusive_group()
    prof.add_argument("--profile", help='Chrome profile dir, e.g. "Profile 3"')
    prof.add_argument("--auto", action="store_true", help="auto-pick the profile with the most matching cookies")
    ex.add_argument("--out", help="write state JSON here instead of a private temp file")
    ex.add_argument("--include-expired", action="store_true", help="keep already-expired cookies")
    ex.add_argument("--no-fallback", action="store_true", help="do not fall back to @mherod/get-cookie")
    ex.add_argument("--engine", choices=("auto", "self", "get-cookie"), default="auto")
    return parser


# --- list-profiles -----------------------------------------------------------


def cmd_list_profiles(args: argparse.Namespace) -> int:
    info = profiles.profile_info()
    host = domains.normalize_host(args.url or args.domain) if (args.url or args.domain) else None
    rows = []
    for d in profiles.list_profile_dirs():
        row = {"profile": d, "email": info.get(d, {}).get("email", ""), "name": info.get(d, {}).get("name", "")}
        if host:
            row["applicable_cookies"] = profiles.count_applicable(d, host)
        rows.append(row)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            line = f"{r['profile']}\t{r['email'] or r['name'] or '(no account)'}"
            if host:
                line += f"\t{r['applicable_cookies']} cookies for {host}"
            print(line)
    return 0


# --- extract -----------------------------------------------------------------


def _select_profile(host: str) -> tuple[str | None, str | None]:
    """Auto-pick the Chrome profile with the most matching cookies. Returns (profile, error)."""
    scored = sorted(
        ((profiles.count_applicable(d, host), d) for d in profiles.list_profile_dirs()),
        reverse=True,
    )
    scored = [(c, d) for c, d in scored if c > 0]
    if not scored:
        return None, f"no Chrome profile has cookies for {host}"
    if len(scored) >= 2 and scored[1][0] >= 0.5 * scored[0][0]:
        info = profiles.profile_info()
        cands = "; ".join(f"{d} ({info.get(d, {}).get('email', '?')}: {c})" for c, d in scored)
        return None, f"AMBIGUOUS: multiple Chrome profiles match {host} — pass --profile. Candidates: {cands}"
    return scored[0][1], None


def _self_decrypt(host: str, profile: str, include_expired: bool) -> tuple[list[dict], dict]:
    """Decrypt applicable Chrome cookies for ``host``. Empty result ⇒ caller falls back."""
    rows = profiles.read_encrypted_rows(profile, host)
    if not rows:
        return [], {"engine": "self", "profile": profile, "found": 0, "note": "no applicable cookies"}

    keychain.touchid_gate(SWIFT_SRC, reason=f"unlock your Chrome cookies for {host}")
    key = crypto.derive_key(keychain.read_safe_storage_key())

    now = time.time()
    cookies: list[dict] = []
    skipped_v20 = failed = 0
    for r in rows:
        try:
            value = crypto.decrypt_value(r["encrypted_value"], key, r["host_key"])
        except crypto.DecryptError as exc:
            if "v20" in str(exc):
                skipped_v20 += 1
            else:
                failed += 1
            continue
        expires = serialize.chrome_micros_to_unix(r["expires_utc"])
        if not include_expired and expires != -1 and expires < now:
            continue
        cookies.append(
            serialize.build_cookie(
                name=r["name"],
                value=value,
                host_key=r["host_key"],
                path=r["path"],
                expires=expires,
                secure=r["is_secure"],
                http_only=r["is_httponly"],
                samesite=r["samesite"],
            )
        )
    summary = {
        "engine": "self",
        "profile": profile,
        "rows": len(rows),
        "found": len(cookies),
        "skipped_v20": skipped_v20,
        "decrypt_failed": failed,
    }
    return cookies, summary


def cmd_extract(args: argparse.Namespace) -> int:
    raw_target = args.url or args.domain
    host = domains.normalize_host(raw_target)
    scheme = domains.url_scheme(args.url) if args.url else "https"
    if not host:
        sys.exit("ERROR: could not parse a host from the target")

    cookies: list[dict] = []
    summary: dict = {}
    used: str | None = None

    if args.engine in ("auto", "self"):
        profile, err = (args.profile, None) if args.profile else _select_profile(host)
        if err:
            if err.startswith("AMBIGUOUS"):
                sys.exit(f"ERROR: {err}")
            print(f"self-decrypt: {err}", file=sys.stderr)
        if profile:
            try:
                cookies, summary = _self_decrypt(host, profile, args.include_expired)
                used = "self"
            except keychain.KeychainError as exc:
                print(f"self-decrypt: {exc}", file=sys.stderr)
                cookies = []

    do_fallback = args.engine == "get-cookie" or (args.engine == "auto" and not args.no_fallback)
    if not cookies and do_fallback:
        print("falling back to @mherod/get-cookie (all browsers)…", file=sys.stderr)
        try:
            records = getcookie.fetch_cookies(host)
            cookies = [c for c in (serialize.normalize_getcookie_record(r, host, scheme) for r in records) if c]
            used = "get-cookie"
            summary = {"engine": "get-cookie", "found": len(cookies)}
        except getcookie.GetCookieError as exc:
            print(f"get-cookie fallback failed: {exc}", file=sys.stderr)

    if not cookies:
        sys.exit(f"ERROR: not logged into {host} in any local browser — log in there first, then retry.")

    path = serialize.write_state_file(serialize.build_state(cookies), args.out)
    print(f"engine={used} host={host} cookies={len(cookies)} {summary}", file=sys.stderr)
    print(path)
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "list-profiles":
        return cmd_list_profiles(args)
    if args.command == "extract":
        return cmd_extract(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
