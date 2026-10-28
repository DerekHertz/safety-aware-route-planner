# Deploying the MVP

Three pieces, two hosts:

| Piece | Host | Config | URL |
|---|---|---|---|
| Route API (`api/`) | Fly.io app `safety-route-api` | [`fly.toml`](../fly.toml) | https://safety-route-api.fly.dev |
| Commute service (`commute/`) | Fly.io app `safety-route-commute` | [`fly.commute.toml`](../fly.commute.toml) | https://safety-route-commute.fly.dev |
| Web client (`web/`) | Vercel project `safety-route` | Vercel project settings (below) | https://safety-route.vercel.app |

The browser calls both Fly apps directly (CORS), not through a Vercel `/api`
rewrite. A rewrite would put Vercel's egress addresses in front of the API, and
the per-client routing quota would then need a second trusted hop to tell
testers apart (README, "If you deploy it later"). Direct calls keep it at one
hop, Fly's proxy, and save a round trip.

The app names are baked into `fly.toml`, `fly.commute.toml` and the CORS
origins. Renaming any of them means changing all three places plus the Vercel
env vars.

## What it costs

Both Fly apps are one always-on `shared-cpu-1x` machine in `sjc`: 1 GB for the
route API (both packs live in RAM), 256 MB for the commute service, plus a 1 GB
volume. At Fly's list prices that is roughly $6 + $2 + $0.15 a month. Vercel's
Hobby tier is free for this.

## One-time setup

You run the logins; nothing here stores a credential in the repo.

```bash
fly auth login
```

```bash
fly apps create safety-route-api
```

```bash
fly apps create safety-route-commute
```

The Nominatim contact goes in as a secret so it never lands in version control.
Their usage policy wants a way to reach whoever runs the deployment:

```bash
fly secrets set -a safety-route-api --stage "SR_NOMINATIM_CONTACT=safety-aware-route-planner/0.1 (contact: you@example.com)"
```

## Deploy the route API

From the repo root:

```bash
fly deploy --ha=false
```

`--ha=false` matters on the first deploy: Fly otherwise starts two machines,
and without Redis the Nominatim token bucket is per process, so two machines
would double the ~1 req/s ceiling (ADR-0013). No Redis is configured because
one replica is the whole deployment.

The image bakes in every pack in the Dockerfile's `PACK_REGIONS`
(`berkeley_oakland,san_francisco`), fetched from the public bucket in
`packs.lock` and checked against its digests during the build. No GitHub or
bucket credential is involved at build or run time. Updating a pack is a
`packs.lock` change and a redeploy.

Check it:

```bash
curl -s https://safety-route-api.fly.dev/health
```

Expect `"status":"ok"`, `"engine":"cpp"` and `"packs_loaded":2`.

## Deploy the commute service

```bash
fly deploy -c fly.commute.toml --ha=false
```

The first deploy creates the `commute_data` volume (1 GB) that holds
`SR_COMMUTE_DB=/data/commute.sqlite3`. The volume is the only copy of the trip
traces: `fly volumes snapshots list` shows Fly's daily snapshots.

`commute/entrypoint.sh` runs `python -m commute.retention purge` at boot and
every 24 hours after, inside the machine that holds the volume. Its output is
in `fly logs -a safety-route-commute`.

### Tester tokens

Mint one per device. The token prints once; send it to the tester over a
private channel and they paste it into the app's trip-recording settings.

```bash
fly ssh console -a safety-route-commute -u app -C "python -m commute.tokens issue --label alice-phone"
```

```bash
fly ssh console -a safety-route-commute -u app -C "python -m commute.tokens list"
```

```bash
fly ssh console -a safety-route-commute -u app -C "python -m commute.tokens revoke 3"
```

`-u app` runs them as the service's own user, so nothing in `/data` ends up
owned by root.

## Deploy the web client (Vercel)

In the Vercel dashboard, import this GitHub repository as a new project named
`safety-route`:

- **Root Directory:** `web` (Framework Preset: Next.js, detected).
- **Environment variables** (Production). `NEXT_PUBLIC_*` values are inlined at
  build time, so changing one needs a redeploy:

  | Name | Value |
  |---|---|
  | `NEXT_PUBLIC_API_URL` | `https://safety-route-api.fly.dev` |
  | `NEXT_PUBLIC_COMMUTE_URL` | `https://safety-route-commute.fly.dev` |
  | `NEXT_PUBLIC_ENABLE_LIVE_NAV` | `1` to turn on in-app navigation and, with it, trip recording; unset to leave both off |

Pushes to `main` then deploy production. Preview deployments build, but their
generated hostnames are not in either service's CORS list, so their API calls
fail. That is on purpose: allowing them takes an anchored
`SR_CORS_ORIGIN_REGEX` (`.env.example`), never a `*.vercel.app` wildcard.

## Smoke test

From a phone, open https://safety-route.vercel.app, allow location, and plan a
route inside Berkeley/Oakland or San Francisco: three route cards (fast,
balanced, safe) should appear. From a shell, the same call without the browser:

```bash
curl -s -X POST https://safety-route-api.fly.dev/route -H "Origin: https://safety-route.vercel.app" -H "Content-Type: application/json" -d '{"origin":{"lat":37.8715,"lon":-122.2680},"destination":{"lat":37.8044,"lon":-122.2712},"safety_enabled":true}'
```

## Operating it

- Logs: `fly logs -a safety-route-api`, `fly logs -a safety-route-commute`.
- Roll back: `fly releases -a <app>` then `fly deploy --image <previous image>`.
- More capacity on the route API means setting `SR_REDIS_URL` first, then
  `fly scale count 2`. Never scale it out without Redis (ADR-0013).
- The commute service never scales past one machine while it is on SQLite.
