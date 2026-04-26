# Deploying geo-nyc

Production layout:

| Piece | Host | Why |
|---|---|---|
| FastAPI backend | **Render** (Python web service) | Long-running, talks to Groq, writes mesh artifacts |
| Next.js frontend | **Vercel** (already deployed) | Vercel is purpose-built for Next.js; no reason to move it |

You don't run anything on your laptop after this. The ngrok tunnel is gone.

---

## 1. Deploy the backend to Render

The repo includes a Render Blueprint at [`render.yaml`](./render.yaml) that
defines the service, env vars, and storage paths. You only have to provide
the Groq API key — everything else is wired up automatically.

### One-time setup

1. **Push the repo to GitHub** (already done — `7dracoder/geo-nyc`).
2. Go to <https://dashboard.render.com/select-repo?type=blueprint>.
3. Pick `7dracoder/geo-nyc`. Render reads `render.yaml` and shows you the
   service it's about to create (`geo-nyc-api`).
4. Click **Apply**. Render will prompt for the one secret env var:

   | Variable | Value |
   |---|---|
   | `GEO_NYC_GROQ_API_KEY` | your Groq key (starts with `gsk_`) |

5. Wait for the build to finish (~2–3 minutes for the first build; pip
   has to compile numpy + scipy wheels). Once it's green, note the
   service URL — it'll look like `https://geo-nyc-api.onrender.com`.

### Smoke-test the deploy

```bash
curl https://geo-nyc-api.onrender.com/api/health
# → {"status":"ok","version":"0.1.0","use_fixtures":true,"enable_gempy":false}

curl https://geo-nyc-api.onrender.com/api/llm/health
# → {"status":"ok","provider":"groq","model":"llama-3.3-70b-versatile",...}
```

If either is non-`ok`, check the Render service logs.

### Free-tier caveats (read this)

* **Spin-down after ~15 min idle.** First request after spin-down takes
  ~50s while uvicorn boots and pip-installed deps are restored. The
  Vercel frontend handles this gracefully (it just looks slow).
* **No persistent disk.** Anything the backend writes under `/var/data`
  (runs, exports, fields, raw PDFs) is wiped on every cold start. Demo
  runs survive within a single warm session, then disappear.
  * Fix A: re-run `make seed-runs` after each restart (idempotent).
  * Fix B: attach a persistent disk. Edit `render.yaml`, uncomment the
    `disk:` block at the bottom of the `geo-nyc-api` service, redeploy.
    Costs ~$1/mo for 1 GB on the standard plan; free plan ignores it.

### Upgrading models / changing config later

All env vars are editable from the Render dashboard
(*Settings → Environment*). Common ones:

| Variable | What it does |
|---|---|
| `GEO_NYC_GROQ_MODEL` | Switch Llama variant. `llama-3.1-8b-instant` is ~3× faster, lower quality. `meta-llama/llama-4-scout-17b-16e-instruct` is the new Llama 4 Scout. |
| `GEO_NYC_GROQ_API_KEY` | Rotate when needed (revoke old key in Groq console first). |
| `GEO_NYC_LLM_TIMEOUT_SECONDS` | Bump if the LLM occasionally times out on long PDFs. |
| `GEO_NYC_USE_FIXTURES` | Set to `false` once you trust the LLM end-to-end. |

---

## 2. Point the Vercel frontend at Render

The frontend already reads `NEXT_PUBLIC_API_BASE_URL` and proxies all
`/api/*` calls through it. You only need to update that one variable.

1. Go to <https://vercel.com/dashboard> → your `geo-nyc` project →
   **Settings** → **Environment Variables**.
2. Edit `NEXT_PUBLIC_API_BASE_URL` and set it to your Render URL:

   ```
   https://geo-nyc-api.onrender.com
   ```

3. Apply to **Production** (and **Preview** if you want previews to hit
   the same backend; usually fine).
4. Trigger a redeploy: **Deployments** → the latest one → **⋮** →
   **Redeploy**. Vercel rebuilds with the new URL baked into the bundle.

### Variables to delete from Vercel

If any of these exist in Vercel, **remove them** — they were either for
the old ngrok flow or for a setup that put secrets in the wrong place:

| Variable | Why delete |
|---|---|
| `GEO_NYC_*` | Backend-only; useless on the frontend. |
| `*GROQ_API_KEY*` | Backend secret. Anything `NEXT_PUBLIC_*` ships in the browser bundle and would leak the key. |
| `*OLLAMA*` | No longer used. |

The only Vercel envs the frontend needs are:

| Variable | Required? | Value |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | yes | `https://geo-nyc-api.onrender.com` |
| `NEXT_PUBLIC_MESH_RUN_ID` | no | optional debug pin to a specific run id |
| `NEXT_PUBLIC_GLTF_URL` | no | optional override for the dock's GLB URL |

---

## 3. Seed the demo runs (optional but recommended)

Right after the first deploy (and after any cold start, if you don't
have a persistent disk), populate the dock with the three NYC geology
PDFs:

```bash
# From your laptop, with $RENDER_URL set to your Render URL:
curl -X POST "$RENDER_URL/api/run" \
  -H "Content-Type: application/json" \
  -d '{}'   # creates one fixture-mode run
```

Or, for the full PDF-driven seed (downloads + extracts + LLM-extracts):

```bash
# Open a Render Shell from the dashboard (Settings → Shell) and run:
make seed-runs
```

The Vercel frontend's 3D dock auto-picks the highest-priority succeeded
run on every page load (priority order: `inline_dsl` >
`document_llm_dsl` > `document_llm` > `document_chunks` > `fixture`).

---

## 4. (Optional) Mirror to Render

If you really want everything on one platform, you can also deploy the
Next.js frontend to Render as a separate web service:

```yaml
# Append to render.yaml — this is just a sketch, not enabled by default.
- type: web
  name: geo-nyc-web
  runtime: node
  plan: free
  rootDir: apps/web
  buildCommand: npm ci && npm run build
  startCommand: npm start
  envVars:
    - key: NEXT_PUBLIC_API_BASE_URL
      value: https://geo-nyc-api.onrender.com
```

But honestly: leave it on Vercel. Vercel's edge network and Next.js
runtime are noticeably faster than running a Node web service on Render.
