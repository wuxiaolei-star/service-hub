# Service Hub Web Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Chinese, light-blue Web management console that operates the existing Service Hub plugin, file, and Job workflows and ships with the backend through one Docker Compose project.

**Architecture:** A React/TypeScript SPA is built into a dedicated Nginx image. Nginx serves browser routes and proxies public `/api/v1` traffic to the existing single-container backend while denying `/internal/v1`; three minimal read APIs and Job timestamps are added to support a complete management view.

**Tech Stack:** React 18, TypeScript, Vite, Ant Design 5, React Router, TanStack Query, Axios, Vitest, Testing Library, Nginx, Docker Compose, FastAPI, Pydantic V1 style, SQLAlchemy.

**Spec:** `docs/superpowers/specs/2026-09-13-service-hub-web-console-design.md`

## Global Constraints

- Work only on `feature/service-hub-web-console`; never commit feature work directly to `main`.
- Preserve the existing plugin package, Job Runtime, Runner Event, conda-pack, and Docker execution protocols.
- Browser code calls only relative `/api/v1` URLs and never calls `/internal/v1`.
- Web container never mounts `/data` or `/var/run/docker.sock`.
- Backend remains `python-service-hub:1.0.0-linux-amd64`; Web image is `python-service-hub-web:1.0.0-linux-amd64`.
- Production Web binds `127.0.0.1:8080`; backend diagnostic port remains loopback-only on `127.0.0.1:8000`.
- Server-supplied filenames, errors, and logs render as text; do not use `dangerouslySetInnerHTML`.
- Frontend persistence may contain UI preferences and recent public file IDs only; it must never contain Runner tokens or file payloads.
- Use strict TypeScript, accessible labels, keyboard-operable actions, and Chinese user-facing text.
- Every implementation task follows test-first RED/GREEN, focused verification, and a dedicated commit.
- Preserve untracked root artifacts such as `service-hub-image.tar` and `service-hub-source.tar`.

---

### Task 1: Add the minimal management read APIs

**Files:**

- Modify: `packages/hub-server/src/hub_server/schemas.py`
- Modify: `packages/hub-server/src/hub_server/routers/plugins.py`
- Modify: `packages/hub-server/src/hub_server/routers/files.py`
- Modify: `packages/hub-server/src/hub_server/routers/jobs.py`
- Modify: `tests/server/test_plugins_api.py`
- Modify: `tests/server/test_files_api.py`
- Modify: `tests/server/test_jobs_api.py`

**Interfaces:**

- Produces `GET /api/v1/plugins/{plugin_id}` returning `PluginDetailResponse`.
- Produces `GET /api/v1/plugin-builds?plugin_id=...` returning `PluginBuildListResponse`.
- Produces `GET /api/v1/files` returning `FileListResponse` with at most 100 newest records.
- Extends `FileResponse` with `created_at: datetime`.
- Extends `JobResponse` with `created_at`, `started_at`, and `finished_at`.
- Preserves every existing request and response field.

- [ ] **Step 1: Write failing plugin detail and Build list API tests**

Create records through the existing install flow, then assert:

```python
detail = client.get("/api/v1/plugins/nc_to_shp")
assert detail.status_code == 200
payload = detail.json()
assert payload["id"] == "nc_to_shp"
assert payload["versions"][0]["version"] == "1.0.0"
assert payload["versions"][0]["manifest"]["entrypoint"]["function"] == "run"

builds = client.get("/api/v1/plugin-builds", params={"plugin_id": "nc_to_shp"})
assert builds.status_code == 200
assert builds.json()["items"][0]["plugin_id"] == "nc_to_shp"
```

Also assert an unknown plugin returns the standard `PLUGIN_NOT_FOUND` error and that list filtering excludes other plugins.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m pytest tests/server/test_plugins_api.py -q
```

Expected: 404/validation failure because the two read routes and schemas do not exist.

- [ ] **Step 3: Add explicit Pydantic response schemas**

Add:

```python
class PluginVersionDetail(BaseModel):
    version: str
    spec_version: str
    sdk_version: str
    source_sha256: str
    status: str
    manifest: dict[str, object]


class PluginDetailResponse(PluginSummary):
    author: str | None = None
    versions: list[PluginVersionDetail]


class PluginBuildListResponse(BaseModel):
    items: list[PluginBuildResponse]


