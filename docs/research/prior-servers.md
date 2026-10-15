---
title: What gsheet-mcp inherits from the Slides and Docs MCP servers
question: issue #2
verified: 2026-10-15
sources: $SLIDES_MCP_REPO at v2.6.0 plus one docs commit (checkout $SLIDES_MCP_DIR), $DOCS_MCP_REPO at its live build (checkout $DOCS_MCP_DIR), their closed issues
---

# What gsheet-mcp inherits from the Slides and Docs MCP servers

**Answer in one line:** copy the Docs MCP's build of the shared stack (HTTP only, Cloud Run, Firestore auth store, GCS state store, skill inside the package, fake API test seam, docs layout), swapping the scope to `spreadsheets`; adapt plans, receipts and scripts to cells; avoid stdio mode, a Drive scope, `structuredContent` on text replies, and the `/healthz` path.

Paths below are relative to each repo. `slides:` means `$SLIDES_MCP_DIR`, `docs:` means `$DOCS_MCP_DIR`. Issue numbers are on `$SLIDES_MCP_REPO` or `$DOCS_MCP_REPO` as marked. Line numbers are a starting point; confirm by what the code says.

## Carry-over table

| carry-over | copy / adapt / avoid | why | source file:line |
|---|---|---|---|
| Python + FastMCP 4 (capped below 5), stateless Streamable HTTP on Cloud Run | copy | Both servers run it in production; the cap exists because the server writes three fastmcp private fields, each guarded by a test | slides: `docs/architecture/packaging-and-release.md:60-73`; docs: spec `$DOCS_MCP_REPO#20` line 159 |
| HTTP mode only, no stdio, no local `token.json` | copy | The Docs MCP dropped stdio and tests with in-memory auth and state stores; half of slides' auth branches exist only for stdio | docs: `$DOCS_MCP_REPO#20` line 160; slides: `docs/architecture/auth-and-entry-points.md:92-100` |
| Multi-user Google sign-in through fastmcp's `GoogleProvider`; server token to the client, Google grant kept server side | copy | A leaked client token cannot be replayed against Google; one grant per caller | slides: `CONTEXT.md` (Server token, Google grant); `docs/architecture/auth-and-entry-points.md:74-90` |
| Caller credentials read from request context, never a parameter; mode fixed at startup | copy | Avoids threading credentials through every tool; a per-request mode guess could act as a stray `token.json` owner | slides: `docs/adr/0007-caller-credentials-from-request-context.md` |
| Copy worker-thread context into thread pools | copy | A plain thread pool loses the caller and raises `NotSignedInError`; receipts had to copy it by hand | slides: `docs/architecture/auth-and-entry-points.md:85-89` |
| Token validation cache on the sign-in proxy | copy | The proxy calls `tokeninfo` and `userinfo` on every request otherwise | slides: `docs/architecture/auth-and-entry-points.md:102-106`; `src/slides_mcp/http_mode.py:115` |
| Scopes `spreadsheets`, `openid`, `email`; no Drive scope | adapt | Docs MCP chose `documents`/`openid`/`email` so the OAuth app needs no security assessment; the same reasoning applies with `spreadsheets` | docs: `src/google_docs_mcp/http_mode.py:37-40`; `$DOCS_MCP_REPO#20` user story 3 |
| Load saved tokens without passing `SCOPES` | avoid (moot) | Only matters for refreshing a stored `token.json`; with HTTP only, the proxy refreshes. Keep the lesson: never narrow scopes on refresh (`invalid_scope`) | slides: `docs/architecture/auth-and-entry-points.md:43-59` |
| Auth store in a separate named Firestore database, collections prefixed with the server name | copy | Firestore IAM is per database, not per collection, and security rules do not apply to server code | slides: `docs/deploying-to-cloud-run.md:35-50`; `src/slides_mcp/http_mode.py:140-160` |
| State store: plans in Firestore with a TTL policy, short-lived files in a GCS bucket, signed URLs via IAM `signBlob`; in-memory store for tests | copy | Plans must survive across Cloud Run instances; signing needs no key file on Cloud Run | slides: `src/slides_mcp/state_store.py:17-20,68,98-104`; docs: `$DOCS_MCP_REPO#11` |
| Bucket lifecycle deletes every object after a day | copy | The bucket holds bundles, upload slots and images, nothing durable | docs: `$DOCS_MCP_REPO#20` lines 151, 271 |
| Scripts in embedded V8 (mini-racer), one worker process per call, parent owns CPU and wall timeouts | copy | mini-racer's eval timeout misses code after an `await`; a V8 crash must not kill the server. Threat model is accidents, not adversaries | slides: `docs/adr/0004-deck-scripts-in-embedded-v8.md`; `docs/architecture/deck-scripts.md:44-60` |
| All Google calls stay in the parent; the worker sees JSON only | copy | No token or network handle enters the isolate | slides: `docs/adr/0004-deck-scripts-in-embedded-v8.md` (Consequences) |
| Script model shaped like the read | adapt | Docs numbered script lines like `read_doc`; a sheet script should address cells by A1 the way reads show them, and see resolved formats | docs: `$DOCS_MCP_REPO#12`, `$DOCS_MCP_REPO#20` lines 253-267 |
| Commit phases inside a script | adapt | Slides needs phases because created ids feed later requests; Docs dropped them (styles only, one phase). Sheets writes are mostly values and formats, so start with one phase | slides: `docs/architecture/deck-scripts.md:80-98`; docs: `$DOCS_MCP_REPO#20` line 255 |
| Dry run by default and a `plan_id`: 1 hour, single use, one doc, one caller, same refusal message for every bad plan | copy | Both servers share the rules; one message for unknown, expired, foreign plans leaks nothing about other callers | slides: `docs/architecture/deck-scripts.md:105-128` |
| What an applied plan sends | adapt | Slides re-runs the script against the live deck; Docs sends exactly the stored requests against a pinned revision. Sheets has no `requiredRevisionId`, so pick consciously (see detail) | slides: `docs/architecture/deck-scripts.md:115-121`; docs: `$DOCS_MCP_REPO#20` line 171 |
| One write path for every write, with a destructive-kinds guard and an audit line | copy | Convenience tools, scripts and raw batches all pass one function, so one guard and one log | slides: `AGENTS.md` (Hard constraints); `docs/architecture/write-wedge.md:36-66` |
| Refuse obviously wrong units before sending | adapt | Slides refuses sizes under 1 pt because Google silently accepts inches as EMU. Sheets analogue: pixel sizes, zero-based grid indices vs A1 | slides: `docs/architecture/write-wedge.md:104-126` |
| Receipts: page renders opt-in, `receipt` off by default, pointer line on every write, max 3 pages, a failed render is a warning | copy (Docs flavour) | Slides attached thumbnails by default (about 480 tokens each); Docs made them opt-in at 384 px (about 150 tokens) with a pointer line | slides: `docs/architecture/write-wedge.md:68-102`; docs: `$DOCS_MCP_REPO#19`, `$DOCS_MCP_REPO#20` line 223 |
| Page renders from the editor's PDF export URL with the caller's bearer, rasterised by pypdfium2 | copy | Docs research proved a `spreadsheets` token gets a 200 PDF from `docs.google.com/spreadsheets/d/<id>/export?format=pdf` for a private sheet; no Drive scope | docs: `$DOCS_MCP_REPO#18` (Sheets symmetry test); `$DOCS_MCP_REPO#42` (Apps Script fallback) |
| "If you cannot see the image(s) below, say so" line beside every image | copy | ChatGPT received images intact yet invented their contents | slides: `docs/architecture/write-wedge.md:84-90`; `releases/v2.6.0.md` |
| Native image content, never base64 in text | copy | Standing preference in both maps | docs: `AGENTS.md` (Hard constraints) |
| Text replies use `content` only, never `structuredContent` | copy | Claude Code drops text blocks when `structuredContent` is present; FastMCP adds it to typed returns | docs: `docs/traps/TOOL_REPLY_TEXT_MISSING_IN_CLAUDE_CODE.md` |
| Register tools with `description=fn.__doc__` | copy | fastmcp otherwise moves `Args:` into schemas and drops `Returns:` | slides: `docs/architecture/packaging-and-release.md:60-65` |
| MCP tool annotations (read-only, destructive) on every tool | copy | ChatGPT badged every unannotated tool as destructive | slides: `$SLIDES_MCP_REPO#19`; `releases/v2.6.0.md` |
| Skill shipped through an `install_skill` tool, versioned with the server | copy | Remote teammates only have an endpoint URL | slides: `docs/adr/0005-skill-delivery-through-mcp-tool.md` |
| Skill folder inside the Python package | copy (Docs flavour) | Slides kept it top level and needed three allowlists plus a fallback path; Docs put it under `src/` so wheel, image and editable install all carry it | docs: `docs/adr/0003-skill-ships-inside-the-package.md` |
| Route agents to the skill from the raw-requests tool description and a one-line server `instructions` map, not from every tool | copy | Agents almost never call `install_skill` unprompted (0 calls in 4 claude.ai sessions); a nudge on every tool cost about 22 KB a session | slides: `docs/adr/0008-tools-tell-agents-to-load-the-skill.md` |
| Companion CLI: Node, `npx`, no credential, no login, `--help` is the whole manual | copy | The MCP client keeps the server token, so a shell cannot sign in; signed URLs carry every transfer | docs: `$DOCS_MCP_REPO#11`; `$DOCS_MCP_REPO#20` lines 239-251 |
| Working copies travel as bundles behind GCS signed URLs (`resource_link` out, signed PUT slot in) | adapt | Sandboxed clients allowlist only `storage.googleapis.com`. For sheets the payload is tables for DuckDB (Parquet or CSV), not Markdown, and push may never be needed | docs: `$DOCS_MCP_REPO#20` lines 172-173 |
| One bundle format with a version field, fixture bundles read by both the Python and Node codecs | copy | Keeps two codecs from drifting | docs: `docs/architecture/bundle-codec.md`; `$DOCS_MCP_REPO#20` line 281 |
| Self-describing local files (`*.gdoc.md`, line-1 marker) | adapt | A grep hit must reveal a remote copy; for DuckDB files, put source sheet, range and pull revision in a sidecar or table comment | docs: `$DOCS_MCP_REPO#17` |
| Fake API test seam: tools called in process against a fake that applies `batchUpdate` in memory; assert what the agent sees and the fake's final state, never a request list | copy | Both servers' main gate; lets the planner be rewritten freely | docs: `AGENTS.md` (Hard constraints), `$DOCS_MCP_REPO#20` lines 277-284; slides: `tests/fake_api.py` |
| Fixtures from a scrubbed recording of a real file | copy | Slides recorded four real slides plus hand-built edge cases | slides: `docs/architecture/deck-scripts.md:159-167` |
| Firestore emulator integration tests outside the CI gate | copy | Covers the hosted state store without cloud credentials | slides: `tests/integration/compose.yaml`; `AGENTS.md` (Commands) |
| Files copied from a prior server name their origin on line 1, listed in a provenance test | copy | A fix upstream can be ported by hand | docs: `tests/unit/test_provenance.py:11` |
| Docs layout: small `AGENTS.md`, `CONTEXT.md` glossary, `docs/{architecture,traps,reference,adr}`, generated `docs/README.md` via `scripts/gen-docs-index.sh` | copy | Same layout in both; traps indexed by symptom | slides: `docs/adr/0003-agent-docs-layout.md`; docs: `docs/adr/0001-docs-hold-facts-tickets-hold-decisions.md` |
| Decisions live in closed tickets, docs hold facts only | copy | Docs MCP rule; avoids restating decisions in two places | docs: `docs/adr/0001-docs-hold-facts-tickets-hold-decisions.md` |
| Release: tag-triggered workflow checks tag equals `pyproject.toml` version, tests, lint, requires hand-written `releases/vX.Y.Z.md`, PyPI via OIDC | adapt | Nothing gates on push to `main`. With HTTP only, the PyPI publish step may be dropped; Cloud Run deploys with `gcloud run deploy --source` | slides: `docs/architecture/packaging-and-release.md:22-39`; `docs/reference/cutting-a-release.md` |
| Health check route `/healthz` | avoid | Cloud Run's front end answers `/healthz` itself with an HTML 404; use `/health` | docs: `docs/traps/HEALTHZ_404_ON_CLOUD_RUN.md` |
| Fonts in the slim image | adapt | Slides needed `fonts-liberation2` because resvg drops text with no font; only matters if gsheet-mcp rasterises SVG itself | slides: `Dockerfile:13-18`; `docs/traps/svg-text-vanishes-in-slim-image.md` |
| Long tool descriptions | avoid | At least one client cuts long descriptions; Docs added a one-line layout hint in every reply as a backstop | slides: `$SLIDES_MCP_REPO#20` (open); docs: `$DOCS_MCP_REPO#20` user story 27 |
| Treat a quota error as a visible "retry later" refusal, no silent retry loop | copy | Quotas are shared across callers per project (Docs: 600 writes a minute; Slides thumbnails: 300 a minute per project) | docs: `$DOCS_MCP_REPO#20` line 272; slides: `docs/architecture/write-wedge.md:96-98` |

