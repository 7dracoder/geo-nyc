# Deploying geo-nyc

Everything (frontend + backend) ships from the **same Render Blueprint**.
You don't run anything on your laptop after this. The ngrok tunnel is gone.

| Piece | Host | Service name | Public URL after deploy |
|---|---|---|---|
| FastAPI backend | Render (Python web service) | `geo-nyc-api` | `https://geo-nyc-api.onrender.com` |
| Next.js frontend | Render (Node web service) | `geo-nyc-web` | `https://geo-nyc-web.onrender.com` |

Both services are defined in [`render.yaml`](./render.yaml) so a single
Blueprint apply spins up the whole stack.

> **Vercel?** You can keep the existing Vercel deployment running side by
> side — both will work — or delete it once Render is green. See
> [§4](#4-optional-decommission-vercel) below.

---

## 1. Deploy both services to Render

### One-time setup

1. **Push the repo to GitHub** (already done — `7dracoder/geo-nyc`).
2. Go to <https://dashboard.render.com/iacs> →
   **New** → **Blueprint** → pick `7dracoder/geo-nyc`.
3. Render reads `render.yaml` and shows the two services it's about to
   create: **`geo-nyc-api`** and **`geo-nyc-web`**.
4. Click **Apply**. Render will prompt for the one secret env var:

   | Variable | Service | Value |
   |---|---|---|
   | `GEO_NYC_GROQ_API_KEY` | `geo-nyc-api` | your Groq key (starts with `gsk_`) |

   Everything else (Groq model, CORS, `NEXT_PUBLIC_API_BASE_URL`,
   `PYTHON_VERSION`, `NODE_VERSION`) is already in the blueprint.

5. Wait for both builds to finish:
   * `geo-nyc-api`: ~2–3 min (pip compiles numpy + scipy wheels on the
     first build, much faster on rebuilds).
   * `geo-nyc-web`: ~1–2 min (`npm ci` + `next build`).

That's it — open `https://geo-nyc-web.onrender.com` and the app loads
the same way it did on Vercel, but now its API calls go to
`geo-nyc-api.onrender.com` over the proxy.

### Smoke-test the deploy

```bash
# Backend
curl https://geo-nyc-api.onrender.com/api/health
# → {"status":"ok","version":"0.1.0","use_fixtures":true,"enable_gempy":false}

curl https://geo-nyc-api.onrender.com/api/llm/health
# → {"status":"ok","provider":"groq","model":"llama-3.3-70b-versatile",...}

# Frontend (should return HTML, not a 404)
curl -I https://geo-nyc-web.onrender.com/
```

If anything is non-`ok`, open the service in the Render dashboard →
**Logs** tab.

You can also run the whole suite at once:

```bash
python scripts/smoke_render.py https://geo-nyc-api.onrender.com
```

(Verifies health, LLM health, runs list, fixture run, inline-DSL run,
and DSL parse.)

### Free-tier caveats (read this)

* **Spin-down after ~15 min idle** — applies to *both* services. First
  request after a cold start takes ~50s while uvicorn / `next start`
  boots. The frontend handles this gracefully (the dock just shows the
  spinner longer than usual).
* **No persistent disk on free tier.** Anything the backend writes
  under `/var/data` (runs, exports, fields, raw PDFs) is wiped on every
  cold start. Demo runs survive within a single warm session, then
  disappear.
  * Fix A: re-run `make seed-runs` after each restart (idempotent).
  * Fix B: attach a persistent disk. Edit `render.yaml`, uncomment the
    `disk:` block at the bottom of the `geo-nyc-api` service, redeploy.
    Costs ~$1/mo for 1 GB on the standard plan; free plan ignores it.
* **Build minutes are not unlimited.** Render's free tier gives you 500
  build minutes / month across all services. The two builds together
  use ~5 min, so a few redeploys per day is fine.

### Upgrading models / changing config later

Every env var is editable from the Render dashboard
(*Settings → Environment* on each service). Common backend tweaks:

| Variable | Service | What it does |
|---|---|---|
| `GEO_NYC_GROQ_MODEL` | `geo-nyc-api` | Switch Llama variant. `llama-3.1-8b-instant` is ~3× faster, lower quality. `meta-llama/llama-4-scout-17b-16e-instruct` is the new Llama 4 Scout. |
| `GEO_NYC_GROQ_API_KEY` | `geo-nyc-api` | Rotate when needed (revoke the old key in the Groq console first). |
| `GEO_NYC_LLM_TIMEOUT_SECONDS` | `geo-nyc-api` | Bump if the LLM times out on long PDFs. |
| `GEO_NYC_USE_FIXTURES` | `geo-nyc-api` | Set to `false` once you trust the LLM end-to-end. |
| `NEXT_PUBLIC_API_BASE_URL` | `geo-nyc-web` | Already wired to the api service. Only change this if you renamed the api service. **Requires a redeploy** of `geo-nyc-web` to bake into the bundle. |

---

## 2. How the two Render services talk to each other

The frontend uses the **same proxy + rewrite** logic it always has,
just pointed at Render now:

```
browser
  └── https://geo-nyc-web.onrender.com/geo-nyc-proxy/api/runs
        └── (Next.js rewrite, defined in next.config.ts)
              └── https://geo-nyc-api.onrender.com/api/runs
```

The CORS config on `geo-nyc-api` (`GEO_NYC_CORS_ORIGINS` /
`GEO_NYC_CORS_ORIGIN_REGEX` in `render.yaml`) already allows
`https://geo-nyc-web.onrender.com` and the regex covers any
`https://geo-nyc-*.onrender.com` preview hostnames Render might issue.

---

## 3. Seed the demo runs (optional but recommended)

Right after the first deploy (and after any cold start, if you don't
have a persistent disk), populate the 3D dock with the three NYC
geology PDFs:

**Quick: one fixture run**

```bash
curl -X POST "https://geo-nyc-api.onrender.com/api/run" \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Full: PDF-driven seed (downloads + extracts + LLM-extracts)**

Open a Shell on the `geo-nyc-api` service from the Render dashboard
(*Settings → Shell*) and run:

```bash
make seed-runs
```

The frontend's 3D dock auto-picks the highest-priority succeeded run on
every page load (priority order: `inline_dsl` > `document_llm_dsl` >
`document_llm` > `document_chunks` > `fixture`).

---

## 4. (Optional) Decommission Vercel

Once `geo-nyc-web.onrender.com` is green, you can either:

**Option A: keep both running.** Costs nothing, and Vercel can stay as
a fallback. Just point its `NEXT_PUBLIC_API_BASE_URL` at the Render API
too:

1. <https://vercel.com/dashboard> → `geo-nyc` → **Settings** →
   **Environment Variables**.
2. Set `NEXT_PUBLIC_API_BASE_URL = https://geo-nyc-api.onrender.com`.
3. **Deployments** → latest → **⋮** → **Redeploy**.

   Variables to **delete** from Vercel if they exist (these are either
   backend-only or for the old ngrok flow):

   | Variable | Why delete |
   |---|---|
   | `GEO_NYC_*` | Backend-only; useless on the frontend. |
   | `*GROQ_API_KEY*` | Backend secret. Anything `NEXT_PUBLIC_*` ships in the browser bundle and would leak the key. |
   | `*OLLAMA*` | No longer used. |

**Option B: shut Vercel down.**
<https://vercel.com/dashboard> → `geo-nyc` → **Settings** →
**General** → **Delete Project**. The Render URL becomes your only
production frontend.