class FileListResponse(BaseModel):
    items: list[FileResponse]
```

Use timezone-aware `datetime` fields for file and Job timestamps. Do not return `package_path`, `runtime_archive_path`, environment path, workspace path, or internal metadata JSON.

- [ ] **Step 4: Implement the plugin read queries**

Use SQLAlchemy `selectinload` for versions/builds, sort versions and Builds deterministically, cap Build results at 100, and apply `plugin_id` filtering through a join. Reuse `_build_response` rather than duplicating mappings.

- [ ] **Step 5: Write failing file list and Job timestamp tests**

```python
files = client.get("/api/v1/files")
assert files.status_code == 200
assert files.json()["items"][0]["created_at"].endswith("Z")

job = client.get(f"/api/v1/jobs/{job_id}").json()
assert job["created_at"]
assert job["started_at"] is None
assert job["finished_at"] is None
```

Assert file results are newest-first and limited to 100.

- [ ] **Step 6: Implement list files and timestamp mappings**

Query `FileRecord` by `created_at.desc()` with `.limit(100)`. Add timestamps in `_file_response` and `_job_response`. Serialize using Pydantic/FastAPI UTC ISO 8601 behavior.

- [ ] **Step 7: Verify server API compatibility**

Run:

```bash
python -m pytest tests/server/test_plugins_api.py tests/server/test_files_api.py tests/server/test_jobs_api.py -q
python -m ruff check packages/hub-server/src/hub_server tests/server
python -m mypy packages/hub-server/src/hub_server
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/hub-server/src/hub_server tests/server
git commit -m "feat(server): expose web console read models"
```

---

### Task 2: Scaffold the typed Web application and test harness

**Files:**

- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/tsconfig.json`
- Create: `web/tsconfig.node.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/src/test/setup.ts`
- Create: `web/src/theme/theme.ts`
- Create: `web/src/styles.css`
- Create: `web/src/layouts/AppLayout.tsx`
- Create: `web/src/routes/router.tsx`
- Create: `web/src/pages/NotFoundPage.tsx`
- Create: `web/src/layouts/AppLayout.test.tsx`

**Interfaces:**

- Produces npm scripts `dev`, `build`, `test`, `test:run`, `lint`, and `typecheck`.
- Produces `appTheme` with primary `#3B82F6`, background `#F4F8FD`, border `#DCE8F5`, and radius 8.
- Produces router paths `/`, `/plugins`, `/files`, `/jobs/new`, `/jobs`, `/jobs/:jobId`, `/system`, and fallback `*`.
- Vite proxies `/api` to `VITE_DEV_PROXY_TARGET`, default `http://127.0.0.1:8000`.

- [ ] **Step 1: Add package metadata and deterministic dependencies**

Use Node `>=20`. Runtime dependencies: React 18, React DOM 18, Ant Design 5, icons, React Router 6, TanStack Query 5, and Axios 1. Development dependencies: TypeScript 5, Vite, React plugin, Vitest, jsdom, ESLint, types, and Testing Library. Generate and commit `package-lock.json` with `npm install`.

- [ ] **Step 2: Write the failing layout test**

```tsx
render(<App />)
expect(screen.getByText("Service Hub")).toBeInTheDocument()
expect(screen.getByRole("navigation")).toBeInTheDocument()
expect(screen.getByText("概览")).toBeInTheDocument()
```

- [ ] **Step 3: Run the test and verify RED**

Run `npm test -- --run src/layouts/AppLayout.test.tsx` from `web/`.
Expected: FAIL because application files are absent.

- [ ] **Step 4: Implement theme, providers, router, and shell**

`App.tsx` owns `ConfigProvider`, `QueryClientProvider`, and `RouterProvider`. `AppLayout` renders collapsible navigation, a top health placeholder, `<Outlet />`, and selected menu state derived from the route. Page route elements may be minimal named placeholders in this task.

- [ ] **Step 5: Add global CSS**

Set a system Chinese font stack, minimum 1280-oriented layout, responsive collapsed sidebar below 992px, visible focus styles, card borders, and no broad animation. Do not import remote fonts.

- [ ] **Step 6: Verify the scaffold**

```bash
cd web
npm run test:run
npm run typecheck
npm run lint
npm run build
```

