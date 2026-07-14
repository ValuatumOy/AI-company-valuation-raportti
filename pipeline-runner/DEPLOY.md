# Deploy — Vercel (frontend) + Railway (backend) + Supabase (Postgres)

The backend needs Python + subprocess + headless Chrome + long SSE, so it can't
run on Vercel/Supabase serverless — it runs as a Docker container on Railway.
Supabase provides the Postgres database; Vercel serves the static UI.

```
 Browser ──HTTPS──▶ Vercel (static React UI)
                      │  fetch(VITE_API_BASE + /api/...) + Authorization: Bearer APP_TOKEN
                      ▼
                    Railway (FastAPI: validators, node+chromium, SSE)
                      │  DATABASE_URL ─────▶ Supabase Postgres (persistent, multi-safe)
                      │  OPENROUTER_API_KEY / VALUATUM_TOKEN / VALU_MCP_PROFINDER_URL  (server-side only)
```

## 1. Supabase — Postgres

1. Create a project at supabase.com.
2. Project → **Connect** → **Connection string** → URI. Copy it.
   - Use the **Transaction pooler** (port 6543) or Session pooler; both work
     (the backend disables prepared statements for PgBouncer).
   - Keep `?sslmode=require`. Replace `[YOUR-PASSWORD]` with the DB password.
3. That URI is your `DATABASE_URL`. Tables auto-create on first backend boot.

(No SQL to run — `db.py` creates the schema on startup.)

## 2. Backend — Railway

1. railway.com → **New Project** → **Deploy from GitHub repo** →
   `Valuatum/AI-company-valuation-raportti`.
2. Railway reads `railway.toml` → builds `pipeline-runner/Dockerfile` from the
   repo root. (No volume needed — state lives in Supabase.)
3. Service → **Variables** → add:
   ```
   DATABASE_URL            = <Supabase URI from step 1>
   APP_TOKEN               = <run: openssl rand -hex 24>
   OPENROUTER_API_KEY      = sk-or-...
   VALUATUM_TOKEN          = ...
   VALU_MCP_PROFINDER_URL  = https://...
   ALLOWED_ORIGINS         = https://<your-vercel-app>.vercel.app
   ```
   Railway injects `PORT` automatically; the container honors it.

   Finished-report email delivery (Amazon SES) adds these variables:
   ```
   REPORT_EMAIL_ENABLED    = 1        # set 0 for an immediate delivery kill switch
   REPORT_EMAIL_FROM       = Valuatum <reports@yourdomain.com>
   CLIENT_SITE_URL         = https://<your-vercel-app>.vercel.app
   AWS_REGION              = eu-north-1
   AWS_ACCESS_KEY_ID       = ...
   AWS_SECRET_ACCESS_KEY   = ...
   AWS_SESSION_TOKEN       = ...      # temporary credentials only; omit otherwise
   ```
   Keep AWS credentials server-side only — never expose them through Vercel or a
   `VITE_` variable. See **SES prerequisites** below before enabling delivery.
4. Service → **Settings** → **Generate Domain**. That URL (e.g.
   `https://valu-pipeline.up.railway.app`) is your backend.
5. Check `…/api/health` → `{"ok":true,"auth":true}`.

`APP_TOKEN` is the shared password the whole team uses. Keep it secret — anyone
with it can spend the OpenRouter/Valuatum tokens. Rotate by changing the var.

### SES prerequisites (report email)

Finished reports are delivered through Amazon SES. Before setting
`REPORT_EMAIL_ENABLED=1`:

1. Set the correct SES region and use it for `AWS_REGION`.
2. Use a From address under the verified domain for `REPORT_EMAIL_FROM`.
3. Create a least-privilege IAM.
4. Store its credentials only in service variables.

Suggested minimum IAM policy (correct the ARN for your real account and region;
do not broaden permissions without explaining why the identity-scoped policy is
insufficient):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ses:SendEmail",
      "Resource": "arn:aws:ses:REGION:ACCOUNT_ID:identity/example.com"
    }
  ]
}
```

## 3. Frontend — Vercel

1. vercel.com → **Add New Project** → import the same GitHub repo.
2. **Root Directory** = `pipeline-runner/frontend` (framework auto-detected: Vite).
3. **Environment Variables**:
   ```
   VITE_API_BASE = https://<your-railway-domain>
   ```
4. Deploy. Note the final `*.vercel.app` domain.
5. Back on Railway, make sure `ALLOWED_ORIGINS` matches that exact domain;
   redeploy the backend if you changed it.

## 4. Use

Open the Vercel URL → it prompts for the access token → paste `APP_TOKEN`
(stored in the browser; change anytime via the 🔒 button).

## Notes

- **Local dev unchanged:** `./run.sh`. No `DATABASE_URL` → SQLite. No `APP_TOKEN`
  → auth disabled. Empty `VITE_API_BASE` → Vite proxies `/api`.
- **Persistence:** Supabase Postgres survives restarts and allows >1 backend
  instance. Inspect/edit data in the Supabase Table editor.
- **Alt hosts:** the same `pipeline-runner/Dockerfile` runs on Fly.io (`fly.toml`
  included) or Render — set the same env vars.
- **PDF/report:** the image bundles `node` + `chromium`, so HTML/PDF report
  generation works in prod.
