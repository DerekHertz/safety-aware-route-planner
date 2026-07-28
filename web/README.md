# Web front-end

Next.js 16 (App Router) + React 19 + MapLibre GL 6. Talks to the FastAPI
backend in `../api`; see the repository root README for the whole stack.

## Running

The API must be up first — the map renders without it, but routing and the
geocoder will not.

```bash
npm install
npm run dev
```

Then open http://localhost:3000.

`NEXT_PUBLIC_API_URL` points at the backend and defaults to
`http://localhost:8000`. See `.env.example`; note it is inlined at build time,
so changing it needs a rebuild rather than a restart.

## Scripts

| Script                            | What it does                                          |
| --------------------------------- | ----------------------------------------------------- |
| `npm run dev`                     | Dev server on :3000                                   |
| `npm run build`                   | Production build                                      |
| `npm run lint`                    | ESLint (flat config, `eslint-config-next`)            |
| `npm run typecheck`               | `next typegen && tsc --noEmit`                        |
| `npm run format` / `format:check` | Prettier                                              |
| `npm run copy:maplibre-worker`    | See below — runs automatically on `predev`/`prebuild` |

## The MapLibre worker copy — read before touching the build

`scripts/copy-maplibre-worker.mjs` copies `maplibre-gl-worker.mjs` and
`maplibre-gl-shared.mjs` out of `node_modules` into `public/maplibre/`, and
`components/MapView.tsx` calls `setWorkerUrl("/maplibre/maplibre-gl-worker.mjs")`
to point MapLibre at them.

This exists because MapLibre locates its worker via `import.meta.url`, which
returns an empty string once the bundler has processed it. It then calls
`new Worker("")`, which resolves to the page's own HTML: a worker that never
replies and never errors. Every tile stays in state `loading` and the map shows
nothing but its background colour.

**The failure is completely silent.** Any build that skips the npm lifecycle
hooks — `npm ci --ignore-scripts`, or a host that runs `next build` directly
instead of `npm run build` — ships a blank map with no error anywhere. If you
change the build pipeline, invoke `copy:maplibre-worker` explicitly rather than
relying on `prebuild`.

`public/maplibre/` is gitignored on purpose: it is regenerated from
`node_modules`, and vendoring a second copy into git would let it drift out of
sync with the installed `maplibre-gl`.

## Layout

| Path                                        | Contents                                               |
| ------------------------------------------- | ------------------------------------------------------ |
| `app/page.tsx`                              | All application state; the only stateful component     |
| `app/layout.tsx`                            | Root layout and metadata                               |
| `app/globals.css`                           | The entire stylesheet                                  |
| `components/MapView.tsx`                    | MapLibre wrapper, loaded with `ssr: false`             |
| `components/RouteCard.tsx`, `SearchBox.tsx` | Sidebar UI                                             |
| `lib/types.ts`                              | Mirror of `api/schemas.py` — keep the two in sync      |
| `lib/api.ts`                                | Every backend call                                     |
| `lib/useGeolocation.ts`                     | GPS watch; needs a secure context (https or localhost) |
| `lib/units.ts`                              | Imperial/metric display conversion                     |

Tiles come from [OpenFreeMap](https://openfreemap.org/) (no API key). Map data
© OpenStreetMap contributors, ODbL — the attribution control is a licence
obligation, so don't hide it.