Expected: PASS and `web/dist/index.html` exists.

- [ ] **Step 7: Commit**

```bash
git add web
git commit -m "feat(web): scaffold service hub console"
```

---

### Task 3: Build the typed API client and query primitives

**Files:**

- Create: `web/src/types/api.ts`
- Create: `web/src/api/client.ts`
- Create: `web/src/api/errors.ts`
- Create: `web/src/api/system.ts`
- Create: `web/src/api/plugins.ts`
- Create: `web/src/api/files.ts`
- Create: `web/src/api/jobs.ts`
- Create: `web/src/hooks/queryKeys.ts`
- Create: `web/src/hooks/polling.ts`
- Create: `web/src/api/client.test.ts`
- Create: `web/src/hooks/polling.test.ts`

**Interfaces:**

- Produces `HubApiError { code: string; message: string; details?: Record<string, unknown>; status?: number }`.
- Produces one Axios client with `baseURL: "/api/v1"`.
- Produces typed functions for every public endpoint used by the seven pages.
- Produces `pollWhileBuildActive(status)` and `pollWhileJobActive(status)` returning `2000 | false`.

- [ ] **Step 1: Write error parsing and polling tests**

Assert the standard `{success:false,error:{code,message,details}}` envelope becomes `HubApiError`, network failures become code `NETWORK_ERROR`, and only `INSTALLING`, `PENDING`, `PREPARING`, `RUNNING`, and `CANCEL_REQUESTED` poll.

- [ ] **Step 2: Run tests and verify RED**

Run `npm run test:run -- src/api/client.test.ts src/hooks/polling.test.ts`.
Expected: FAIL because modules are absent.

- [ ] **Step 3: Define exact API types**

Mirror Task 1 schemas. Include `RuntimeType = "conda-pack" | "docker"`, all Job states, nullable ISO timestamp strings, `PluginManifest` parameter/input/output structures, and paged wrappers with `items`.

- [ ] **Step 4: Implement the shared client and endpoint modules**

Use relative URLs only. Upload functions accept `File` and optional `onProgress`; downloads request `blob`. Do not expose a caller-selectable host. Encode all path IDs with `encodeURIComponent`.

- [ ] **Step 5: Implement query keys and polling predicates**

Keys must include filters, IDs, cursors, and versions so cache entries cannot collide. Stop polling on terminal states and on component unmount through TanStack Query.

- [ ] **Step 6: Verify**

```bash
cd web
npm run test:run -- src/api src/hooks
npm run typecheck
npm run lint
```

- [ ] **Step 7: Commit**

```bash
git add web/src/api web/src/types web/src/hooks
git commit -m "feat(web): add typed service hub api client"
```

---

### Task 4: Implement shared status, error, upload, and download components

**Files:**

- Create: `web/src/components/StatusTag.tsx`
- Create: `web/src/components/HubErrorAlert.tsx`
- Create: `web/src/components/UploadPanel.tsx`
- Create: `web/src/components/PageHeader.tsx`
- Create: `web/src/components/EmptyState.tsx`
- Create: `web/src/utils/format.ts`
- Create: `web/src/utils/download.ts`
- Create: `web/src/components/StatusTag.test.tsx`
- Create: `web/src/components/HubErrorAlert.test.tsx`
- Create: `web/src/utils/download.test.ts`

**Interfaces:**

- Produces `StatusTag({status})` with one centralized status/color map.
- Produces `HubErrorAlert({error, onRetry?})` with collapsed details.
- Produces `UploadPanel({accept, beforeUpload, uploading, progress})`.
- Produces `safeDownloadName(name, extension, fallback): string` and `saveBlob(blob, filename): void`.

- [ ] **Step 1: Write failing status/error/filename tests**

Cover every Build/Job status; assert error message and code are visible while details are collapsed; reject `../x`, separators, control characters, empty names, and duplicate extension.

- [ ] **Step 2: Run and verify RED**

Run `npm run test:run -- src/components src/utils`.

- [ ] **Step 3: Implement pure utilities and components**

Use Ant Design `Tag`, `Alert`, `Progress`, and `Upload.Dragger`. Render server strings through React text nodes only. `saveBlob` creates and revokes an object URL in `finally`.

- [ ] **Step 4: Verify accessibility and build**