## Detail by area

### Deployment stack

Both servers run one container on Cloud Run built from a two-stage `Dockerfile` (uv sync from `uv.lock` into a venv, non-root user, `CMD [... "serve-http"]`), deployed with `gcloud run deploy --source .`, with `.dockerignore` and `.gcloudignore` written as allowlists (slides: `Dockerfile`, `docs/deploying-to-cloud-run.md`). The Docs MCP copied slides v2.6.0's HTTP sign-in, auth store, state store, skill bundle, CLI entry, script worker, Dockerfile, release workflow and docs index generator, renamed the env prefix, collection prefix and scopes, and stamped each copied file with its origin (`$DOCS_MCP_REPO#20` line 159). gsheet-mcp should copy from the Docs MCP rather than from slides, since it is the newer, HTTP-only cut of the same files.

Settings come from env vars checked at startup (`http_mode.check_settings`), and an unknown CLI word exits 2 rather than silently starting stdio, because on Cloud Run that only shows up as a failed health check (slides: `docs/architecture/auth-and-entry-points.md:21-25`). The Google OAuth client secret lives in Secret Manager with access granted to that one secret only (slides: `docs/deploying-to-cloud-run.md:136-149`).

Sizing: Docs starts at 1 GiB for in-process V8 plus pypdfium2 (about 40 MB a page) and says to measure (`$DOCS_MCP_REPO#20` line 273). gsheet-mcp adds DuckDB or big grids on the server only if it builds tables server side; if the CLI owns DuckDB, the server just streams values to a bundle.

