"""Smoke-test the compose stack through the web container, the way a phone
reaches it: every call goes to the Next server, which forwards /api/* and
/commute/* (web/next.config.ts).

    docker compose exec -T commute python -m commute.tokens issue --label smoke \
      | python scripts/compose_smoke.py [--base http://localhost:3000]

Reads a freshly minted tester token from stdin (the CLI prints it on its last
line), so the token never appears in a command line or a log. Stdlib only.
Exits non-zero if any check fails. It writes one synthetic trip under that
token: run it against a throwaway stack (CI) or revoke the token afterwards.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="http://localhost:3000")
    args = parser.parse_args()
    lines = sys.stdin.read().strip().splitlines()
    if not lines:
        print("error: no tester token on stdin", file=sys.stderr)
        return 2
    token = lines[-1].strip()

    def call(method: str, path: str, body: object = None, auth: bool = True) -> int:
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {token}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(args.base + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code

    # Ten minutes ago: inside the ingest's accepted window for fix times.
    t0 = int(time.time() * 1000) - 600_000
    trip = str(uuid.uuid4())
    fixes = [{"t": t0 + 1000 * i, "lat": round(37.8712 + 0.00009 * i, 7),
              "lon": -122.2687, "speed_mps": 10.0, "accuracy_m": 5.0,
              "heading_deg": 0.0} for i in range(5)]
    chunk = {"fixes": fixes}
    end = {"ended_at": t0 + 60_000, "arrived": True,
           "prediction": {"effective_at": t0, "eta_s": 540.0, "level": "safe",
                          "profile_version": "smoke"}}
    route = {"origin": {"lat": 37.8715, "lon": -122.2680},
             "destination": {"lat": 37.8044, "lon": -122.2712}}
    chunk_path = f"/commute/v1/trips/{trip}/chunks"

    checks = [
        ("page renders", call("GET", "/", auth=False), 200),
        ("api health via /api", call("GET", "/api/health", auth=False), 200),
        ("route via /api", call("POST", "/api/route", route, auth=False), 200),
        ("commute health via /commute", call("GET", "/commute/health", auth=False), 200),
        ("no token is 401", call("GET", "/commute/v1/me", auth=False), 401),
        ("token accepted", call("GET", "/commute/v1/me"), 200),
        ("chunk stored", call("PUT", f"{chunk_path}/0", chunk), 201),
        ("identical replay is a no-op", call("PUT", f"{chunk_path}/0", chunk), 200),
        ("trip ended", call("POST", f"/commute/v1/trips/{trip}/end", end), 201),
        # The proxy must not swallow the service's own size limit (1 MiB).
        ("oversized chunk is 413",
         call("PUT", f"{chunk_path}/1", {"fixes": fixes, "pad": "x" * 1_100_000}), 413),
    ]
    failed = 0
    for name, got, want in checks:
        ok = got == want
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got}" + ("" if ok else f" (want {want})"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