```bash
cd web
npm run test:run -- src/components src/utils
npm run typecheck
npm run lint
npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/src/components web/src/utils
git commit -m "feat(web): add console interaction components"
```

---

### Task 5: Implement overview and system pages

**Files:**

- Create: `web/src/pages/DashboardPage.tsx`
- Create: `web/src/pages/SystemPage.tsx`
- Create: `web/src/pages/DashboardPage.test.tsx`
- Create: `web/src/pages/SystemPage.test.tsx`
- Modify: `web/src/layouts/AppLayout.tsx`
- Modify: `web/src/routes/router.tsx`

**Interfaces:**

- Consumes Task 3 system/plugin/Build/Job query functions.
- Produces dashboard cards and recent Job table.
- Produces top-bar Hub health polling every 10 seconds.
- Produces read-only system information and unauthenticated-mode warning.

- [ ] **Step 1: Write Mock API page tests**

Assert health `UP`, plugin/Build counts, status aggregation, recent Job rows, loading skeletons, network error retry, architecture, Python version, deployment mode, and security warning.

- [ ] **Step 2: Run and verify RED**

Run `npm run test:run -- src/pages/DashboardPage.test.tsx src/pages/SystemPage.test.tsx`.

- [ ] **Step 3: Implement dashboard and system pages**

Calculate counts with memoized pure functions. Use the existing 100-item Job list as “recent,” clearly label it rather than implying a full historical statistic. Top health indicator must show unknown/offline states without crashing the layout.

- [ ] **Step 4: Verify**

```bash
cd web
npm run test:run -- src/pages/DashboardPage.test.tsx src/pages/SystemPage.test.tsx
npm run typecheck
npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages web/src/layouts web/src/routes
git commit -m "feat(web): add dashboard and system status"
```

---

### Task 6: Implement plugin management

**Files:**

- Create: `web/src/pages/PluginsPage.tsx`
- Create: `web/src/pages/PluginDetailDrawer.tsx`
- Create: `web/src/hooks/usePluginInstall.ts`
- Create: `web/src/pages/PluginsPage.test.tsx`
- Modify: `web/src/routes/router.tsx`

**Interfaces:**

- Consumes plugin list/detail, Build list/detail, install, enable, and disable APIs.
- Produces `.pypkg` upload with progress and Build terminal-state polling.
- Produces Build enable/disable actions with cache invalidation.

- [ ] **Step 1: Write the page workflow test**

Simulate selecting `plugin.pypkg`, upload progress, response `INSTALLING`, polling to `READY`, enable to `ENABLED`, viewing manifest parameters, disabling back to `READY`, and displaying a failed Build error summary.

- [ ] **Step 2: Run and verify RED**

Run `npm run test:run -- src/pages/PluginsPage.test.tsx`.

- [ ] **Step 3: Implement the install hook**

Store only the returned public `build_id`, poll `GET /plugin-builds/{id}` every two seconds while `INSTALLING`, stop at `READY`, `ENABLED`, or `FAILED`, and invalidate plugin/Build lists after terminal transition.

- [ ] **Step 4: Implement plugin table and detail drawer**

Group Builds by version/runtime/platform. Use confirmation for disable, disable actions while mutation is pending, and never infer Build status from upload completion alone.

- [ ] **Step 5: Verify**

```bash
cd web
npm run test:run -- src/pages/PluginsPage.test.tsx
npm run typecheck
npm run lint
```

- [ ] **Step 6: Commit**

```bash
git add web/src/pages web/src/hooks web/src/routes
git commit -m "feat(web): add plugin build management"
```

---

### Task 7: Implement file management and recent-file persistence

**Files:**

- Create: `web/src/pages/FilesPage.tsx`
- Create: `web/src/hooks/useRecentFiles.ts`
- Create: `web/src/pages/FilesPage.test.tsx`
- Create: `web/src/hooks/useRecentFiles.test.ts`
- Modify: `web/src/routes/router.tsx`

**Interfaces:**

- Consumes file list/upload/metadata/download APIs.
- Produces `useRecentFiles` storing at most 50 public metadata entries under `service-hub.recent-files.v1`.
- Produces upload, lookup-by-ID, copy-ID, and safe download interactions.

- [ ] **Step 1: Write recent-file and page tests**

