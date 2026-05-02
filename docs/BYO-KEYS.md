# BYO Keys — running Endless Canvas yourself

Endless Canvas has no hosted backend. To actually generate pages you need to provide:

1. **LLM API key** — planning + VLM click interpretation + web search.
   Use either OpenRouter or Alibaba Cloud Model Studio / Bailian (DashScope).
2. **fal API key** — image generation (nano-banana).
3. **Modal account + token** — hosts the orchestration FastAPI app (and, once step 8 lands, the LTX-2 video worker).
4. **Alibaba Cloud OSS or Cloudflare R2 bucket** — blob storage for generated images.
5. **Postgres database** — metadata for the node graph. Any Postgres works (Railway, Neon, Supabase, local).

Optional for v1:

- Custom `OPENROUTER_VLM_MODEL` / `OPENROUTER_TEXT_MODEL` if you want to swap off the Qwen 2.5 defaults.

## 1. Accounts & keys

| Service | Where to get it | Env var |
|---|---|---|
| OpenRouter | <https://openrouter.ai/keys> | `OPENROUTER_API_KEY` |
| Alibaba Cloud Model Studio / Bailian | <https://bailian.console.aliyun.com/> | `DASHSCOPE_API_KEY` |
| fal | <https://fal.ai/dashboard/keys> | `FAL_KEY` |
| Modal | `brew install modal-cli && modal token new` | (stored on disk) |
| Alibaba Cloud OSS | OSS bucket + RAM AccessKey. Needs write permission on the bucket. | `OSS_REGION`, `OSS_ACCESS_KEY_ID`, `OSS_ACCESS_KEY_SECRET`, `OSS_BUCKET` |
| OSS public URL | Bucket public endpoint or custom/CDN domain. | `OSS_PUBLIC_BASE_URL` |
| Cloudflare R2 | Cloudflare dash → R2 → Manage tokens. Needs *Object Read & Write*. | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` |
| R2 public URL | Enable the R2 bucket's public dev URL, or attach a custom domain. | `R2_PUBLIC_BASE_URL` |
| MongoDB | Railway → Add MongoDB (or Atlas M0 free). | `MONGODB_URI`, `MONGODB_DB` |

## 2. Set Modal secrets

Modal reads secrets at runtime from a named secret, not your local `.env`.
Create one that the backend expects. For OpenRouter:

```bash
modal secret create openflipbook-secrets \
  FAL_KEY="$FAL_KEY" \
  OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  LLM_PROVIDER="openrouter" \
  OPENROUTER_ENABLE_WEB_SEARCH=true
```

For Alibaba Cloud Model Studio / Bailian Qwen:

```bash
modal secret create openflipbook-secrets \
  FAL_KEY="$FAL_KEY" \
  DASHSCOPE_API_KEY="$DASHSCOPE_API_KEY" \
  LLM_PROVIDER="dashscope" \
  DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1" \
  DASHSCOPE_TEXT_MODEL="qwen-plus" \
  DASHSCOPE_VLM_MODEL="qwen-vl-plus" \
  DASHSCOPE_ENABLE_WEB_SEARCH=true \
  DASHSCOPE_ENABLE_THINKING=false
```

If your account has the newer VL models enabled, `DASHSCOPE_VLM_MODEL` can be
changed to `qwen3-vl-plus` or `qwen3-vl-flash`.

## 3. Deploy the Modal backend

```bash
cd apps/modal-backend
modal deploy generate.py
# → prints a URL ending in ...modal.run
```

Copy that URL into `apps/web/.env.local`:

```bash
MODAL_API_URL=https://<your-workspace>--openflipbook-generate-fastapi-ingress.modal.run
```

During development you can use `modal serve generate.py` instead — it prints a hot-reloading ephemeral URL.

## 4. Configure the web app

Create `apps/web/.env.local`:

```bash
BLOB_STORAGE_PROVIDER=oss
BLOB_STORAGE_PREFIX=flipbook
OSS_REGION=oss-cn-beijing
OSS_ENDPOINT=
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
OSS_BUCKET=openflipbook
OSS_PUBLIC_BASE_URL=https://openflipbook.oss-cn-beijing.aliyuncs.com

MODAL_API_URL=...
MONGODB_URI=mongodb://...
MONGODB_DB=openflipbook

# Alternative: Cloudflare R2
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=openflipbook
R2_PUBLIC_BASE_URL=https://pub-<hash>.r2.dev
# Optional: WS URL from `modal deploy ltx_stream.py`.
NEXT_PUBLIC_LTX_WS_URL=
```

No DB migration step — the web app creates the `nodes` collection + indexes
on first request. See `infra/MONGO.md` for the document shape.

## 5. Run it

```bash
pnpm install
pnpm dev
# open http://localhost:3000/play
```

## Cost notes

- OpenRouter Qwen 2.5 72B Text ≈ $0.0005 / request; VLM 72B ≈ $0.0015 / click resolution.
- fal nano-banana ≈ $0.02 / image (varies).
- Modal CPU container (generate.py) idles at $0; wakes for a few seconds per request.
- R2: storage is cheap, egress is free on the public dev URL.
- Railway MongoDB: hobby tier is enough to start; Mongo Atlas M0 is free.

Expected cost per "page explored": ~$0.02–0.03 of mixed spend, mostly fal.

## Future: live video toggle (step 8)

Will add a second Modal app (`ltx_stream.py`) deploying a GPU class. Costs jump to ~$2–4/GPU-hr while actively streaming — that's why it's a per-page toggle, and why the demo site only shows a prerecorded clip.
