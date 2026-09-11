# Public-read, internal-write Railway release runbook

This deployment intentionally exposes read-only Cockpit pages and GET/HEAD APIs
to the public. It keeps every state-changing API behind an internal token or
signed session. The product deliberately has no password page.

## 1. Release boundary

Preferred order:

1. Set a long random `INTERNAL_ACCESS_TOKEN` in Railway's secret environment
   variables. Never put it in `.env.example`, source, screenshots, logs, or a
   chat message.
2. Keep `/healthz`, UI pages, static assets, and GET/HEAD APIs publicly
   reachable. Anonymous responses are marked `X-Mango-Access-Mode:
   public-read-only`.
3. Require the token or a signed session for every POST/PUT/PATCH/DELETE
   request. Anonymous writes return 401 before reaching business logic.
4. If the read data later needs to become private again, put the origin behind
   the organisation's SSO/VPN/identity proxy; do not weaken the write gate.

Railway Private Networking is suitable for service-to-service traffic inside
the project, but it does not by itself give a human browser an authenticated
internal route. Use organisation SSO/VPN for that human-access boundary.

There is no Basic Auth and no password form. To make a 15-minute browser link
without placing the underlying token in the URL:

```bash
cd kol_database
PYTHONPATH=. python3 scripts/bootstrap_internal_access.py
PYTHONPATH=. python3 scripts/bootstrap_internal_access.py \
  --sync-railway --service '<exact-service>' --environment production
PYTHONPATH=. python3 scripts/generate_access_link.py \
  --base-url https://internal.example.com --ttl-minutes 15
```

The bootstrap helper generates the value with `secrets`, writes/reuses it in
the ignored repo-root `.env` at mode 0600, and never prints it. Railway sync
uses `railway variable set INTERNAL_ACCESS_TOKEN --stdin --skip-deploys` with
an explicit service/environment, suppresses CLI output, and stages the change
without restarting production. Do not replace this with `KEY=value` on a
command line: that leaks into shell history and process listings. Only the
short-lived fragment-signed browser link is printed when the final command is
explicitly requested.

The signed values are carried only in the URL fragment, which is never sent
in the initial HTTP request or proxy logs. A minimal bootstrap clears the
fragment, POSTs it same-origin, redirects to `/`, and sets a
rotating-token-bound HttpOnly, Secure, SameSite=Lax cookie. API/UAT clients use either
`Authorization: Bearer <token>` or `X-Internal-Access-Token: <token>`.
Authorized write responses are private/no-store, no-referrer, nosniff, and
denied framing. Public reads remain no-store. The cookie's client-side lifetime defaults to 12 hours; server-side
revocation is token rotation. Rotate the platform secret to revoke all
existing cookies and links immediately.

## 2. Persistent DB gate — do this before deploying

Mount a persistent volume at `/data` and set `DB_PATH=/data/kol.db`. Do not
deploy this image onto an uninspected existing volume. The entrypoint will not
delete or reseed an incompatible DB; it exits with a migration message.
`railway.json` configures `/healthz` as Railway's deploy-time health check;
Railway waits for HTTP 200 before activating the release. With an attached
volume, Railway cannot overlap old and new deployments, so expect a short
maintenance window and stop cockpit writes during the cutover.

On a staging clone of the production volume:

```bash
cd /app
PYTHONPATH=. python3 scripts/backup_db.py pre_release \
  --db /data/kol.db --backup-dir /data/backups
PYTHONPATH=. python3 scripts/check_runtime_db.py \
  --db /data/kol.db --runtime-contract
```

If the contract fails, stop. Preserve the backup and the old image. An older
Creator-only database needs a one-time migration on the staging clone using
the repository's migration scripts, followed by reconciliation, the runtime
contract, unit tests, and read-only UAT. Do not delete the production volume,
blindly copy `deploy_seed.db` over it, or run the startup scripts against the
only production copy: those actions could discard prior shortlist/review
writes. Promote the migrated DB only after comparing business-row inventory
and retaining a tested restore path.

The audited empty-volume seed contract is:

- 89 companies
- 29 operators
- 640 directional intro paths
- 42 action items
- 64 sponsorship observations

An existing compatible volume may contain more rows because Solomon's real
reviews and campaign work must persist. It may not contain fewer than the
audited baseline, missing direction payloads, unexplained unavailable
directions, integrity failures, or foreign-key violations.

## 3. Build and local container proof

Run from the repository root:

```bash
docker build -t mango-cockpit:release kol_database
docker run --rm -p 127.0.0.1:8811:8000 \
  -e DB_PATH=/data/kol.db \
  -v mango-cockpit-uat:/data \
  mango-cockpit:release
```

Binding only to loopback keeps this local runtime non-network-accessible, so
the normal local-open default is appropriate. The separate release E2E below
enables and proves the signed-link gate using a generated in-memory token; do
not put a token literal in a Docker command or shell history.

The normal runtime stage copies the seed from the validated `seedcheck` stage,
so BuildKit cannot skip the seed audit. `.dockerignore` excludes `.env`, live
DBs, backups, reports, tests, and caches. The image exposes `/healthz` through
a Docker health check and stores mutable state only on `/data`.

## 4. Release verification

Against a local disposable DB, run all three:

```bash
cd kol_database
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 scripts/run_solomon_uat.py \
  --base-url http://127.0.0.1:8811 --access-token "$INTERNAL_ACCESS_TOKEN"
PYTHONPATH=. python3 scripts/capture_uat_screenshots.py \
  --base-url http://127.0.0.1:8811 --access-token "$INTERNAL_ACCESS_TOKEN" --strict
PYTHONPATH=. python3 scripts/run_browser_interaction_smoke.py \
  --base-url http://127.0.0.1:8811 --access-token "$INTERNAL_ACCESS_TOKEN"
PYTHONPATH=. python3 scripts/run_release_browser_e2e.py
```

The interaction smoke intentionally refuses a non-loopback URL. The release
E2E goes further: it starts its own loopback server, copies `deploy_seed.db`
into a temporary directory, performs the full mutating UI workflow, proves the
normal DB and seed hash were untouched, and destroys the temporary DB. It has
no remote URL option by design. After deploy, only run the two read-only
commands (`run_solomon_uat.py` and `capture_uat_screenshots.py`) against HTTPS.
Never run POST/PATCH/DELETE smoke against production. The UAT client uses
certifi and verified TLS; certificate verification must never be disabled.

Review `browser-smoke.json`/`.md` for console errors, failed network requests,
404/5xx responses, broken images, stuck loading/error states, same-origin
broken links, and external-link markup/status. A third-party 401/403/429 is
reported separately as automation-blocked; it is not mislabeled as a 404.
X links are status-checked only and their content is not read—X research
continues to use Rapid X exclusively.

## 5. Rollback

Keep the previous image reference and the pre-release SQLite backup until UAT
sign-off. If rollback is needed, stop writes, run `restore_db.py --yes` against
the exact mounted `DB_PATH` (it creates another safety snapshot first), then
start the previous image with the same protected access boundary. Record the
image ID, DB backup filename/hash, migration output, and UAT report in the
release handoff; do not call a dirty local worktree a reproducible release.