Assert JSON corruption is ignored, records are de-duplicated by `file_id`, list is capped at 50, no blob data is stored, uploads report progress, lookup failures preserve existing records, and downloads use safe names.

- [ ] **Step 2: Run and verify RED**

Run `npm run test:run -- src/hooks/useRecentFiles.test.ts src/pages/FilesPage.test.tsx`.

- [ ] **Step 3: Implement persistence and page**

Merge server list and recent client metadata by `file_id`; server data wins. Clearly label the list as the most recent 100 server records. Use clipboard API with visible success/failure feedback.

- [ ] **Step 4: Verify**

```bash
cd web
npm run test:run -- src/hooks/useRecentFiles.test.ts src/pages/FilesPage.test.tsx
npm run typecheck
npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add web/src/pages web/src/hooks web/src/routes
git commit -m "feat(web): add file upload and download management"
```

---

### Task 8: Implement Manifest-driven Job creation

**Files:**

- Create: `web/src/pages/NewJobPage.tsx`
- Create: `web/src/components/PluginParameterForm.tsx`
- Create: `web/src/utils/jobPayload.ts`
- Create: `web/src/utils/jobPayload.test.ts`
- Create: `web/src/pages/NewJobPage.test.tsx`
- Modify: `web/src/routes/router.tsx`

**Interfaces:**

- Produces `buildJobRequest(manifest, values): JobCreateRequest`.
- Supports manifest parameter types currently accepted by V1: string, integer, number, boolean, string_list, and file inputs.
- Produces form and JSON modes with the same validated request preview.

- [ ] **Step 1: Write payload validation tests**

Cover required/default/min/max/options validation, repeated file inputs, invalid JSON, unsupported parameter types, `runtime_type`, and exact NC payload:

```json
{
  "plugin_id": "nc_to_shp",
  "version": "1.0.0",
  "runtime_type": "docker",
  "inputs": {"source_nc": "file_123"},
  "params": {"group_name": "1", "metrics": ["depth", "stage"]}
}
```

- [ ] **Step 2: Run and verify RED**

Run `npm run test:run -- src/utils/jobPayload.test.ts src/pages/NewJobPage.test.tsx`.

- [ ] **Step 3: Implement the parameter form**

Render controls from the selected version Manifest, select only `ENABLED` Builds, constrain runtime to available Builds, populate defaults, and let operators select recent files or enter a file ID. Unsupported definitions render a blocking explanatory error rather than silently dropping data.

- [ ] **Step 4: Implement JSON mode and submit flow**

Keep one canonical request object. Validate JSON as an object, display the request preview, prevent double submit, preserve values on errors, and navigate to `/jobs/{job_id}` on success.

- [ ] **Step 5: Verify**

```bash
cd web
npm run test:run -- src/utils/jobPayload.test.ts src/pages/NewJobPage.test.tsx
npm run typecheck
npm run lint
```

- [ ] **Step 6: Commit**

```bash
git add web/src/pages web/src/components web/src/utils web/src/routes
git commit -m "feat(web): add manifest driven job creation"
```

---

### Task 9: Implement Job center, detail polling, logs, cancellation, and outputs

**Files:**

- Create: `web/src/pages/JobsPage.tsx`
- Create: `web/src/pages/JobDetailPage.tsx`
- Create: `web/src/components/JobLogViewer.tsx`
- Create: `web/src/hooks/useJobLogs.ts`
- Create: `web/src/utils/jobTime.ts`
- Create: `web/src/pages/JobsPage.test.tsx`
- Create: `web/src/pages/JobDetailPage.test.tsx`
- Create: `web/src/hooks/useJobLogs.test.ts`
- Modify: `web/src/routes/router.tsx`

**Interfaces:**

- Consumes Job list/detail/log/cancel/output and file download APIs.
- Produces incremental `useJobLogs(jobId)` cursor accumulation without duplicates.
- Produces `formatDuration(createdAt, startedAt, finishedAt, now): string`.

- [ ] **Step 1: Write Job time and log cursor tests**

Assert queued/running/finished durations, invalid timestamp fallback, ordered event accumulation, duplicate cursor suppression, terminal stop, pause/resume auto-scroll, and unmount cancellation.

- [ ] **Step 2: Write page workflow tests**

Cover status polling, details tabs, cancel confirmation, failure summary, output fetch only after `SUCCESS`, safe download, empty logs/outputs, and direct route refresh.

