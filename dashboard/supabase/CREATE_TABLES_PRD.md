# PRD — Create the Supabase project + tables for timbre dashboard

**Owner:** Kanika (kanika@findraya.com)
**Hand-off:** This document is the complete brief. The receiving agent has no prior context.

---

## Goal

Stand up a fresh Supabase project named **`timbre`** for the timbre postpartum voice-agent dashboard. Apply the schema and synthetic seed data exactly as written. Return three secrets so the Next.js dashboard (and later, the Pipecat voice agent) can connect to it.

Done when:
- A Supabase project named `timbre` exists in Kanika's org.
- All tables in `supabase/schema.sql` exist with RLS enabled and the listed tables added to the `supabase_realtime` publication.
- All seed rows from `supabase/seed.sql` are inserted (10 patients, 10 newborns, ~11 billing rows, etc.).
- The three secrets below are written into `~/Documents/GitHub/timbre_dashboard/.env.local` (and reported back).
- A smoke test confirms the API can query the DB (see "Verification").

---

## Context (read this first)

- **Repo:** `~/Documents/GitHub/timbre_dashboard` — Next.js 16 + Tailwind 4 dashboard that the Pipecat voice agent posts patient data to. Build is already clean (`npm run build` passes).
- **Voice-agent repo:** `~/Documents/GitHub/timbre_new` — Pipecat + Twilio + NVIDIA Nemotron + Deepgram + Cartesia. Will eventually POST to the dashboard's `/api/v1/*` endpoints.
- **Sibling docs in this folder:**
  - `schema.sql` — full DDL (enums, tables, indexes, realtime publication, RLS policies). **Run first.**
  - `seed.sql` — synthetic postpartum patient roster + billing + appointments + prescriptions + a couple of historical escalations and feedback. **Run second.** Idempotent (uses `on conflict do nothing` for the rows with stable UUIDs).
- **Demo data only.** No real PHI. Patients are fictitious. Safe to log, screenshot, share.
- **Build status:** `npm run build` in `timbre_dashboard` succeeds. Don't refactor the schema or the routes — they're locked.

---

## Decisions already made (do NOT re-litigate)

| Decision | Value |
|---|---|
| Project name | `timbre` |
| Region | Pick **closest to N. America East** (e.g. `us-east-1` or `us-east-2`). The Pipecat worker runs on Pipecat Cloud and Twilio calls are US-based. |
| Plan | **Free tier.** This is a demo with synthetic data; do not upgrade. |
| RLS | **Enabled on every table.** Schema already includes `for select using (true)` anon-read policies — keep them. Writes are service-role only. |
| Realtime | Enable for `call`, `escalation`, `recovery_answer`, `newborn_answer`, `phq_score`, `adherence`, `csat`, `feedback`, `eval_run`, `eval_result`. The `alter publication supabase_realtime add table ...` lines in `schema.sql` handle this — do not skip them. |
| Postgres extensions | Only `pgcrypto` (already in schema). No others. |
| Auth | Not configured for the demo. Skip. |

---

## Required tools

You must have **Supabase MCP** registered in `~/.claude.json` (already done — verify with `claude mcp list | grep supabase` → expect `✓ Connected`). The MCP server is `@supabase/mcp-server-supabase`, authed via `SUPABASE_ACCESS_TOKEN` env var. If `claude mcp list` shows it as connected, you can proceed.

If the Supabase MCP tools are NOT visible in your current session (look for tool names starting with `mcp__supabase__`), the session was started before the MCP was registered. **Quit and re-open Claude Code in `~/Documents/GitHub/timbre_dashboard`** so the tools surface. Do not proceed without them — manual paste into the Supabase web UI is the fallback only if MCP is unavailable.

---

## Step-by-step plan

### 1. Verify MCP and inventory

- Run `claude mcp list` and confirm `supabase: ... ✓ Connected`.
- List the Supabase tools available to you (they'll start with `mcp__supabase__`). You're looking for: list/create projects, run SQL, get project URL + anon key + service-role key, and (optionally) get a connection string.
- Read `supabase/schema.sql` and `supabase/seed.sql` to understand what will run. Don't modify them.

### 2. Create the project

- Find Kanika's Supabase organization via the MCP (list orgs / get user).
- Create a new project named `timbre` in that org, in `us-east-1` (or `us-east-2`).
- Pick a strong DB password; you don't need to surface it — the MCP retains it. (If the MCP requires a password param and won't auto-generate, generate one locally with `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`.)
- Wait for the project to become `ACTIVE_HEALTHY`. The MCP may have a poll method, or you may need to call "get project" repeatedly. Don't run SQL until it's ready.

### 3. Apply schema

- Read `supabase/schema.sql` from disk.
- Execute the entire file as one SQL transaction against the new project. The script is idempotent (uses `if not exists` and `do $$ ... exception when duplicate_object then null` for enums), so re-running is safe.
- Confirm no errors. If anything fails on the `alter publication supabase_realtime add table ...` lines, that's likely a permissions issue on the free tier — see Troubleshooting.

### 4. Apply seed

- Read `supabase/seed.sql` from disk.
- Execute. Confirm 10 rows in `patient`, 10 in `newborn`, 11 in `billing`, 9 in `appointment`, 11 in `prescription`, 5 queued `call` rows, 2 `escalation` rows, 5 `feedback` rows.

### 5. Fetch the secrets

