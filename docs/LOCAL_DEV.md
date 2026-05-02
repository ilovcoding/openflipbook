# Local dev — run everything without Modal

Endless Canvas runs locally as two processes:

1. **Python FastAPI backend** — wraps fal (image + animate) and OpenRouter (LLM + VLM).
2. **Next.js web app** — the UI.

Plus your already-existing MongoDB cluster and Cloudflare R2 bucket for
persistence.

## One-time setup

### Python backend

```bash
cd apps/modal-backend
uv venv --python 3.12 --seed
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env      # then fill in FAL_KEY + an LLM provider key
```

### Web app

```bash
cd apps/web
cp ../../.env.example .env.local   # then fill it in
```

Fill in `.env.local`:

```env
MODAL_API_URL=http://localhost:8787

MONGODB_URI=<your existing cluster>
MONGODB_DB=openflipbook

BLOB_STORAGE_PROVIDER=oss
BLOB_STORAGE_PREFIX=flipbook
OSS_REGION=oss-cn-beijing
OSS_ACCESS_KEY_ID=<your RAM AccessKey ID>
OSS_ACCESS_KEY_SECRET=<your RAM AccessKey secret>
OSS_BUCKET=openflipbook
OSS_PUBLIC_BASE_URL=https://openflipbook.oss-cn-beijing.aliyuncs.com

# Or use Cloudflare R2 instead:
# BLOB_STORAGE_PROVIDER=r2
# R2_ACCOUNT_ID=5d3211faff5f9446d940004811bd...
# R2_ACCESS_KEY_ID=<reuse or new token with endlessvideo write access>
# R2_SECRET_ACCESS_KEY=<same>
# R2_BUCKET=endlessvideo
# R2_PUBLIC_BASE_URL=https://pub-<hash>.r2.dev

# Leave blank — local dev uses the cheap fal fallback.
NEXT_PUBLIC_LTX_WS_URL=
```

For OSS, `OSS_PUBLIC_BASE_URL` should be a browser-readable bucket endpoint
or custom domain. If the bucket is private, put a CDN/signed-URL layer in
front before using permalinks. For R2, enable the R2.dev subdomain on the
bucket to get `R2_PUBLIC_BASE_URL`.

## Run

```bash
# Terminal 1 — Python backend
cd apps/modal-backend
source .venv/bin/activate
PORT=8787 python local_server.py

# Terminal 2 — Next.js
pnpm dev
```

Open <http://localhost:3000/play> and type a query. The flow:

1. Browser → `POST /api/generate-page` → Next proxies to `http://localhost:8787/sse/generate`.
2. Python backend plans the page (OpenRouter Qwen), calls fal nano-banana,
   streams back the final JPEG as an SSE event.
3. Browser renders the image, POSTs to `/api/nodes` which uploads to R2 and
   writes metadata to Mongo. URL flips to `/n/<id>`.
4. Click on the image → VLM resolves the region → next page generates.
5. Click "Animate ▶" → Python backend calls `fal-ai/ltx-video` → returns a
   5-second MP4 URL → browser plays it.

## Debug

| Symptom | Check |
|---|---|
| `/play` shows "MODAL_API_URL is not set" | `apps/web/.env.local` loaded? Next restarted after edit? |
| 502 / timeout on generate | Python backend running on the port matching `MODAL_API_URL`? `curl http://localhost:8787/health`. |
| Image generates but save fails | Mongo reachable? OSS/R2 token scoped to the bucket? `/status` page shows red badges. |
| VLM click returns parent title | That is the fallback when VLM can't parse. Check OpenRouter/DashScope credits and model name. |
| Animate 500s | fal credits? Check Python backend logs. |

## Port already in use on 8000

macOS AirPlay Receiver listens on 8000. We default to **8787** for this reason.
If a different port is already taken, set `PORT=9000` (or any free port) and
update `MODAL_API_URL` in `apps/web/.env.local` to match.

## Without MongoDB + R2

The app runs, but:

- Generated pages show in `/play` and work in-memory.
- Saving to `/api/nodes` returns 503 — no permalinks.
- `/play` keeps working; you just don't get `/n/<id>` URLs.

So for a quick "does it generate pretty pictures" smoke test, only FAL_KEY +
one LLM provider key (`OPENROUTER_API_KEY` or `DASHSCOPE_API_KEY`) is strictly
required.
