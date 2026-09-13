# Task 4 Web Console Components Report

## Delivered scope

- Added shared console UI components in `web/src/components/`:
  `StatusTag`, `HubErrorAlert`, `UploadPanel`, `PageHeader`, and
  `EmptyState`.
- Added shared utilities in `web/src/utils/`: `format.ts` and `download.ts`.
- Added focused tests for every build/job status, collapsed error details,
  retry behavior, safe download filenames, and object URL revocation.
- Audited the pre-existing untracked baseline and repaired two behaviors:
  filenames containing control characters now fall back instead of being
  silently stripped, and the retry action has a stable accessible name.
- Corrected the `saveBlob` click-error test to assert the required cleanup
  behavior without requiring unrelated exception swallowing, then added a red
  assertion proving the temporary anchor is removed when `click()` throws.

No later web console pages, routes, or feature flows were added.

## TDD red run

Command:

```powershell
cd web
npm run test:run -- src/components src/utils
```

Result: failed as expected before implementation repair.

Key failing assertions:

```text
src/utils/download.test.ts > safeDownloadName > falls back instead of using unsafe name control.zip
AssertionError: expected 'control.zip' to be 'fallback.zip'

src/utils/download.test.ts > safeDownloadName > falls back instead of using unsafe name [2J.zip
AssertionError: expected '[2J.zip' to be 'fallback.zip'

src/utils/download.test.ts > saveBlob > revokes the object URL even when the click throws
AssertionError: expected [Function] to not throw an error but 'Error: click blocked' was thrown

src/components/HubErrorAlert.test.tsx > HubErrorAlert > renders a retry action when provided
TestingLibraryElementError: Unable to find an accessible element with the role "button" and name "重试"
Accessible button name was "重 试".
```

Suite summary:

```text
Test Files  2 failed | 1 passed (3)
Tests       4 failed | 25 passed (29)
```

Additional cleanup red check:

```powershell
cd web
npm run test:run -- src/utils/download.test.ts
```

Result: failed as expected after adding the anchor-removal assertion and before
the production cleanup change.

```text
src/utils/download.test.ts > saveBlob > revokes the object URL even when the click throws
Error: expect(element).not.toBeInTheDocument()

expected document not to contain element, found <a
  download="result.zip"
  href="blob:mock-url"
  rel="noopener"
/> instead

Test Files  1 failed (1)
Tests       1 failed | 11 passed (12)
```

## Green and final verification

Command:

```powershell
cd web
npm run test:run -- src/components src/utils
```

Output:

```text
> service-hub-web@0.0.0 test:run
> vitest run src/components src/utils

RUN  v3.2.7 D:/LearningMaterials/GIS+AI/service-hub/.worktrees/service-hub-web-console-impl/web

✓ src/utils/download.test.ts (12 tests) 11ms
✓ src/components/StatusTag.test.tsx (13 tests) 124ms
✓ src/components/HubErrorAlert.test.tsx (4 tests) 342ms

Test Files  3 passed (3)
Tests       29 passed (29)
```

Command:

```powershell
cd web
npm run typecheck
```

Output:

```text
> service-hub-web@0.0.0 typecheck
> tsc -b --pretty false
```

Command:

```powershell
cd web
npm run lint
```

Output:

```text
> service-hub-web@0.0.0 lint
> eslint .
```

Command:

```powershell
cd web
npm run build
```

Output:

```text
> service-hub-web@0.0.0 build
> tsc -b && vite build

vite v6.4.3 building for production...
transforming...
✓ 3049 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.40 kB │ gzip:   0.27 kB
dist/assets/index-CWC-41OG.css    1.55 kB │ gzip:   0.74 kB
dist/assets/index-Db4mew2A.js   707.41 kB │ gzip: 227.64 kB

(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
✓ built in 4.59s
```

## Notes

- The Vite chunk-size warning is pre-existing build behavior for the current
  dependency graph and does not fail the build.
- `npm` printed a local update notice after commands; it was not relevant to
  Task 4 behavior or verification.

## Review fix round 1

Addressed four review findings:

- `safeDownloadName` now treats the server extension as a single safe suffix.
  Traversal fragments, separators, control characters, empty extensions, and
  multi-suffix values fall back to the trusted fallback extension.
- `StatusTag` now uses the approved palette: green for ready/enabled/success,
  blue for pending/installing/preparing, cyan plus a spinning marker for
  running, orange for cancel request, red for failures/timeouts, and gray
  default for cancelled/disabled.
- `formatDateTime` now returns `-` for invalid timestamps, matching the
  existing missing-value behavior.
- `UploadPanel` now invokes the supplied `beforeUpload` callback and returns
  `false` so Ant does not run its faux custom request/success path.

Red evidence:

```powershell
cd web
npm run test:run -- src/components src/utils
```

Result before production fixes:

```text
Test Files  3 failed | 2 passed (5)
Tests       18 failed | 40 passed (58)
```

The failures covered unsafe server extensions appending directly to names,
invalid datetime returning the raw input, status tags using Ant status presets
instead of approved colors, and missing running marker.

Additional UploadPanel red check:

```powershell
cd web
npm run test:run -- src/components/UploadPanel.test.tsx
```

Result before the `beforeUpload` fix:

```text
src/components/UploadPanel.test.tsx > UploadPanel > invokes beforeUpload for the accepted file and prevents Ant auto upload
Error: expect(element).not.toBeInTheDocument()

expected document not to contain element, found <span>
  auto-uploaded
</span> instead

Test Files  1 failed (1)
Tests       1 failed | 2 passed (3)
```

Fresh final verification:

```powershell
cd web
npm run test:run -- src/components src/utils
```

```text
✓ src/utils/download.test.ts (19 tests) 10ms
✓ src/utils/format.test.ts (7 tests) 16ms
✓ src/components/HubErrorAlert.test.tsx (4 tests) 324ms
✓ src/components/StatusTag.test.tsx (25 tests) 161ms
✓ src/components/UploadPanel.test.tsx (3 tests) 159ms

Test Files  5 passed (5)
Tests       58 passed (58)
```

```powershell
cd web
npm run typecheck
```

```text
> service-hub-web@0.0.0 typecheck
> tsc -b --pretty false
```

```powershell
cd web
npm run lint
```

```text
> service-hub-web@0.0.0 lint
> eslint .
```

```powershell
cd web
npm run build
```

```text
> service-hub-web@0.0.0 build
> tsc -b && vite build

vite v6.4.3 building for production...
transforming...
✓ 3049 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.40 kB │ gzip:   0.27 kB
dist/assets/index-CWC-41OG.css    1.55 kB │ gzip:   0.74 kB
dist/assets/index-Db4mew2A.js   707.41 kB │ gzip: 227.64 kB

(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
✓ built in 3.93s
```

The Vite chunk-size warning remains non-fatal and unchanged from the prior
Task 4 verification.