- [ ] **Step 3: Run and verify RED**

Run `npm run test:run -- src/hooks/useJobLogs.test.ts src/pages/JobsPage.test.tsx src/pages/JobDetailPage.test.tsx`.

- [ ] **Step 4: Implement Job list and duration utilities**

Show the newest 100 returned Jobs, derive durations without mutating server data, use two-second refresh only when any row is active, and include accessible status text.

- [ ] **Step 5: Implement detail, logs, cancel, and download**

Use independent query keys for Job, logs, and outputs. Only request outputs after `SUCCESS`; render logs as `<pre>`/text nodes; retain the last cursor; stop every poll at terminal state.

- [ ] **Step 6: Verify the complete Web suite**

```bash
cd web
npm run test:run
npm run typecheck
npm run lint
npm run build
```

- [ ] **Step 7: Commit**

```bash
git add web/src
git commit -m "feat(web): add job monitoring and outputs"
```

---

### Task 10: Package the Web SPA with Nginx and Compose

**Files:**

- Create: `web/Dockerfile`
- Create: `web/nginx.conf`
- Create: `web/.dockerignore`
- Modify: `compose.yaml`
- Modify: `tests/server/test_container_smoke.py`
- Create: `tests/deploy/test_web_container_files.py`

**Interfaces:**

- Produces image `python-service-hub-web:1.0.0-linux-amd64`.
- Produces Compose services exactly `service-hub` and `service-hub-web`.
- Produces Nginx routing: `/` SPA fallback, `/api/v1` proxy, `/internal/v1` denial, `/web-health` 200.

- [ ] **Step 1: Write static deployment tests**

Parse Compose and assert Web depends on healthy backend, binds only `127.0.0.1:8080:8080`, has no host data/socket mounts, and backend keeps its two existing mounts. Assert Dockerfile is multi-stage and Nginx config contains exact public proxy, internal deny, SPA fallback, upload-size, and long timeout directives.

- [ ] **Step 2: Run and verify RED**

Run `python -m pytest tests/deploy/test_web_container_files.py tests/server/test_container_smoke.py -q`.

- [ ] **Step 3: Implement the Web image**

Use `node:22-alpine` for `npm ci && npm run build`, then an unprivileged-compatible Nginx Alpine image/config listening on 8080. Copy only `dist/` and config into the final stage; add a no-shell HTTP healthcheck where supported.

- [ ] **Step 4: Implement safe Nginx routing**

Proxy `/api/v1/` to `http://service-hub:8000/api/v1/`, set forwarding headers, disable buffering for streaming downloads where appropriate, configure at least 10 GB body size and 3700-second read/send timeouts, return 404 for `/internal/v1`, and use `try_files $uri $uri/ /index.html` for SPA routes.

- [ ] **Step 5: Update Compose and smoke generator**

Keep `HUB_HOST_DATA_DIR` as the only required deployment variable. Do not publish backend on a non-loopback address. Make smoke tests use random ports and disposable absolute host data, then verify Web root, proxied health, SPA refresh, internal denial, and original file upload.

- [ ] **Step 6: Verify**

```bash
HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose config --quiet
docker compose config --services
python -m pytest tests/deploy/test_web_container_files.py tests/server/test_container_smoke.py -q
```

Expected services: `service-hub`, `service-hub-web` only. Run marked container smoke on a Docker-enabled Linux AMD64 host.

- [ ] **Step 7: Commit**

```bash
git add web/Dockerfile web/nginx.conf web/.dockerignore compose.yaml tests
git commit -m "feat(deploy): serve web console through nginx"
```

---

### Task 11: Extend the offline release bundle to include the Web image

**Files:**

- Modify: `deploy/release/build-release.sh`
- Modify: `deploy/release/install.sh`
- Modify: `deploy/release/status.sh`
- Modify: `tests/deploy/test_release_scripts.py`

**Interfaces:**

- Produces backend `service-hub-image.tar` and Web `service-hub-web-image.tar` in one bundle.
- Preserves `.env` with only `HUB_HOST_DATA_DIR`.
- Status checks both container health and `http://127.0.0.1:8080/api/v1/system/health`.

- [ ] **Step 1: Write failing release artifact tests**