Get from the project's API settings (the MCP exposes these directly — no need to open the web UI):

1. **Project URL** (looks like `https://<ref>.supabase.co`)
2. **anon (public)** key — long JWT starting with `eyJ...`
3. **service_role** key — long JWT starting with `eyJ...`. **Server-only. NEVER ship to the browser.**

### 6. Write `.env.local`

Generate a shared bearer token:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Write `~/Documents/GitHub/timbre_dashboard/.env.local`:
```
NEXT_PUBLIC_SUPABASE_URL=<from step 5>
NEXT_PUBLIC_SUPABASE_ANON_KEY=<from step 5>
SUPABASE_SERVICE_ROLE_KEY=<from step 5>
DASHBOARD_API_TOKEN=<from secrets.token_urlsafe>
```

Then APPEND the same `DASHBOARD_API_TOKEN` value into `~/Documents/GitHub/timbre_new/.env`, replacing the placeholder line `DASHBOARD_API_TOKEN=replace-me-with-a-long-random-string`. Keep the existing `DASHBOARD_API_URL=http://localhost:3000` (it'll get overwritten with the Vercel URL later).

### 7. Verification (smoke tests)

Run all three:

1. **Tables exist and seed loaded:**
   ```bash
   # via Supabase MCP, run SQL:
   select count(*) from patient;
   # expect 10
   select count(*) from billing;
   # expect 11
   select count(*) from "call" where status = 'queued';
   # expect 5
   ```

2. **Realtime is wired:**
   ```sql
   select tablename from pg_publication_tables where pubname = 'supabase_realtime';
   -- expect at least: call, escalation, recovery_answer, newborn_answer, phq_score, adherence, csat, feedback, eval_run, eval_result
   ```

3. **Dashboard can reach Supabase:**
   ```bash
   cd ~/Documents/GitHub/timbre_dashboard
   npm run dev
   # in another shell:
   curl -sS http://localhost:3000/api/v1/health | jq .
   # expect: { "ok": true, "service": "timbre-dashboard", "time": "..." }

   TOKEN="$(grep DASHBOARD_API_TOKEN .env.local | cut -d= -f2)"
   curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:3000/api/v1/patients/call-queue | jq '.data | length'
   # expect: 5 (the queued calls from seed.sql)
   ```

   Then open http://localhost:3000 in a browser — the "Today's call queue" page should show 5 patients (María García, Aisha Patel, Sofía Rodríguez, Emma Thompson, Destiny Johnson). Stop the dev server when done.

### 8. Report back

Reply with:
- Project ref (the `<ref>` from the URL)
- The dashboard URL (`https://<ref>.supabase.co`)
- The four env vars now in `.env.local` (redact the JWT bodies — first 12 chars + `...` is enough proof)
- Output of the three verification commands
- Anything that went wrong

---

## Constraints

- **Do NOT modify** `supabase/schema.sql`, `supabase/seed.sql`, any file in `src/`, or `package.json`. If you find a real bug, surface it — don't fix it silently. The schema and routes are locked.
- **Do NOT commit `.env.local`** to git. It should be covered by Next.js's default `.gitignore` — verify with `git status` in the dashboard repo before finishing; the file must not appear.
- **Do NOT push the dashboard repo to GitHub.** Kanika will handle the remote setup.
- **Do NOT touch** `~/Documents/GitHub/timbre_new/` except to update the `DASHBOARD_API_TOKEN` line in its `.env` (already gitignored).
- **Do NOT upgrade the Supabase plan.** Free tier is required.
- **Do NOT enable Supabase Auth / Storage / Edge Functions.** Not needed for the demo.
- If the MCP exposes a `--read-only` switch or asks to confirm dangerous operations, you have permission to write (this is a fresh empty project) but never `truncate` or `drop` existing data.
- Sensitive values (PAT, service-role key) must stay in `.env.local` / MCP config — never echo them into a chat or log uncensored.

---

## Troubleshooting

- **`alter publication supabase_realtime` fails on free tier:** Supabase's free plan supports Realtime by default; the publication exists out of the box. If the `add table` lines fail with "publication does not exist", create it first: `create publication supabase_realtime;` then re-run those lines. Don't skip — the dashboard's `/live` page depends on Realtime.
- **Project stuck `COMING_UP` for > 90s:** Wait. New projects sometimes take 1–2 min. Don't create a second one — delete the stuck one only after 5 min.
- **MCP tool returns "permission denied":** The `SUPABASE_ACCESS_TOKEN` may have insufficient scopes. Confirm with `claude mcp get supabase` that the env var is set; if it's missing, regenerate the PAT at https://supabase.com/dashboard/account/tokens with `all` scope.
- **`npm run dev` errors with "Missing NEXT_PUBLIC_SUPABASE_URL":** `.env.local` wasn't written correctly. Re-verify the file exists and has all 4 keys.
- **Curl returns 401 unauthorized:** `DASHBOARD_API_TOKEN` in `.env.local` doesn't match the `Authorization: Bearer` value. Make sure no trailing whitespace.

---

## Out of scope (do NOT do these — Kanika or a later step handles them)

- Vercel deployment (separate PRD, separate agent).
- Pipecat voice-agent code changes in `timbre_new/`.
- Cekura MCP setup.
- GitHub repo creation for the dashboard.
- Production HIPAA hardening (BAA, KMS, audit retention) — demo-grade only.
- Adding real patients or any non-synthetic data.
