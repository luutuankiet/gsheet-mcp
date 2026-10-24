<!-- Copied from google-docs-mcp@2fb4652 docs/reference/deploying-to-cloud-run.md; renamed, cut down to the gsheet-mcp checklist, setup commands moved into scripts/deploy. -->
# Deploying gsheet-mcp to Cloud Run

Everything lives in `$GCP_PROJECT` and every `gcloud` call runs as
`gcpx exec $GCPX_ALIAS -p $GCP_PROJECT -- gcloud ...`, a wrapper that picks the
Google account per command. Both values come from `.env`. Anyone without
`gcpx` drops that prefix and adds `--project=$GCP_PROJECT`.

## What exists

| resource | name | why |
|---|---|---|
| Cloud Run service | `gsheet-mcp`, region `asia-southeast1` | 1 vCPU, 1 GiB, concurrency 20, timeout 300 s, `max-instances=1`: the per-user and per-spreadsheet locks live in one process |
| Firestore database | `gsheet-mcp-auth` | the auth store; a database of its own, collection prefix `gsheet_mcp__` |
| GCS bucket | `$GCP_PROJECT-gsheet-mcp-state` | scans, renders, pulls, plans and upload slots; objects deleted after one day; no "google" in the name, which GCS refuses |
| service account | `gsheet-mcp-run@$GCP_PROJECT.iam.gserviceaccount.com` | `datastore.user` on that database only, `storage.objectAdmin` on the bucket, token creator on itself for `signBlob` URLs |
| secret | `gsheet-mcp-google-client-secret` | the OAuth client secret, pinned by version number |
| OAuth client | `gsheet-mcp`, Web application | made by hand, below; stays in testing |

Override any name with the `GSHEET_DEPLOY_*` variables listed in `scripts/deploy --help`.

## 1. Look before you deploy

```
scripts/deploy --dry-run
```

Prints every command, runs none. Resources that already exist are skipped on
a real run; a dry run cannot ask, so it shows each check followed by the
create it would run if the check failed.

## 2. Deploy

```
scripts/deploy
```

In order: the release gate (`uv run pytest`, `uv run ruff check src tests`,
and `npm test` in `cli/` once that folder exists) stops the run on any
failure; then the APIs are enabled; the Firestore database, service account,
bucket (with the one-day rule) and secret are created if missing; the IAM
bindings are applied; and the service is deployed from source.

On the first deploy the client ID and secret are placeholders. The service
boots, and sign-in reaches Google, which answers `invalid_client` until step 3.

`.gcloudignore` is an allowlist, so `tmp/`, `.env` and local credentials are
never uploaded.

## 3. The OAuth client (Cloud console only)

The Web client, the consent screen and the redirect URI cannot be made from
`gcloud`. In the Cloud console, project `$GCP_PROJECT`, **Google Auth Platform**:

- [ ] **Branding** (first visit only; shared with any other server in the project): app name, support email, developer contact.
- [ ] **Audience**: user type External, publishing status **Testing**. Do not publish: in testing, the `drive` scope needs no security assessment. Add each person who will sign in under **Test users**.
- [ ] **Data access** → **Add or remove scopes**: exactly these four, no more:
  - [ ] `openid`
  - [ ] `.../auth/userinfo.email`
  - [ ] `https://www.googleapis.com/auth/spreadsheets`
  - [ ] `https://www.googleapis.com/auth/drive`
- [ ] **Clients** → **Create client** → type **Web application**, name `gsheet-mcp`.
- [ ] **Authorized redirect URIs**: exactly `<service URL>/auth/callback`, where the service URL is the one `scripts/deploy` printed at the end (`https://gsheet-mcp-<project number>.asia-southeast1.run.app`). No JavaScript origins.
- [ ] Copy the client ID and the client secret.

Then store the secret and point the service at the client:

```
printf '%s' '<CLIENT_SECRET>' | gcpx exec $GCPX_ALIAS -p $GCP_PROJECT -- \
  gcloud secrets versions add gsheet-mcp-google-client-secret --data-file=-

GSHEET_MCP_GOOGLE_CLIENT_ID=<CLIENT_ID> GSHEET_SECRET_VERSION=<version printed above> \
  scripts/deploy
```

- **Pin the secret to a version number, never `latest`.** The server's token
  signing key derives from the client secret, so instances on either side of a
  rotation would reject each other's tokens. Rotating it signs everyone out.
- Later deploys keep the client ID and secret version; pass them again only
  to change them.
- If Google says `Error 400: redirect_uri_mismatch`, the client lacks the exact
  redirect URI above; `Error 401: invalid_client` means the service still holds
  the placeholder or a client from another project.
- A custom domain changes the base URL: redeploy with
  `GSHEET_MCP_BASE_URL=https://<domain>` and add `<domain>/auth/callback` to the
  client's redirect URIs.

## 4. Check it is up

None of these need a sign-in:

| check | expect | proves |
|---|---|---|
| `curl -s <URL>/health` | `200`, `{"status":"ok",...}` | the container started and every setting passed the startup check |
| `curl -s <URL>/.well-known/oauth-protected-resource/mcp` | JSON whose `resource` is `<URL>/mcp` | `GSHEET_MCP_BASE_URL` is right |
| `curl -s -X POST <URL>/mcp -H 'content-type: application/json' -d '{}'` | `401` | sign-in is enforced |

Use `/health`, never `/healthz`: Cloud Run's front end answers `/healthz` itself with a 404.

## 5. Live smoke test

`tests/smoke/test_live_canary.py` is skipped unless `GSHEET_LIVE=1`. It copies
the canary through Drive, scans the copy, reads it, writes one cell through a
plan, checks that `spreadsheets.get` with ranges returns the shape the fake
Sheets API assumes and that the PDF export with `pages.PRINT_OPTIONS` is a
landscape letter page, then trashes the copy in a `finally` and checks it is
trashed. The canary itself is never written.

It runs the tool layer in process with a Google access token that carries the
`spreadsheets` and `drive` scopes:

```
GSHEET_LIVE=1 \
GSHEET_LIVE_TOKEN="$(gcpx exec $GCPX_ALIAS -p $GCP_PROJECT -- gcloud auth print-access-token)" \
CANARY_SHEET_ID=<from .env> \
GSHEET_LIVE_URL=<service URL> \
  uv run pytest tests/smoke -v
```

`GSHEET_LIVE_URL` is optional and adds the three no-sign-in checks above
against the deployed service. If the token lacks a scope, the copy fails with
`403 insufficient scopes`; get a token for an identity that has both.