Assert both tagged images are built/saved, both tar files are in `SHA256SUMS`, install loads both, README uses Web port 8080, and scripts remain free of `rm -rf` and `docker compose down -v`.

- [ ] **Step 2: Run and verify RED**

Run `python -m pytest tests/deploy/test_release_scripts.py -q`.

- [ ] **Step 3: Update build, install, and status scripts**

Build both images for `linux/amd64`, save them separately, stage the updated Compose and `hubctl`, hash every artifact, load both during install, and preserve the existing normalized host-data validation. Status prints both Compose services before testing Web-proxied API health.

- [ ] **Step 4: Verify shell safety**

```bash
bash -n deploy/release/*.sh
python -m pytest tests/deploy/test_release_scripts.py -q
```

On a Linux AMD64 Docker host also run `bash deploy/release/build-release.sh`, extract the archive into a disposable directory, and run `sha256sum -c SHA256SUMS`.

- [ ] **Step 5: Commit**

```bash
git add deploy/release tests/deploy/test_release_scripts.py
git commit -m "feat(release): bundle service hub web console"
```

---

### Task 12: Document and certify the unified frontend/backend workflow

**Files:**

- Create: `docs/guides/Web管理台构建部署与使用.md`
- Modify: `README.md`
- Modify: `docs/guides/单容器部署与脚本插件使用.md`
- Modify: `docs/reports/single-container-linux-amd64-acceptance.md`
- Modify: `tests/deploy/test_documented_commands.py`
- Create: `tests/integration/test_web_console_lifecycle.py`

**Interfaces:**

- Documents Windows Docker Desktop dual-image build/export.
- Documents Linux AMD64 online/offline import, Compose startup, browser/SSH tunnel access, plugin install, file upload, NC Job, logs, cancel, and output download.
- Adds a Web lifecycle integration gate without replacing existing dual-runtime NC acceptance.

- [ ] **Step 1: Write failing documentation consistency tests**

Assert active docs name both images, port 8080, exact branch-independent commands, two Compose services, `HUB_HOST_DATA_DIR`, `/api/v1`, internal-path denial, and never describe the obsolete three-container backend topology as current.

- [ ] **Step 2: Write the Web integration test**

Against the disposable Compose stack assert `/`, `/api/v1/system/health`, `/jobs/nonexistent` SPA fallback, `/internal/v1/*` denial, API error rendering prerequisites, Web container mount isolation, and restart recovery.

- [ ] **Step 3: Write the Chinese operator guide**

Include exact PowerShell and Bash commands, expected output, rollback, logs, backup, Nginx authentication boundary, and the complete Web workflow. Do not claim native Docker or NC success unless evidence was actually collected.

- [ ] **Step 4: Run all non-integration quality gates**

```bash
python -m pytest -m "not integration"
python -m ruff check .
python -m mypy packages
cd web && npm ci && npm run test:run && npm run typecheck && npm run lint && npm run build
```

- [ ] **Step 5: Run Linux AMD64 release gates**

```bash
HUB_HOST_DATA_DIR=/srv/service-hub-data docker compose up -d --build
python -m pytest -m integration tests/integration/test_web_console_lifecycle.py -v
HUB_NC_SAMPLE_PATH=/absolute/path/sample.nc \
HUB_PLUGIN_PACKAGE_DIR=/absolute/path/plugin-dist \
HUB_BASE_URL=http://127.0.0.1:8080 \
python -m pytest -m integration tests/integration/test_dual_runtime_nc_to_shp.py -v
```

Record image IDs, SHA256 values, Job IDs, output comparison, and any unexecuted gate in the acceptance report.

- [ ] **Step 6: Commit**

```bash
git add README.md docs tests/deploy/test_documented_commands.py tests/integration/test_web_console_lifecycle.py
git commit -m "docs: add web console deployment and acceptance"
```

---

## Final branch gate

Before requesting merge into `main`:

```bash
git status --short
git diff --check feature/single-container-hub...HEAD
python -m pytest -m "not integration"
python -m ruff check .
python -m mypy packages
cd web && npm ci && npm run test:run && npm run typecheck && npm run lint && npm run build
```

On Linux AMD64, also build both images, run both Web lifecycle and NC dual-runtime integration tests, create the offline release bundle, and verify its SHA256 manifest. Only after these gates pass should `feature/service-hub-web-console` enter PR review for `main`.