### Auth and multi-user sign-in

The vocabulary in slides' `CONTEXT.md` is worth copying verbatim: server token (what the client holds), Google grant (held by the server, never sent), auth store (must outlive any instance; losing it signs everyone out), deployer (owns the OAuth app). The caller's access token is read from fastmcp's request context in one function; nothing is cached by token, because a wrong cache key would hand one caller's client to another (slides: `docs/adr/0007-caller-credentials-from-request-context.md`). Plans are bound to the caller's Google `sub` claim (slides: `docs/architecture/deck-scripts.md:107-113`).

### OAuth scopes

Slides asks for `presentations` and `drive.readonly` (slides: `src/slides_mcp/auth.py:26-28`); Docs narrowed to `documents`, `openid`, `email` (docs: `src/google_docs_mcp/http_mode.py:37-40`) and paid for it: no code block languages and no Drive Markdown export (`$DOCS_MCP_REPO#14`, `#15`). For gsheet-mcp, `spreadsheets` covers values, formats, filters, charts metadata and `batchUpdate`; the PDF export of a sheet works with a `spreadsheets` token (measured in `$DOCS_MCP_REPO#18`). What `spreadsheets` will not give: Drive comments, file search and creating a file in a folder. Whether a sheet's in-cell image bytes can be fetched without Drive is an open question for the read model ticket.

