#!/usr/bin/env python3
"""
smoke_test.py — Quick smoke test for all enePath WebAdmin GET routes.

Usage (run on the server or any machine that can reach it):
    python3 smoke_test.py [--host 192.168.68.152] [--port 8443] [--user admin] [--pass admin]

Requires only the stdlib — no third-party packages needed.
"""

import argparse
import http.cookiejar
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

# ── All GET routes to test ─────────────────────────────────────────────────────
ROUTES = [
    # Dashboard
    "/dashboard",

    # Users
    "/users",
    "/users/add",

    # Groups
    "/groups",
    "/groups/add",

    # Lines
    "/lines",
    "/lines/add",
    "/lines/line-groups",

    # Calls
    "/calls",

    # Trunks / Routing
    "/trunks",
    "/trunks/add",
    "/inbounds",
    "/inbounds/add",
    "/outbounds",
    "/outbounds/add",

    # Settings
    "/settings/general",
    "/settings/node",
    "/settings/alarms",
    "/settings/hold-music",
    "/settings/logo",
    "/settings/blacklist",
    "/settings/whitelist",
    "/settings/monitoring",
    "/settings/intercom",

    # Topology
    "/topology",

    # Audit
    "/audit",

    # Backup / Restore
    "/backup",

    # Media
    "/media/ringtones",
    "/media/dialtone",

    # Recordings
    "/recordings",
    "/recordings/line-permissions",

    # Benchmarking
    "/benchmarking",

    # Routing
    "/routes",
    "/routes/add",

    # Docs
    "/docs/admin",
    "/docs/dev",

    # System
    "/license",
    "/stations",
    "/logs",

    # Directory
    "/directory",

    # Health (no auth needed)
    "/health",
]

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def make_opener(base_url: str):
    """Build an urllib opener with a cookie jar and SSL verification disabled."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(jar),
    )
    opener.addheaders = [("User-Agent", "enepath-smoke-test/1.0")]
    return opener, jar


def login(opener, base_url: str, username: str, password: str) -> bool:
    """POST to /login and return True if we land on /dashboard."""
    url = base_url + "/login"
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    try:
        resp = opener.open(url, data=data, timeout=10)
        final_url = resp.geturl()
        if "/dashboard" in final_url or "/login" not in final_url:
            return True
        # Read response to see if we got a 200 on /login (= fail)
        body = resp.read(4096).decode(errors="replace")
        if "Invalid" in body or "fail" in body.lower():
            print(f"{RED}Login failed — check credentials{RESET}")
        return False
    except urllib.error.HTTPError as e:
        print(f"{RED}Login HTTP error {e.code}{RESET}")
        return False
    except Exception as e:
        print(f"{RED}Login error: {e}{RESET}")
        return False


def test_route(opener, base_url: str, route: str) -> tuple[int, str]:
    """GET a route, return (status_code, error_snippet)."""
    url = base_url + route
    try:
        resp = opener.open(url, timeout=15)
        body = resp.read(8192).decode(errors="replace")
        code = resp.getcode()
        snippet = ""
        if "Something Went Wrong" in body or "Traceback" in body:
            # Extract first line of traceback
            for line in body.splitlines():
                line = line.strip()
                if line and "Error" in line:
                    snippet = line[:120]
                    break
        return code, snippet
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read(8192)
        except Exception:
            pass
        snippet = body.decode(errors="replace")[:120].replace("\n", " ").strip()
        return e.code, snippet
    except urllib.error.URLError as e:
        return 0, str(e.reason)
    except Exception as e:
        return 0, str(e)


def main():
    parser = argparse.ArgumentParser(description="enePath WebAdmin smoke test")
    parser.add_argument("--host", default="192.168.68.152")
    parser.add_argument("--port", default=8443, type=int)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", "--pass", default="admin")
    args = parser.parse_args()

    base_url = f"https://{args.host}:{args.port}"

    print(f"\n{BOLD}enePath WebAdmin Smoke Test{RESET}")
    print(f"Target : {CYAN}{base_url}{RESET}")
    print(f"User   : {args.user}")
    print("─" * 60)

    opener, jar = make_opener(base_url)

    print("Logging in … ", end="", flush=True)
    ok = login(opener, base_url, args.user, args.password)
    if not ok:
        print(f"{RED}FAILED{RESET} — aborting")
        sys.exit(1)
    print(f"{GREEN}OK{RESET}")
    print("─" * 60)

    passed = []
    failed = []
    warned = []

    for route in ROUTES:
        code, snippet = test_route(opener, base_url, route)
        label = f"{route:<45}"
        if code == 200:
            print(f"  {GREEN}PASS{RESET}  {label}  {GREEN}{code}{RESET}")
            passed.append(route)
        elif code in (301, 302, 303, 307, 308):
            print(f"  {YELLOW}REDIR{RESET} {label}  {YELLOW}{code}{RESET}")
            warned.append((route, code, "redirect"))
        elif code == 404:
            print(f"  {YELLOW}404  {RESET} {label}  {YELLOW}{code}{RESET}  {snippet}")
            warned.append((route, code, snippet))
        elif code == 0:
            print(f"  {RED}ERR  {RESET} {label}  {RED}CONN{RESET}  {snippet}")
            failed.append((route, code, snippet))
        else:
            print(f"  {RED}FAIL {RESET} {label}  {RED}{code}{RESET}  {snippet[:60]}")
            failed.append((route, code, snippet))

    print("─" * 60)
    print(f"\n{BOLD}Summary{RESET}")
    print(f"  {GREEN}Passed : {len(passed)}{RESET}")
    print(f"  {YELLOW}Warned : {len(warned)}{RESET}")
    print(f"  {RED}Failed : {len(failed)}{RESET}")

    if failed:
        print(f"\n{BOLD}{RED}Failures:{RESET}")
        for route, code, msg in failed:
            print(f"  [{code}] {route}")
            if msg:
                print(f"        {msg[:100]}")

    print()
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