### State store

`state_store.py` has two implementations behind one interface: `MemoryStateStore` (tests) and `HostedStateStore` (Firestore collection `<prefix>__plans` with `expires_at` as a Firestore timestamp so a TTL policy deletes it, images in an optional bucket) (slides: `src/slides_mcp/state_store.py:68-104`). Plan TTL is 3600 s and signed image URLs live 300 s (lines 17, 20). Firestore TTL deletes within about a day, so code must still check `expires_at` itself (slides: `docs/deploying-to-cloud-run.md:165-173`). The Docs MCP widened it to hold bundles and 24-hour upload slots.

### Scripts in embedded V8

Copy the worker process unchanged: mini-racer, one child per call, JSON lines on stdin and stdout, kill as the only stop, 1 GB memory cap, about 0.1 s start-up (slides: `docs/architecture/deck-scripts.md:44-60`). Rejected alternatives and why are in slides `docs/adr/0004-deck-scripts-in-embedded-v8.md`; do not reopen them. Error kinds worth reusing: `syntax`, `script` (line and column in the script's numbering), `memory`, `timeout`, `validation`, `limit`, `destructive`, `api` (with request index), `auth`, `plan` (slides: `docs/architecture/deck-scripts.md:144-157`). Note the Docs MCP also runs a second, in-process V8 for its diagram expander; that is not the script sandbox (`$DOCS_MCP_REPO#20` line 168). Bytes cross mini-racer's Python boundary only as base64 text from Python to JS (docs: `docs/reference/mini-racer-bytes.md`).

For sheets, the interesting adaptation is the model: a script should see a tab as a grid addressed in A1, with values, formulas, effective formats, merges, frozen and hidden state, and write through helpers that emit `batchUpdate` requests. The Docs reply format (changes grouped by kind with counts and up to five samples) is the right shape for a bulk format sweep (`$DOCS_MCP_REPO#20` lines 258-267).

### Dry run and plan_id

Shared rules: dry run is the default for scripts and raw batches; a successful dry run returns `plan_id`; the plan is single use, one file, one caller, one hour; a refused or failed apply leaves it for a retry; `confirm_destructive` must be given again on apply (slides: `docs/architecture/deck-scripts.md:105-128`).

The fork: slides re-runs the script against the live deck at apply time and lets receipts verify; Docs stores the computed requests and sends them against the pinned revision with `writeControl.requiredRevisionId`. **The Sheets API has no revision guard on `spreadsheets.batchUpdate`**, so gsheet-mcp cannot copy the Docs behaviour as is. Options for the write model ticket: re-run like slides, or store requests plus a fingerprint of the target ranges and refuse when it changed (Docs used a fingerprint for its false-stale retry, docs: `docs/reference/doc-script-requests.md`).

### Receipts and page renders

Slides attaches up to 3 thumbnails by default after every slide-changing write, fetched concurrently, each tried once, a `429` never retried, and shares a quota of 60 a minute per user and 300 per project with `render_thumbnail` (slides: `docs/architecture/write-wedge.md:68-102`). Docs reversed the default: every write ends with a pointer line naming the changed lines and how to render them; `receipt` is `off`, `minimal` (384 px, about 150 tokens a page) or `low`; images are labelled with the lines they hold; dry runs never render (`$DOCS_MCP_REPO#19`). Copy the Docs flavour, labelling each render with the tab and cell range it shows.

Docs renders through `docs.google.com/document/d/<id>/export?format=pdf&tab=<tabId>`, checks the tab id first because an unknown id silently returns the first tab, and maps pages to lines through the PDF text layer (`$DOCS_MCP_REPO#20` line 175). The sheet export URL takes range, gridline and fit parameters, which makes a cell-range render plausible; that is for the render ticket to verify. `$DOCS_MCP_REPO#42` is open: some private docs refuse the export with the caller's token and need an Apps Script fallback, so plan for that failure.

### Skill shipping

`install_skill` returns every file of the skill folder with paths, the version and install steps (slides: `docs/adr/0005-skill-delivery-through-mcp-tool.md`). Keep the folder under the package (docs: `docs/adr/0003-skill-ships-inside-the-package.md`) with a test that the built wheel carries it. Route to it from the raw `exec_batch_update` description, which also carries the raw-request rules agents must see, and a one-line server `instructions` map (slides: `docs/adr/0008-tools-tell-agents-to-load-the-skill.md`). Each tool call logs one line (tool, duration, outcome) so whether agents load the skill can be measured.

### Companion CLI and how working copies travel

The Docs CLI is Node with no dependencies, published as `@luutuankiet/gdoc`, released in lockstep with the server, and holds no credential: every transfer is a GCS signed URL handed over in a tool reply (`$DOCS_MCP_REPO#11`). Pull returns a `resource_link` to a signed GET plus the next signed PUT slot; push takes the upload handle, or none to get a fresh slot; pull with `base_revision` answers "up to date" without building a bundle. Local state sits in a hidden `.gdoc/` folder (manifest, base copies). The `--help` output is the whole manual, and the pull and push tool descriptions name `npx ... --help` as the first step.

For gsheet-mcp the map asks for structured tables loaded into DuckDB, joined across tables and tabs. That changes the payload (typed columns, not Markdown) and likely the direction: pull only for the MVP, with writes going back through live tools rather than a push diff. Keep the transport, the no-credential CLI, the format version and the shared fixtures.

### Testing with a fake API

Slides: `tests/fake_api.py` serves a scrubbed recording of four real slides plus hand-built edge cases and applies the request kinds tests send; receipts tests use the same fake, which records thumbnails asked for and can be told to fail (slides: `docs/architecture/deck-scripts.md:159-167`, `docs/architecture/write-wedge.md:266-274`). Docs: `FakeDocs` honours the revision guard, UTF-16 index shifts and named ranges; tests go through the MCP tool layer with a `client` fixture and assert only what the agent sees and the fake doc's final state (docs: `AGENTS.md`). A `FakeSheets` should apply `batchUpdate` to a grid model built from a scrubbed `spreadsheets.get` recording of the reference sheet, with a fake export endpoint serving fixture PDFs. Live smoke tests against a copy of the canary stay opt-in.

Lesson from both live tests: much Google behaviour was "only modelled in FakeDocs", and each such page lists the manual check that would verify it (docs: `docs/reference/pull-push-live-checks.md`, `docs/reference/tables-footnotes-rules.md`). Record a real response before modelling it in the fake.

### Docs layout

Identical in both repos: `AGENTS.md` with hard constraints, layout, commands and the standard "Before you wrap up" block; `CONTEXT.md` as glossary only; `docs/architecture/` (one page per area, `verified:` date), `docs/traps/` (symptom-indexed, filename is an identifier), `docs/reference/`, `docs/adr/` (append-only); `docs/README.md` generated by `scripts/gen-docs-index.sh`, with `--check` for staleness. Docs adds that research notes live on `research/*` branches linked from tickets, which is what this file is.

### Release process

Slides: tag `vX.Y.Z` triggers build (tag equals `pyproject.toml` version, tests, ruff with `W` rules so trailing whitespace fails), release (requires `releases/vX.Y.Z.md`, used verbatim), and PyPI publish with OIDC and `skip-existing` (slides: `docs/architecture/packaging-and-release.md:22-39`). Nothing gates on push to `main`, so run tests and lint before tagging (slides: `docs/reference/cutting-a-release.md`). An HTTP-only server has no PyPI audience, so the Docs-style flow (tag, release notes, `gcloud run deploy --source`, plus an npm publish of the CLI in lockstep) fits better.

### Token economy in tool descriptions and replies

- **Reads are projections, not API JSON.** Slides measured Google's own Slides MCP at 4 to 14 times the tokens per slide of its reads even with a field mask (about 130 times unmasked), which is why it keeps self-hosting (slides: `docs/adr/0006-keep-self-hosting-over-google-slides-mcp.md`). Slides offers four detail levels; Docs renders Markdown with line numbers.
- **Outline first.** Both start with an outline tool so an agent picks what to read (`get_deck_outline`, `get_doc_outline`).
- **Headers that let the agent stop.** Docs reads default to 2,000 lines, cut lines over 2,000 characters with `…[+N chars]`, and print `lines 1-2000 of 5,312 (~68k tokens)` (`$DOCS_MCP_REPO#20` stories 13-15). Grep caps at 50 hits, trims lines to about 80 characters and says `+N more in <tab>` (stories 29-34).
- **Writes return verification in the same reply.** Slides returns post-write state (outline plus touched slides, `post_state` levels) so no second read is needed (slides: `docs/adr/0002-legwork-write-wedge.md`); Docs returns the new revision and a pointer line.
- **Images on request, at chosen quality.** Docs reads attach images automatically only when five or fewer are in range, with `low` 512, `standard` 1024, `high` 1568 px.
- **Descriptions stay short, rules go where they are needed.** Raw-request rules live only on `exec_batch_update`; worked examples live in the skill; a one-line hint in each reply covers clients that cut descriptions (slides: `docs/adr/0008-tools-tell-agents-to-load-the-skill.md`; `$SLIDES_MCP_REPO#20`).
- **Text, not `structuredContent`,** for anything the model must read (docs: `docs/traps/TOOL_REPLY_TEXT_MISSING_IN_CLAUDE_CODE.md`).

### Traps and lessons worth carrying

| lesson | source |
|---|---|
| Google often answers a request that did nothing with HTTP 200 (an empty replace reply, a `CUSTOM` shape that draws nothing, an unknown font stored verbatim). Check effects by reading back, not by status | slides: `docs/traps/` (three pages) |
| An unknown tab id on the export URL silently returns the first tab; validate ids first | docs: `$DOCS_MCP_REPO#20` line 175 |
| `documents.get` without `includeTabsContent` returns only the first tab. Check the Sheets equivalent (`includeGridData`, ranges) for the same silent truncation | docs: `docs/traps/ONLY_FIRST_TAB_RETURNED.md` |
| Never narrow `SCOPES` on refresh: Google rejects with `invalid_scope` | slides: `docs/architecture/auth-and-entry-points.md:50-55` |
| `/healthz` is Cloud Run's; an HTML 404 with no log line came from Google's front end | docs: `docs/traps/HEALTHZ_404_ON_CLOUD_RUN.md` |
| Web agents rarely load a skill unprompted; put the must-know rules in the one description that needs them | slides: `$SLIDES_MCP_REPO#19`, ADR 0008 |
| Tools with no MCP annotations get badged destructive in ChatGPT | slides: `$SLIDES_MCP_REPO#19` |
| A model can receive an image and still invent its contents; ask it to say so | slides: `docs/architecture/write-wedge.md:84-90` |
| The Google API client's HTTP transport is not thread safe; build one client per thread | slides: `docs/architecture/auth-and-entry-points.md:61-68` |
| Escaping: Google's own Markdown export escapes underscores; render yourself and escape only where meaning changes | docs: `docs/traps/UNDERSCORES_COME_BACK_ESCAPED.md` |
| Live checks found four follow-ups after a fake-only build (`$DOCS_MCP_REPO#39` to `#42`); budget a live test pass before calling the build done | docs: `$DOCS_MCP_REPO#1` closing comment |

## Gaps in this research

- The Slides MCP has no local issue store under `tmp/issues` in `$SLIDES_MCP_DIR` (only `tmp/20261010/auth-diagram/`); its tickets were read from GitHub instead (issues 11 to 27).
- Nothing here was run against the Sheets API. Sheets-specific claims (no revision guard on `batchUpdate`, export URL parameters) come from knowledge of the API and the Docs MCP's Sheets symmetry test, and belong to the read model, write model and render tickets to verify.
- Raw issue dumps used for this page are in the main checkout under `tmp/20261015/prior-servers/issues/`, produced by `tmp/20261015/prior-servers/fetch_issues.sh`.
