# Table Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new private repository `Generality-Labs/audits-site`: a Cloudflare Worker that proxies the findings export from the private findings repo, and a React app that shows the findings table and one page per eval, deployed to `audits.generality.org` behind Cloudflare Access.

**Architecture:** Scaffolded from `cloudflare-worker-template` v1.1.1. The Worker (`src/index.ts`) answers `/health` and `/data/*`; `/data/*` fetches `export/*.json` from `raw.githubusercontent.com` with a read token and caches it with the Cache API for five minutes. Everything else is static assets built by Vite into `dist/`, with single-page-application fallback so deep links reach React Router. The app (`app/`) loads one JSON document per route and holds it in memory; filters are a pure function over the rows, kept in the query string; TanStack Table sorts and renders.

**Tech Stack:** TypeScript strict, wrangler 4, `@cloudflare/vitest-pool-workers` (the template's pin) and vitest 4, Vite, React 19, React Router 7 (library mode), TanStack Table 8, Tailwind CSS 4 via `@tailwindcss/vite`, shadcn/ui (copied components), `@fontsource-variable/geist`, Testing Library with jsdom, Playwright. Node 26 (the template default; `nvm use 26`). Gate: `npm run typecheck && npm test && npm run check:bundle && uvx pre-commit run --all-files`.

**Spec:** `docs/superpowers/specs/2026-10-07-table-site-design.md` in `inspect_audit` on `feat/findings`, in particular "The Worker", "The React app", "Repository, CI, deploy", "Testing" and "Implementation notes". The export is the source of truth for shapes: `src/inspect_audit/findings/export.py`.

## Global Constraints

- Repository `Generality-Labs/audits-site`, private, scaffolded with `uvx copier copy --vcs-ref v1.1.1 gh:Generality-Labs/cloudflare-worker-template audits-site`. Answers: no staging, no D1, no R2, no KV, no assets (the `[assets]` block is written by hand to point at `dist/`), no cron, Playwright yes, typos no, template-update yes, Node `26`, license Proprietary.
- `wrangler.toml` top level is local only; production is `[env.production]`, name `audits-site`, custom domain `audits.generality.org`. `[vars]` are not inherited by named environments, so they are repeated under `[env.production.vars]`.
- Vars: `FINDINGS_ORIGIN = "https://raw.githubusercontent.com"`, `FINDINGS_REPO = "Generality-Labs/inspect-evals-findings"`, `FINDINGS_REF = "main"`. Secret: `GITHUB_TOKEN`.
- Slugs match `^[a-z0-9-]+$`. Cache TTL 300 seconds, stored with `Cache-Control: public, max-age=300`. Upstream 404 is 404; any other upstream failure is 502 with a one-line body. No upstream response header is forwarded.
- Export `schema` the app understands: `1`. A newer schema shows exactly "This site needs updating to read the current export." and nothing else.
- Palette from inspect_evals `docs/_brand.yml`: teal `#24857a` primary; light ink `#1a1919` on paper `#ffffff`, teal links; dark mist `#e4e7e6` on night `#121514`, mint `#3fcfbc` links. Washes are `color-mix` of the link colour into the background at 8, 16 and 32 percent. Font Geist from `@fontsource-variable/geist`, fallback `ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif`. No other font, no third-party request.
- Dark by default; a header toggle switches theme and stores the choice in `localStorage`; the first visit follows `prefers-color-scheme`.
- Copy rules: count line "N of M findings"; header `data from <time>`; empty eval "No findings. N checks ran, M skipped."
- Biome formats and lints TypeScript (`biome.json`, 2-space indent, width 100). Comments follow the template's density: short, explain why.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Nothing is pushed and no GitHub repository is created without Matt's go-ahead (Task 9).

## Deviations from the spec, and why

1. **`/health`, not `/healthz`.** The template's handler, its test and its Playwright spec use `/health` returning `{"status":"ok"}`; the deploy smoke test reads whatever `HEALTH_URL` says. Keeping the template's path avoids a conflict on every `copier update`.
2. **`FINDINGS_ORIGIN` var.** The spec has the dev `FINDINGS_REPO` point at a local stub, but the host is fixed in the URL, so the stub cannot be reached by changing the repo alone. Playwright overrides `FINDINGS_ORIGIN` with `--var`.
3. **`run_worker_first = ["/data/*", "/health"]`.** With single-page-application fallback, a browser navigation (`Sec-Fetch-Mode: navigate`) to a path with no asset gets `index.html` before the Worker runs, so opening `/data/index.json` in a tab would show the app. The array makes those paths reach the Worker always.
4. **`vi.spyOn(globalThis, "fetch")`, not `fetchMock`.** `fetchMock` was removed from `cloudflare:test` with the move to Vitest 4.
5. **The Cache API is a no-op behind Access.** Cloudflare's Cache API docs: "For Workers fronted by Cloudflare Access, the Cache API is not currently available." Until the Access policy comes off, every `/data` request goes to GitHub. That is fine at pilot traffic (a handful of readers, 5 to 20 KB files). The code and tests follow the spec, so caching starts working when the site goes public. KV is the move if it matters sooner, as the spec already says.
6. **Filtering outside TanStack.** Filters are one pure function (`applyFilters`) over the rows, because it is unit-testable without rendering and the query string is its only state. TanStack Table sorts and renders.
7. **Native selects.** shadcn's `native-select`, not the Radix `select`: it is accessible, works in jsdom without pointer-capture shims, and a filter needs no more.
8. **Fixture.** The real export (sweep of 2026-10-07) has no suppressions, no issues, one producer with findings and no empty eval. The fixture is that export plus a small set of additions, listed in `test/fixtures/README.md`, so every page state has data.
9. **CI bundle check builds first.** `wrangler deploy --dry-run` needs `dist/` to exist, so `ci.yml` passes `bundle-script: check:bundle` (`vite build && wrangler deploy --env production --dry-run`), an input the shared workflow already has.
10. **Reviewed column.** The issue id links to GitHub when `github` is set; an accepted finding whose issue has no GitHub link yet shows the id as plain text, and an unreviewed one is blank. A blank cell on an accepted row would contradict the "accepted" filter.

## Review Focus

1. **An expired or revoked `GITHUB_TOKEN`.** GitHub answers 401 or 403; the Worker must answer 502 with a one-line body, and the app must say the data could not be loaded, not render a blank table. Tests in Task 2 (401 → 502) and Task 4 (`loadDoc` on 502).
2. **An Access session that expired while the tab was open.** The data fetch gets the Access login page (HTML) or a cross-origin redirect instead of JSON; the app must say so and suggest reloading, not throw. Test in Task 4 (non-JSON body → failed with "Reload the page").
3. **Hostile or malformed data paths.** `/data/evals/..%2Fsecret.json`, `/data/evals/UPPER.json`, `/data/evals/a/b.json`, `/data/evals/.json`, `/data/index.json/`, `/data/other.json` are 404 without any upstream request. Test in Task 2.
4. **A shared link with a filter value that does not exist** (`?severity=bogus`, `?reviewed=xyz`). No crash; `reviewed` falls back to "any"; an unknown facet value stays selected and visible in its select (so the view explains its "0 of M"), with a clear-filters button. Tests in Task 5.
5. **Direct navigation to a deep link or a data URL on the deployed Worker.** `/evals/<slug>?…` serves the app; `/data/index.json` typed into the address bar serves JSON. Test in Task 7 (Playwright navigations, which send `Sec-Fetch-Mode: navigate`).

______________________________________________________________________

### Task 1: Scaffold the repository

**Files:**

- Create: `~/Developer/inspect_ai/audits-site/` (whole scaffold)

**Interfaces:**

- Produces: a local git repository with one commit on `main`, `npm test` green with the template's tests.

- [ ] **Step 1: Scaffold**

```bash
cd ~/Developer/inspect_ai
uvx copier copy --vcs-ref v1.1.1 --trust gh:Generality-Labs/cloudflare-worker-template audits-site \
  --data project_name=audits-site \
  --data "project_description=The Inspect Evals findings table: every active finding across every eval, and a page per eval" \
  --data use_staging=false --data use_d1=false --data use_r2=false --data use_kv=false \
  --data use_assets=false --data use_cron=false --data use_playwright=true --data node_version=26 \
  --data use_typos=false --data use_template_update=true --data license=Proprietary
```

- [ ] **Step 2: Install and run the template's tests**

```bash
cd ~/Developer/inspect_ai/audits-site && source ~/.nvm/nvm.sh && nvm use 26
npm install && npm run typecheck && npm test
```

Expected: typecheck clean; `worker` project 2 passed.

- [ ] **Step 3: Commit**

```bash
git init -b main && git add -A && git commit -m "chore: scaffold from cloudflare-worker-template v1.1.1" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git switch -c feat/table-site
```

______________________________________________________________________

### Task 2: The Worker serves the export

**Files:**

- Modify: `src/index.ts`, `test/worker.test.ts`, `wrangler.toml`, `vitest.config.ts`, `.dev.vars.example`

**Interfaces:**

- Produces: `GET /health` → `{"status":"ok"}`; `GET /data/index.json`, `GET /data/evals/<slug>.json` → the export JSON; `exportPath(pathname: string): string | null` exported from `src/index.ts`.

- [ ] **Step 1: Configure wrangler**

Replace the body of `wrangler.toml` below the header comment with:

```toml
name = "audits-site-dev"
main = "src/index.ts"
compatibility_date = "2026-07-01"

# Inherited by all environments.
[observability.logs]
enabled = true
invocation_logs = true

# The React app, built by `vite build`. Any path without a file gets
# index.html so React Router can route deep links. /data and /health always
# reach the Worker first: without this, a browser navigation to a path with no
# asset is answered with index.html before the Worker runs.
[assets]
directory = "./dist"
binding = "ASSETS"
not_found_handling = "single-page-application"
run_worker_first = ["/data/*", "/health"]

# Where the export is read from. FINDINGS_ORIGIN exists so the e2e tests can
# point the Worker at a local stub. The read token is the GITHUB_TOKEN secret.
[vars]
FINDINGS_ORIGIN = "https://raw.githubusercontent.com"
FINDINGS_REPO = "Generality-Labs/inspect-evals-findings"
FINDINGS_REF = "main"

[env.production]
name = "audits-site"
workers_dev = false
routes = [{ pattern = "audits.generality.org", custom_domain = true }]

# [vars] are not inherited by named environments.
[env.production.vars]
FINDINGS_ORIGIN = "https://raw.githubusercontent.com"
FINDINGS_REPO = "Generality-Labs/inspect-evals-findings"
FINDINGS_REF = "main"
```

In `vitest.config.ts`, give the worker pool a test token:

```ts
plugins: [
  cloudflareTest({
    wrangler: { configPath: "./wrangler.toml" },
    miniflare: { bindings: { GITHUB_TOKEN: "test-token" } },
  }),
],
```

In `.dev.vars.example`, replace the example lines with:

```ini
# Fine-grained PAT, Contents: read on Generality-Labs/inspect-evals-findings only.
GITHUB_TOKEN=
```

Create `dist/` if the pool refuses a missing assets directory: `mkdir -p dist` (it is gitignored; Task 3's `pretest` makes it permanent).

- [ ] **Step 2: Write the failing tests**

`test/worker.test.ts`:

```ts
import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";

const UPSTREAM =
  "https://raw.githubusercontent.com/Generality-Labs/inspect-evals-findings/main/export";

async function call(path: string, init?: RequestInit): Promise<Response> {
  const ctx = createExecutionContext();
  const res = await worker.fetch(new Request(`https://audits.example${path}`, init), env, ctx);
  await waitOnExecutionContext(ctx);
  return res;
}

function stubUpstream(status = 200, body = '{"schema":1}') {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(body, {
        status,
        headers: { "Set-Cookie": "upstream=1", "X-GitHub-Request-Id": "abc" },
      }),
  );
}

// Each test uses its own slug, so no test sees another's cache entry; the
// index entry is the one URL shared, and is cleared here.
afterEach(async () => {
  vi.restoreAllMocks();
  await caches.default.delete(`${UPSTREAM}/index.json`);
});

describe("GET /health", () => {
  it("returns 200 ok", async () => {
    const res = await call("/health");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
  });
});

describe("GET /data", () => {
  it("fetches the index from the findings repo with the token on a miss", async () => {
    const upstream = stubUpstream(200, '{"schema":1,"evals":[]}');
    const res = await call("/data/index.json");
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toContain("application/json");
    expect(await res.json()).toEqual({ schema: 1, evals: [] });
    expect(upstream).toHaveBeenCalledOnce();
    const [url, init] = upstream.mock.calls[0];
    expect(url).toBe(`${UPSTREAM}/index.json`);
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer test-token");
  });

  it("serves a second request within the TTL from the cache", async () => {
    const upstream = stubUpstream();
    await call("/data/evals/cached-eval.json");
    const res = await call("/data/evals/cached-eval.json");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ schema: 1 });
    expect(upstream).toHaveBeenCalledOnce();
  });

  it("maps an eval slug to its export file", async () => {
    const upstream = stubUpstream();
    await call("/data/evals/inspect-evals-hle.json");
    expect(upstream.mock.calls[0][0]).toBe(`${UPSTREAM}/evals/inspect-evals-hle.json`);
  });

  it.each([
    "/data/evals/UPPER.json",
    "/data/evals/a_b.json",
    "/data/evals/..%2Fsecret.json",
    "/data/evals/a/b.json",
    "/data/evals/.json",
    "/data/index.json/",
    "/data/other.json",
    "/data/",
  ])("404s %s without calling upstream", async (path) => {
    const upstream = stubUpstream();
    const res = await call(path);
    expect(res.status).toBe(404);
    expect(upstream).not.toHaveBeenCalled();
  });

  it("passes an upstream 404 through as 404", async () => {
    stubUpstream(404, "404: Not Found");
    expect((await call("/data/evals/missing-eval.json")).status).toBe(404);
  });

  it.each([500, 401, 403])("turns an upstream %i into a one-line 502", async (status) => {
    stubUpstream(status, "nope");
    const res = await call(`/data/evals/failing-${status}.json`);
    expect(res.status).toBe(502);
    expect((await res.text()).trim().split("\n")).toHaveLength(1);
  });

  it("turns a network failure into 502", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("network down"));
    expect((await call("/data/evals/unreachable.json")).status).toBe(502);
  });

  it("does not cache a failure", async () => {
    const upstream = stubUpstream(500);
    await call("/data/evals/flaky.json");
    await call("/data/evals/flaky.json");
    expect(upstream).toHaveBeenCalledTimes(2);
  });

  it("forwards no upstream header and never the token", async () => {
    stubUpstream();
    for (const res of [
      await call("/data/evals/headers-eval.json"),
      await call("/data/evals/headers-eval.json"),
    ]) {
      expect(res.headers.get("Set-Cookie")).toBeNull();
      expect(res.headers.get("X-GitHub-Request-Id")).toBeNull();
      for (const [, value] of res.headers) expect(value).not.toContain("test-token");
    }
  });

  it("rejects writes", async () => {
    const upstream = stubUpstream();
    expect((await call("/data/index.json", { method: "POST" })).status).toBe(405);
    expect(upstream).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run to see them fail**

Run: `npx vitest run --project worker`
Expected: the `/data` tests FAIL (404 for every data path); `/health` passes.

- [ ] **Step 4: Implement**

`src/index.ts`:

```ts
// Binding types are generated from wrangler.toml into worker-configuration.d.ts
// by `npm run typecheck` (`wrangler types`). The secret is declared by hand
// because the generator cannot see it.
declare global {
  namespace Cloudflare {
    interface Env {
      GITHUB_TOKEN: string;
    }
  }
}

const SLUG = /^[a-z0-9-]+$/;
const MAX_AGE_SECONDS = 300;

/** The export file a /data path names, or null when it names nothing served. */
export function exportPath(pathname: string): string | null {
  if (pathname === "/data/index.json") return "index.json";
  const match = /^\/data\/evals\/([^/]+)\.json$/.exec(pathname);
  return match && SLUG.test(match[1]) ? `evals/${match[1]}.json` : null;
}

function plain(body: string, status: number): Response {
  return new Response(`${body}\n`, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

async function serveExport(
  path: string,
  env: Cloudflare.Env,
  ctx: ExecutionContext,
): Promise<Response> {
  const url = `${env.FINDINGS_ORIGIN}/${env.FINDINGS_REPO}/${env.FINDINGS_REF}/export/${path}`;
  const cached = await caches.default.match(url);
  if (cached) return cached;

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, "User-Agent": "audits-site" },
    });
  } catch {
    return plain("could not reach the findings repository", 502);
  }
  if (upstream.status === 404) return plain("not found", 404);
  if (!upstream.ok) return plain(`the findings repository answered ${upstream.status}`, 502);

  // A new response with only our headers, so nothing upstream sent is forwarded.
  const response = new Response(await upstream.arrayBuffer(), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": `public, max-age=${MAX_AGE_SECONDS}`,
    },
  });
  ctx.waitUntil(caches.default.put(url, response.clone()));
  return response;
}

export default {
  async fetch(request: Request, env: Cloudflare.Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // The deploy pipeline smoke-tests this endpoint after every deploy — keep
    // it cheap and dependency-free.
    if (request.method === "GET" && url.pathname === "/health") {
      return Response.json({ status: "ok" });
    }

    if (url.pathname.startsWith("/data/")) {
      if (request.method !== "GET") return plain("method not allowed", 405);
      const path = exportPath(url.pathname);
      return path ? serveExport(path, env, ctx) : plain("not found", 404);
    }

    return plain("not found", 404);
  },
} satisfies ExportedHandler<Cloudflare.Env>;
```

- [ ] **Step 5: Run the tests**

Run: `npm run typecheck && npx vitest run --project worker`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(worker): serve the findings export from GitHub with an edge cache" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 3: App toolchain, theme and shell

**Files:**

- Create: `index.html`, `vite.config.ts`, `tsconfig.app.json`, `components.json`, `app/main.tsx`, `app/App.tsx`, `app/index.css`, `app/lib/utils.ts`, `app/lib/theme.ts`, `app/components/Layout.tsx`, `app/components/ThemeToggle.tsx`, `app/components/ui/*` (shadcn), `test/app/setup.ts`, `test/app/theme.test.tsx`
- Modify: `package.json`, `tsconfig.json`, `vitest.config.ts`, `biome.json`, `.github/workflows/ci.yml`

**Interfaces:**

- Produces: `Theme = "dark" | "light"`, `THEME_KEY`, `initialTheme(): Theme`, `applyTheme(theme: Theme, remember?: boolean): void` in `app/lib/theme.ts`; `<Layout />` (header with title link and `<ThemeToggle />`, `<Outlet />`); `<App />` with routes `/` and `/evals/:slug` (placeholders until Tasks 5 and 6); `cn()` in `app/lib/utils.ts`; shadcn `Button`, `Badge`, `Input`, `NativeSelect`, `Table*` under `@/components/ui/`. Path alias `@/` → `app/`.

- [ ] **Step 1: Dependencies**

```bash
npm install react react-dom react-router @tanstack/react-table @fontsource-variable/geist \
  class-variance-authority clsx tailwind-merge lucide-react
npm install -D vite @vitejs/plugin-react tailwindcss @tailwindcss/vite @types/react @types/react-dom \
  jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom
```

- [ ] **Step 2: Config files**

`package.json` scripts (keep the template's others):

```json
"dev": "wrangler dev",
"dev:app": "vite",
"build": "vite build",
"typecheck": "wrangler types --include-runtime=false && tsc --noEmit && tsc --noEmit -p tsconfig.app.json",
"pretest": "node -e \"require('node:fs').mkdirSync('dist',{recursive:true})\"",
"test": "vitest run",
"test:e2e": "playwright test",
"check:bundle": "vite build && wrangler deploy --env production --dry-run",
"deploy": "vite build && wrangler deploy --env production",
```

(`pretest` exists because the worker pool reads `[assets]` and a clean checkout has no `dist/`. `node -e` scripts are CommonJS even under `"type": "module"`.)

`vite.config.ts`:

```ts
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "app") } },
  build: { outDir: "dist", emptyOutDir: true },
  // `npm run dev:app` serves the app with hot reload; data comes from
  // `npm run dev` (wrangler) on its default port.
  server: { proxy: { "/data": "http://127.0.0.1:8787" } },
});
```

`tsconfig.app.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "types": ["vite/client"],
    "paths": { "@/*": ["./app/*"] },
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noImplicitReturns": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["app", "test/app"]
}
```

`tsconfig.json`: keep the template's options; add `"paths": { "@/*": ["./app/*"] }` (the shadcn CLI reads aliases from here) and change `include`/`exclude` so the DOM-free worker program does not pick up the app tests:

```json
"include": ["worker-configuration.d.ts", "src/**/*.ts", "test/**/*.ts"],
"exclude": ["test/app"]
```

`vitest.config.ts`, `unit` project:

```ts
{
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "app") } },
  test: {
    name: "unit",
    include: ["test/**/*.test.{ts,tsx}"],
    exclude: ["test/worker.test.ts"],
    setupFiles: ["./test/app/setup.ts"],
  },
},
```

(with `import path from "node:path"` and `import react from "@vitejs/plugin-react"`; drop `passWithNoTests`, the project now has tests). App test files opt into jsdom with a `// @vitest-environment jsdom` first line.

`test/app/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (typeof localStorage !== "undefined") localStorage.clear();
});
```

`biome.json`: add `"css": { "parser": { "tailwindDirectives": true } }` so `@theme` and `@custom-variant` parse, and exclude `dist`: `"includes": ["**", "!**/.wrangler", "!**/dist"]`.

`.github/workflows/ci.yml`, under `with:`:

```yaml
      # The dry run needs dist/, so build first (vite build && wrangler deploy --dry-run).
      bundle-script: check:bundle
```

`components.json`:

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": { "config": "", "css": "app/index.css", "baseColor": "neutral", "cssVariables": true, "prefix": "" },
  "iconLibrary": "lucide",
  "aliases": { "components": "@/components", "utils": "@/lib/utils", "ui": "@/components/ui", "lib": "@/lib", "hooks": "@/hooks" }
}
```

- [ ] **Step 3: Styles**

`app/index.css`:

```css
@import "tailwindcss";
@import "@fontsource-variable/geist";

@custom-variant dark (&:where(.dark, .dark *));

/* Colours from inspect_evals docs/_brand.yml. Surfaces, borders and washes are
   mixed from the link colour or the foreground into the background, as the
   docs site does, so they follow the active theme. */
:root {
  --ink: #1a1919;
  --paper: #ffffff;
  --background: var(--paper);
  --foreground: var(--ink);
  --link: #24857a;
  --primary: #24857a;
  --primary-foreground: #ffffff;
  --destructive: #c0362c;
}

.dark {
  --background: #121514;
  --foreground: #e4e7e6;
  --link: #3fcfbc;
  --destructive: #ff7b72;
}

:root,
.dark {
  --wash: color-mix(in srgb, var(--link) 8%, var(--background));
  --wash-strong: color-mix(in srgb, var(--link) 16%, var(--background));
  --wash-edge: color-mix(in srgb, var(--link) 32%, var(--background));
  --card: var(--background);
  --card-foreground: var(--foreground);
  --popover: var(--background);
  --popover-foreground: var(--foreground);
  --secondary: color-mix(in srgb, var(--foreground) 6%, var(--background));
  --secondary-foreground: var(--foreground);
  --muted: color-mix(in srgb, var(--foreground) 6%, var(--background));
  --muted-foreground: color-mix(in srgb, var(--foreground) 70%, var(--background));
  --accent: var(--wash-strong);
  --accent-foreground: var(--foreground);
  --border: color-mix(in srgb, var(--foreground) 14%, var(--background));
  --input: var(--border);
  --ring: var(--wash-edge);
  --radius: 0.5rem;
}

@theme inline {
  --font-sans: "Geist Variable", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
    sans-serif;
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-link: var(--link);
  --color-wash: var(--wash);
  --color-wash-strong: var(--wash-strong);
  --color-wash-edge: var(--wash-edge);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --radius-sm: calc(var(--radius) - 4px);
  --radius-md: calc(var(--radius) - 2px);
  --radius-lg: var(--radius);
}

@layer base {
  * {
    @apply border-border outline-ring/50;
  }
  body {
    @apply bg-background text-foreground font-sans antialiased;
  }
  a {
    @apply text-link underline-offset-2 hover:underline;
  }
}
```

- [ ] **Step 4: shadcn components**

```bash
npx shadcn@latest add button badge input table native-select --yes
```

Expected: `app/components/ui/{button,badge,input,table,native-select}.tsx` and `app/lib/utils.ts` (`cn`). If the CLI cannot resolve the aliases or rewrites `app/index.css`, restore `app/index.css` from Step 3 and keep the generated components. Run `npx biome check --write app` afterwards.

- [ ] **Step 5: Write the failing theme test**

`test/app/theme.test.tsx`:

```tsx
// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "@/components/ThemeToggle";
import { applyTheme, initialTheme, THEME_KEY } from "@/lib/theme";

function prefersLight(light: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: light && query === "(prefers-color-scheme: light)",
    media: query,
  }));
}

beforeEach(() => document.documentElement.classList.remove("dark"));

describe("initialTheme", () => {
  it("is dark by default", () => {
    prefersLight(false);
    expect(initialTheme()).toBe("dark");
  });

  it("follows prefers-color-scheme on a first visit", () => {
    prefersLight(true);
    expect(initialTheme()).toBe("light");
  });

  it("prefers a stored choice", () => {
    prefersLight(true);
    localStorage.setItem(THEME_KEY, "dark");
    expect(initialTheme()).toBe("dark");
  });

  it("ignores a stored value it does not know", () => {
    prefersLight(false);
    localStorage.setItem(THEME_KEY, "sepia");
    expect(initialTheme()).toBe("dark");
  });
});

describe("ThemeToggle", () => {
  it("switches the dark class and remembers the choice", async () => {
    applyTheme("dark");
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  });
});
```

Run: `npx vitest run --project unit` — Expected: FAIL, modules not found.

- [ ] **Step 6: Implement theme, toggle, layout, entry**

`app/lib/theme.ts`:

```ts
export type Theme = "dark" | "light";

export const THEME_KEY = "audits-site-theme";

/** A stored choice wins; otherwise light only when the system asks for it. */
export function initialTheme(): Theme {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** Only an explicit toggle is remembered, so a first visit keeps following the system. */
export function applyTheme(theme: Theme, remember = false): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  if (remember) localStorage.setItem(THEME_KEY, theme);
}
```

`app/components/ThemeToggle.tsx`:

```tsx
import { Moon, Sun } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { applyTheme, type Theme } from "@/lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Switch to ${next} theme`}
      onClick={() => {
        applyTheme(next, true);
        setTheme(next);
      }}
    >
      {theme === "dark" ? <Sun /> : <Moon />}
    </Button>
  );
}
```

`app/components/Layout.tsx`:

```tsx
import { Link, Outlet } from "react-router";
import { ThemeToggle } from "@/components/ThemeToggle";

export function Layout() {
  return (
    <div className="min-h-screen">
      <header className="border-b">
        <div className="mx-auto flex max-w-screen-2xl items-center justify-between px-4 py-3">
          <Link to="/" className="font-semibold text-foreground hover:no-underline">
            Inspect Evals audits
          </Link>
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-screen-2xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
```

`app/App.tsx` (pages arrive in Tasks 5 and 6; placeholders keep the shell testable):

```tsx
import { Link, Route, Routes } from "react-router";
import { Layout } from "@/components/Layout";

export function PageNotFound() {
  return (
    <p>
      There is no page here. <Link to="/">Back to the findings</Link>.
    </p>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<p>Findings</p>} />
        <Route path="evals/:slug" element={<p>Eval</p>} />
        <Route path="*" element={<PageNotFound />} />
      </Route>
    </Routes>
  );
}
```

`app/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "@/App";
import { applyTheme, initialTheme } from "@/lib/theme";
import "@/index.css";

applyTheme(initialTheme());

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");
createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
```

`index.html`:

```html
<!doctype html>
<html lang="en" class="dark">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Inspect Evals audits</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/app/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: Run everything**

Run: `npm run typecheck && npm test && npm run build && npx biome check .`
Expected: all PASS; `dist/index.html` and hashed assets exist; no request in `dist/` points at a third-party host (`grep -rE "https?://" dist | grep -v "w3.org\|reactjs.org\|react.dev"` prints nothing that is loaded).

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat(app): Vite, React, Tailwind and shadcn shell with the brand themes" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 4: Export types, fixture and data loading

**Files:**

- Create: `app/lib/types.ts`, `app/lib/data.ts`, `app/lib/format.ts`, `app/components/LoadState.tsx`, `test/fixtures/export/index.json`, `test/fixtures/export/evals/*.json`, `test/fixtures/README.md`, `test/app/fixtures.ts`, `test/app/data.test.ts`, `test/app/format.test.ts`

**Interfaces:**

- Produces (types): `SUPPORTED_SCHEMA = 1`; `Severity`, `Status`, `Revision`, `ProducerRun`, `EvalEntry`, `FindingRow`, `IndexDoc`, `Location`, `Outcome`, `RunEntry`, `GroupFinding`, `Group`, `SuppressedGroup`, `IssueEntry`, `DatasetInput`, `LogEntry`, `Inputs`, `EvalDoc`; `SEVERITY_RANK: Record<Severity, number>`.
- Produces (data): `Loaded<T>` union with states `loading | ready (doc) | not-found | too-new | failed (message)`; `loadDoc<T extends { schema: number }>(path: string): Promise<Loaded<T>>`; `useDoc<T>(path: string): Loaded<T>`; `<LoadState loaded notFound />`.
- Produces (format): `formatTime(iso: string): string` → `"2026-10-07 06:42 UTC"`; `shortCommit(commit: string | null): string`; `locationKeyLabel(key: string): string`; `locationLabel(location: Location): string`.
- Produces (tests): `fixtureIndex(): IndexDoc`, `fixtureEval(slug: string): EvalDoc`, `stubData(overrides?: Record<string, Response>)` in `test/app/fixtures.ts`.

- [ ] **Step 1: Copy the real export and add the fixture rows**

```bash
mkdir -p test/fixtures/export/evals
F=~/Developer/inspect_ai/inspect-evals-findings
git -C $F fetch -q origin
for p in index.json evals/inspect-evals-hle.json evals/inspect-evals-scicode.json evals/inspect-evals-stereoset.json; do
  git -C $F show origin/main:export/$p > test/fixtures/export/$p
done
git -C $F rev-parse --short origin/main   # record in the README below
```

Then run this once (not committed) from the repo root:

```bash
node --input-type=module <<'EOF'
import { readFileSync, writeFileSync } from "node:fs";
const dir = "test/fixtures/export";
const read = (p) => JSON.parse(readFileSync(`${dir}/${p}`, "utf8"));
const write = (p, d) => writeFileSync(`${dir}/${p}`, `${JSON.stringify(d, null, 1)}\n`);

const index = read("index.json");
const stereo = read("evals/inspect-evals-stereoset.json");
const github = "https://github.com/UKGovernmentBEIS/inspect_evals/issues/2650";

// 1. An accepted issue on StereoSet, linked to its lint finding.
const lint = index.findings.find((f) => f.slug === "inspect-evals-stereoset");
lint.issue = "ISS-0001";
lint.github = github;
stereo.groups[0].findings[0].issue = "ISS-0001";
stereo.issues = [{ id: "ISS-0001", title: "StereoSet shuffles without a seed", author: "Matt Fisher",
  opened: "2026-10-05", reason: "Unseeded shuffle changes the sample order between runs.", github, current: 1 }];

// 2. A dataset finding: another producer, dimension, check, severity, status and location kind.
const run = stereo.runs.find((r) => r.producer === "inspect_dataset");
const finding = {
  id: `${run.run_id}/1`, fingerprint: "sha256:fixture-duplicate-inputs", status: "hypothesis",
  summary: "Two samples share the same context and the same three options",
  locations: [{ role: "primary", quote: null, kind: "sample", sample_id: "intersentence-0042", field: "input" }],
  issue: null, first_seen: "2026-10-07T06:43:21Z", last_seen: "2026-10-07T06:43:21Z",
};
stereo.groups.unshift({ producer: "inspect_dataset", rule: "duplicate_inputs", dimension: "dataset",
  check: "duplicates", severity: "major", count: 1, findings: [finding] });
run.passing -= 1;
run.outcomes.unshift({ rule: "duplicate_inputs", status: "fail", message: "1 finding" });
index.findings.push({ id: finding.id, fingerprint: finding.fingerprint, eval: "inspect_evals/stereoset",
  slug: "inspect-evals-stereoset", producer: "inspect_dataset", rule: "duplicate_inputs", dimension: "dataset",
  check: "duplicates", severity: "major", status: "hypothesis", summary: finding.summary,
  location: "sample:intersentence-0042:input", issue: null, github: null,
  first_seen: finding.first_seen, last_seen: finding.last_seen });

// 3. A suppression that matched two observations.
stereo.suppressed = [{ producer: "inspect_dataset", rule: "answer_length", count: 2, kind: "false_positive",
  author: "Matt Fisher", reason: "length outliers on struct answers", since: "2026-10-05" }];
const stereoEntry = index.evals.find((e) => e.slug === "inspect-evals-stereoset");
Object.assign(stereoEntry, { active: 2, suppressed: 2, issues: 1 });

// 4. An eval that was checked and has no findings; one producer skipped as a whole.
const lintRun = stereo.runs.find((r) => r.producer === "inspect_evals_lint");
const skipReason = "no logs matched the declared filter";
const gsm = {
  generated_at: stereo.generated_at, schema: 1, eval: "inspect_evals/gsm8k", slug: "inspect-evals-gsm8k",
  revision: stereo.revision, task_version: "2-A",
  inputs: { dataset: {}, logs: { used: [], excluded: [{ path: "hawk:logs/2026-10-01T00-00-00_gsm8k_mockllm.eval", reason: "mock model" }], count_excluded: [] }, comparison: {} },
  runs: [
    { run_id: "inspect_audit_header-20261007T064400Z-inspect-evals-gsm8k", producer: "inspect_audit_header",
      producer_version: "0.1.0", timestamp: "2026-10-07T06:44:00Z", duration_s: 0.4, skipped: skipReason,
      passing: 0, outcomes: [{ rule: "header", status: "skip", message: skipReason }] },
    { ...lintRun, run_id: "inspect_evals_lint-20261007T064401Z-inspect-evals-gsm8k", timestamp: "2026-10-07T06:44:01Z",
      passing: 20, outcomes: lintRun.outcomes.filter((o) => o.status === "skip") },
  ],
  groups: [], suppressed: [], issues: [],
};
write("evals/inspect-evals-gsm8k.json", gsm);
index.evals.push({ eval: gsm.eval, slug: gsm.slug, revision: gsm.revision, task_version: gsm.task_version,
  last_run: "2026-10-07T06:44:01Z",
  producers: Object.fromEntries(gsm.runs.map((r) => [r.producer, { run_id: r.run_id, timestamp: r.timestamp, skipped: r.skipped }])),
  active: 0, suppressed: 0, issues: 0 });
index.evals.sort((a, b) => a.eval.localeCompare(b.eval));
index.findings.sort((a, b) => a.eval.localeCompare(b.eval) || a.rule.localeCompare(b.rule) || a.id.localeCompare(b.id));
write("index.json", index);
write("evals/inspect-evals-stereoset.json", stereo);
EOF
```

`test/fixtures/README.md`:

```markdown
# Fixture export

`export/` is the first committed export from `Generality-Labs/inspect-evals-findings` (`main` at <short sha>, sweep of 2026-10-07: StereoSet with lint and dataset, SciCode and HLE with lint), with additions so every page state has data:

- StereoSet: issue `ISS-0001` with a GitHub link, accepting the IEBP011 finding; one `inspect_dataset` finding (`duplicate_inputs`, major, hypothesis, a sample location); one suppression matching two observations.
- GSM8K (`inspect-evals-gsm8k.json`): checked, no findings, one excluded log, and a header producer that was skipped as a whole.

The real files are the shapes the site is built against. Refresh them from a newer export when `export.py` changes, and re-apply the additions.
```

- [ ] **Step 2: Types**

`app/lib/types.ts`:

```ts
// The export the site reads, written by inspect_audit's findings/export.py.
// A field removed or renamed there bumps `schema`; a field added does not.

export const SUPPORTED_SCHEMA = 1;

export type Severity = "none" | "minor" | "major" | "critical";
export type Status = "hypothesis" | "supported" | "qualified" | "retracted";

export const SEVERITY_RANK: Record<Severity, number> = { critical: 0, major: 1, minor: 2, none: 3 };

export interface Revision {
  commit: string | null;
  package_version: string | null;
  dirty: boolean;
}

export interface ProducerRun {
  run_id: string;
  timestamp: string;
  skipped: string | null;
}

export interface EvalEntry {
  eval: string;
  slug: string;
  revision: Revision;
  task_version: string | null;
  last_run: string;
  producers: Record<string, ProducerRun>;
  active: number;
  suppressed: number;
  issues: number;
}

export interface FindingRow {
  id: string;
  fingerprint: string;
  eval: string;
  slug: string;
  producer: string;
  rule: string;
  dimension: string;
  check: string | null;
  severity: Severity;
  status: Status;
  summary: string;
  location: string;
  issue: string | null;
  github: string | null;
  first_seen: string;
  last_seen: string;
}

export interface IndexDoc {
  generated_at: string;
  schema: number;
  evals: EvalEntry[];
  findings: FindingRow[];
}

/** One of the export's location kinds; `code` carries file, line, end_line and column. */
export interface Location {
  kind: string;
  role: "primary" | "related";
  quote: string | null;
  [field: string]: unknown;
}

export interface Outcome {
  rule: string;
  status: "fail" | "skip";
  message: string;
}

export interface RunEntry {
  run_id: string;
  producer: string;
  producer_version: string | null;
  timestamp: string;
  duration_s: number | null;
  skipped: string | null;
  passing: number;
  outcomes: Outcome[];
}

export interface GroupFinding {
  id: string;
  fingerprint: string;
  status: Status;
  summary: string;
  locations: Location[];
  issue: string | null;
  first_seen: string;
  last_seen: string;
}

export interface Group {
  producer: string;
  rule: string;
  dimension: string;
  check: string | null;
  severity: Severity;
  count: number;
  findings: GroupFinding[];
}

export interface SuppressedGroup {
  producer: string | null;
  rule: string;
  count: number;
  kind: string;
  author: string;
  reason: string;
  since: string;
}

export interface IssueEntry {
  id: string;
  title: string;
  author: string;
  opened: string;
  reason: string | null;
  github: string | null;
  current: number;
}

export interface DatasetInput {
  path?: string;
  config?: string | null;
  split?: string | null;
  revision?: string | null;
  declared?: boolean;
  mode?: string;
  task?: string;
  samples?: number;
  scorers?: string[];
  inspect_ai?: string;
}

export interface LogEntry {
  path?: string;
  reason?: string;
}

export interface Inputs {
  dataset: DatasetInput;
  logs: { used: string[]; excluded: LogEntry[]; count_excluded: LogEntry[] };
  comparison: { commit?: string | null; package_version?: string | null; task_version?: string | null };
}

export interface EvalDoc {
  generated_at: string;
  schema: number;
  eval: string;
  slug: string;
  revision: Revision;
  task_version: string | null;
  inputs: Inputs;
  runs: RunEntry[];
  groups: Group[];
  suppressed: SuppressedGroup[];
  issues: IssueEntry[];
}
```

- [ ] **Step 3: Test helpers**

`test/app/fixtures.ts`:

```ts
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { vi } from "vitest";
import type { EvalDoc, IndexDoc } from "@/lib/types";

const DIR = path.resolve(import.meta.dirname, "../fixtures/export");

function read(file: string): unknown {
  return JSON.parse(readFileSync(path.join(DIR, file), "utf8"));
}

export function fixtureIndex(): IndexDoc {
  return read("index.json") as IndexDoc;
}

export function fixtureEval(slug: string): EvalDoc {
  return read(`evals/${slug}.json`) as EvalDoc;
}

/** Answers /data/* from the fixture export, as the Worker would; `overrides` replace a path's response. */
export function stubData(overrides: Record<string, () => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (overrides[url]) return overrides[url]();
    const file = url.replace(/^\/data\//, "");
    if (!url.startsWith("/data/") || !existsSync(path.join(DIR, file))) {
      return new Response("not found\n", { status: 404 });
    }
    return Response.json(read(file));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
```

- [ ] **Step 4: Write the failing tests**

`test/app/data.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { loadDoc } from "@/lib/data";
import type { IndexDoc } from "@/lib/types";
import { fixtureIndex, stubData } from "./fixtures";

describe("loadDoc", () => {
  it("returns the document", async () => {
    stubData();
    const loaded = await loadDoc<IndexDoc>("/data/index.json");
    expect(loaded).toEqual({ state: "ready", doc: fixtureIndex() });
  });

  it("reports a missing file as not-found", async () => {
    stubData();
    expect(await loadDoc("/data/evals/nope.json")).toEqual({ state: "not-found" });
  });

  it("refuses a schema newer than it understands", async () => {
    stubData({ "/data/index.json": () => Response.json({ ...fixtureIndex(), schema: 2 }) });
    expect(await loadDoc("/data/index.json")).toEqual({ state: "too-new" });
  });

  it("explains a server failure", async () => {
    stubData({ "/data/index.json": () => new Response("bad gateway", { status: 502 }) });
    const loaded = await loadDoc("/data/index.json");
    expect(loaded).toMatchObject({ state: "failed" });
    expect(loaded.state === "failed" && loaded.message).toContain("502");
  });

  it("suggests reloading when the answer is not JSON (an expired Access session)", async () => {
    stubData({ "/data/index.json": () => new Response("<html>Sign in</html>", { status: 200 }) });
    const loaded = await loadDoc("/data/index.json");
    expect(loaded.state === "failed" && loaded.message).toContain("Reload the page");
  });

  it("explains a network failure", async () => {
    stubData({
      "/data/index.json": () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const loaded = await loadDoc("/data/index.json");
    expect(loaded.state === "failed" && loaded.message).toContain("Reload the page");
  });
});
```

`test/app/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatTime, locationKeyLabel, locationLabel, shortCommit } from "@/lib/format";

describe("format", () => {
  it("formats export timestamps in UTC to the minute", () => {
    expect(formatTime("2026-10-07T06:43:47Z")).toBe("2026-10-07 06:43 UTC");
  });

  it("shortens commits", () => {
    expect(shortCommit("dbd3dd25e81d5f5fc4d19860b496a34176da6c61")).toBe("dbd3dd25e");
    expect(shortCommit(null)).toBe("unknown");
  });

  it("drops the code: prefix from location keys", () => {
    expect(locationKeyLabel("code:src/a.py:46")).toBe("src/a.py:46");
    expect(locationKeyLabel("sample:x:input")).toBe("sample:x:input");
  });

  it("labels code locations by file and line", () => {
    const loc = { kind: "code", role: "primary", quote: null, file: "src/a.py", line: 46 } as const;
    expect(locationLabel(loc)).toBe("src/a.py:46");
    expect(locationLabel({ ...loc, line: null })).toBe("src/a.py");
  });

  it("labels other locations by kind and their fields", () => {
    const loc = { kind: "sample", role: "primary", quote: null, sample_id: "s-42", field: "input" } as const;
    expect(locationLabel(loc)).toBe("sample s-42 input");
  });
});
```

Run: `npx vitest run --project unit` — Expected: FAIL, modules not found.

- [ ] **Step 5: Implement**

`app/lib/data.ts`:

```ts
import { useEffect, useState } from "react";
import { SUPPORTED_SCHEMA } from "./types";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "ready"; doc: T }
  | { state: "not-found" }
  | { state: "too-new" }
  | { state: "failed"; message: string };

/** Fetch one export document. Never throws: every failure is a state the page can show. */
export async function loadDoc<T extends { schema: number }>(path: string): Promise<Loaded<T>> {
  let response: Response;
  try {
    response = await fetch(path);
  } catch {
    return { state: "failed", message: "The data could not be fetched. Reload the page to try again." };
  }
  if (response.status === 404) return { state: "not-found" };
  if (!response.ok) {
    return { state: "failed", message: `The data service answered ${response.status}. Try again in a few minutes.` };
  }
  let doc: T;
  try {
    doc = (await response.json()) as T;
  } catch {
    // Behind Access, an expired session answers with the sign-in page.
    return { state: "failed", message: "The data could not be read; your sign-in may have expired. Reload the page." };
  }
  return doc.schema > SUPPORTED_SCHEMA ? { state: "too-new" } : { state: "ready", doc };
}

export function useDoc<T extends { schema: number }>(path: string): Loaded<T> {
  const [loaded, setLoaded] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    let current = true;
    setLoaded({ state: "loading" });
    loadDoc<T>(path).then((result) => {
      if (current) setLoaded(result);
    });
    return () => {
      current = false;
    };
  }, [path]);
  return loaded;
}
```

`app/lib/format.ts`:

```ts
import type { Location } from "./types";

/** Export timestamps are UTC ISO strings; show them to the minute, as UTC, the same for every reader. */
export function formatTime(iso: string): string {
  return `${iso.slice(0, 16).replace("T", " ")} UTC`;
}

export function shortCommit(commit: string | null): string {
  return commit ? commit.slice(0, 9) : "unknown";
}

export function locationKeyLabel(key: string): string {
  return key.replace(/^code:/, "");
}

const NOT_SHOWN = new Set(["kind", "role", "quote", "end_line", "column"]);

export function locationLabel(location: Location): string {
  if (location.kind === "code") {
    return location.line ? `${location.file}:${location.line}` : String(location.file);
  }
  const fields = Object.entries(location)
    .filter(([key, value]) => !NOT_SHOWN.has(key) && value !== null && value !== undefined)
    .map(([, value]) => String(value));
  return [location.kind, ...fields].join(" ");
}
```

`app/components/LoadState.tsx`:

```tsx
import type { ReactNode } from "react";
import type { Loaded } from "@/lib/data";

/** What a page shows until its document is ready. */
export function LoadState({
  loaded,
  notFound,
}: {
  loaded: Exclude<Loaded<unknown>, { state: "ready" }>;
  notFound: ReactNode;
}) {
  switch (loaded.state) {
    case "loading":
      return <p className="text-muted-foreground">Loading…</p>;
    case "too-new":
      return <p>This site needs updating to read the current export.</p>;
    case "not-found":
      return <>{notFound}</>;
    case "failed":
      return <p className="text-destructive">{loaded.message}</p>;
  }
}
```

- [ ] **Step 6: Run the tests**

Run: `npm run typecheck && npx vitest run --project unit`
Expected: PASS. Also `npx tsc --noEmit -p tsconfig.app.json` accepts the fixture casts.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(app): export types, data loading and the fixture export" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 5: The findings table

**Files:**

- Create: `app/lib/filters.ts`, `app/components/Badges.tsx`, `app/findings/FindingsPage.tsx`, `app/findings/columns.tsx`, `app/findings/FilterBar.tsx`, `test/app/filters.test.ts`, `test/app/findings.test.tsx`
- Modify: `app/App.tsx` (index route → `<FindingsPage />`)

**Interfaces:**

- Consumes: `useDoc`, `LoadState`, `formatTime`, `locationKeyLabel`, `FindingRow`, `IndexDoc`, `SEVERITY_RANK`; `stubData`, `fixtureIndex`.
- Produces: `Filters`, `NO_FILTERS`, `REVIEWED`, `FACETS`, `Facet`, `NO_CHECK`, `filtersFromParams(params: URLSearchParams): Filters`, `filtersToParams(filters: Filters): URLSearchParams`, `applyFilters(rows: FindingRow[], filters: Filters): FindingRow[]`, `facetOptions(rows: FindingRow[], facet: Facet, selected?: string): string[]`, `defaultOrder(rows: FindingRow[]): FindingRow[]`; `<SeverityBadge value />`, `<StatusBadge value />`; `<FindingsPage />`.

- [ ] **Step 1: Write the failing filter tests**

`test/app/filters.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  applyFilters,
  defaultOrder,
  facetOptions,
  filtersFromParams,
  filtersToParams,
  NO_FILTERS,
} from "@/lib/filters";
import { fixtureIndex } from "./fixtures";

const rows = fixtureIndex().findings;

describe("applyFilters", () => {
  it("keeps everything with no filters", () => {
    expect(applyFilters(rows, NO_FILTERS)).toHaveLength(rows.length);
  });

  it.each([
    ["producer", "inspect_dataset", 1],
    ["dimension", "grading", 2],
    ["check", "duplicates", 1],
    ["check", "none", rows.length - 1],
    ["severity", "major", 1],
    ["status", "hypothesis", 1],
  ] as const)("narrows by %s=%s", (facet, value, count) => {
    const kept = applyFilters(rows, { ...NO_FILTERS, [facet]: value });
    expect(kept).toHaveLength(count);
  });

  it("matches the eval as case-insensitive text", () => {
    const kept = applyFilters(rows, { ...NO_FILTERS, eval: "  SciCode " });
    expect(kept.map((r) => r.slug)).toEqual(["inspect-evals-scicode", "inspect-evals-scicode"]);
  });

  it("splits accepted and unreviewed", () => {
    const accepted = applyFilters(rows, { ...NO_FILTERS, reviewed: "accepted" });
    const unreviewed = applyFilters(rows, { ...NO_FILTERS, reviewed: "unreviewed" });
    expect(accepted.map((r) => r.issue)).toEqual(["ISS-0001"]);
    expect(accepted.length + unreviewed.length).toBe(rows.length);
  });

  it("combines filters", () => {
    const kept = applyFilters(rows, { ...NO_FILTERS, eval: "stereoset", severity: "minor" });
    expect(kept.map((r) => r.rule)).toEqual(["IEBP011"]);
  });
});

describe("query string", () => {
  it("round-trips, leaving defaults out", () => {
    const filters = { ...NO_FILTERS, eval: "hle", severity: "major", reviewed: "accepted" as const };
    const params = filtersToParams(filters);
    expect(params.toString()).toBe("eval=hle&severity=major&reviewed=accepted");
    expect(filtersFromParams(params)).toEqual(filters);
  });

  it("falls back to any for an unknown reviewed value and keeps other unknown values", () => {
    const filters = filtersFromParams(new URLSearchParams("reviewed=xyz&severity=bogus"));
    expect(filters.reviewed).toBe("any");
    expect(filters.severity).toBe("bogus");
    expect(applyFilters(rows, filters)).toEqual([]);
  });
});

describe("facetOptions", () => {
  it("lists severities most severe first", () => {
    expect(facetOptions(rows, "severity")).toEqual(["major", "minor"]);
  });

  it("names a null check", () => {
    expect(facetOptions(rows, "check")).toEqual(["duplicates", "none"]);
  });

  it("keeps a selected value the data does not have, so the select can show it", () => {
    expect(facetOptions(rows, "severity", "bogus")).toEqual(["major", "minor", "bogus"]);
  });
});

describe("defaultOrder", () => {
  it("puts reviewed rows first, then sorts by eval and rule", () => {
    const ordered = defaultOrder(rows);
    expect(ordered[0].issue).toBe("ISS-0001");
    const rest = ordered.slice(1).map((r) => `${r.eval} ${r.rule}`);
    expect(rest).toEqual([...rest].sort());
  });
});
```

Run: `npx vitest run test/app/filters.test.ts` — Expected: FAIL, module not found.

- [ ] **Step 2: Implement filters**

`app/lib/filters.ts`:

```ts
import { type FindingRow, SEVERITY_RANK, type Severity } from "./types";

export const REVIEWED = ["any", "accepted", "unreviewed"] as const;
export type Reviewed = (typeof REVIEWED)[number];

export const FACETS = ["producer", "dimension", "check", "severity", "status"] as const;
export type Facet = (typeof FACETS)[number];

export interface Filters extends Record<Facet, string> {
  eval: string;
  reviewed: Reviewed;
}

export const NO_FILTERS: Filters = {
  eval: "",
  producer: "",
  dimension: "",
  check: "",
  severity: "",
  status: "",
  reviewed: "any",
};

/** The value a row with no check has in the check filter. */
export const NO_CHECK = "none";

function facetValue(row: FindingRow, facet: Facet): string {
  return row[facet] ?? NO_CHECK;
}

export function filtersFromParams(params: URLSearchParams): Filters {
  const reviewed = params.get("reviewed");
  const filters: Filters = { ...NO_FILTERS, eval: params.get("eval") ?? "" };
  for (const facet of FACETS) filters[facet] = params.get(facet) ?? "";
  filters.reviewed = REVIEWED.includes(reviewed as Reviewed) ? (reviewed as Reviewed) : "any";
  return filters;
}

export function filtersToParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of ["eval", ...FACETS, "reviewed"] as const) {
    if (filters[key] && filters[key] !== NO_FILTERS[key]) params.set(key, filters[key]);
  }
  return params;
}

export function applyFilters(rows: FindingRow[], filters: Filters): FindingRow[] {
  const needle = filters.eval.trim().toLowerCase();
  return rows.filter(
    (row) =>
      (!needle || row.eval.toLowerCase().includes(needle)) &&
      FACETS.every((facet) => !filters[facet] || facetValue(row, facet) === filters[facet]) &&
      (filters.reviewed === "any" || (filters.reviewed === "accepted") === (row.issue !== null)),
  );
}

/** The values a facet's select offers: those in the data, plus a selected one that is not. */
export function facetOptions(rows: FindingRow[], facet: Facet, selected = ""): string[] {
  const values = [...new Set(rows.map((row) => facetValue(row, facet)))];
  if (facet === "severity") {
    values.sort((a, b) => SEVERITY_RANK[a as Severity] - SEVERITY_RANK[b as Severity]);
  } else {
    values.sort();
  }
  return selected && !values.includes(selected) ? [...values, selected] : values;
}

/** The table's order before any column is sorted: reviewed rows first, then eval and rule. */
export function defaultOrder(rows: FindingRow[]): FindingRow[] {
  return [...rows].sort(
    (a, b) =>
      Number(b.issue !== null) - Number(a.issue !== null) ||
      a.eval.localeCompare(b.eval) ||
      a.rule.localeCompare(b.rule) ||
      a.id.localeCompare(b.id),
  );
}
```

Run: `npx vitest run test/app/filters.test.ts` — Expected: PASS.

- [ ] **Step 3: Write the failing page tests**

`test/app/findings.test.tsx`:

```tsx
// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import { App } from "@/App";
import { fixtureIndex, stubData } from "./fixtures";

const total = fixtureIndex().findings.length;

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <App />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function bodyRows() {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

describe("findings table", () => {
  it("shows every finding, the count and when the data is from", async () => {
    stubData();
    renderAt("/");
    expect(await screen.findByText(`${total} of ${total} findings`)).toBeInTheDocument();
    expect(screen.getByText(/data from 2026-10-07 \d\d:\d\d UTC/)).toBeInTheDocument();
    expect(bodyRows()).toHaveLength(total);
  });

  it("puts the reviewed row first and links its issue to GitHub", async () => {
    stubData();
    renderAt("/");
    await screen.findByText(`${total} of ${total} findings`);
    const link = within(bodyRows()[0]).getByRole("link", { name: "ISS-0001" });
    expect(link).toHaveAttribute("href", "https://github.com/UKGovernmentBEIS/inspect_evals/issues/2650");
  });

  it.each([
    ["Producer", "inspect_dataset", 1],
    ["Dimension", "grading", 2],
    ["Check", "duplicates", 1],
    ["Severity", "major", 1],
    ["Status", "hypothesis", 1],
    ["Reviewed", "accepted", 1],
  ])("narrows by %s and records it in the query string", async (label, value, count) => {
    stubData();
    renderAt("/");
    await screen.findByText(`${total} of ${total} findings`);
    await userEvent.selectOptions(screen.getByLabelText(label), value);
    expect(screen.getByText(`${count} of ${total} findings`)).toBeInTheDocument();
    expect(bodyRows()).toHaveLength(count);
    expect(screen.getByTestId("location")).toHaveTextContent(`${label.toLowerCase()}=${value}`);
  });

  it("narrows by eval text", async () => {
    stubData();
    renderAt("/");
    await screen.findByText(`${total} of ${total} findings`);
    await userEvent.type(screen.getByLabelText("Eval"), "scicode");
    expect(screen.getByText(`2 of ${total} findings`)).toBeInTheDocument();
  });

  it("restores filters from the query string", async () => {
    stubData();
    renderAt("/?severity=minor&eval=hle");
    expect(await screen.findByText(`2 of ${total} findings`)).toBeInTheDocument();
    expect(screen.getByLabelText("Severity")).toHaveValue("minor");
    expect(screen.getByLabelText("Eval")).toHaveValue("hle");
  });

  it("shows an unknown filter value and offers to clear it", async () => {
    stubData();
    renderAt("/?severity=bogus&reviewed=xyz");
    expect(await screen.findByText(`0 of ${total} findings`)).toBeInTheDocument();
    expect(screen.getByLabelText("Severity")).toHaveValue("bogus");
    expect(screen.getByLabelText("Reviewed")).toHaveValue("any");
    expect(screen.getByText("No findings match these filters.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText(`${total} of ${total} findings`)).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
  });

  it("sorts on a column and flips", async () => {
    stubData();
    renderAt("/");
    await screen.findByText(`${total} of ${total} findings`);
    const rules = () => bodyRows().map((row) => within(row).getAllByRole("cell")[2].textContent);
    await userEvent.click(screen.getByRole("button", { name: "Rule" }));
    const ascending = rules();
    expect(ascending).toEqual([...ascending].sort());
    await userEvent.click(screen.getByRole("button", { name: "Rule" }));
    expect(rules()).toEqual([...ascending].reverse());
  });

  it("sorts severity by rank, not alphabetically", async () => {
    stubData();
    renderAt("/");
    await screen.findByText(`${total} of ${total} findings`);
    await userEvent.click(screen.getByRole("button", { name: "Severity" }));
    expect(within(bodyRows()[0]).getByText("major")).toBeInTheDocument();
  });

  it("links each eval to its page", async () => {
    stubData();
    renderAt("/");
    await screen.findByText(`${total} of ${total} findings`);
    const link = within(bodyRows()[0]).getByRole("link", { name: "inspect_evals/stereoset" });
    expect(link).toHaveAttribute("href", "/evals/inspect-evals-stereoset");
  });

  it("shows the update message for a newer schema and nothing else", async () => {
    stubData({ "/data/index.json": () => Response.json({ ...fixtureIndex(), schema: 2 }) });
    renderAt("/");
    expect(
      await screen.findByText("This site needs updating to read the current export."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the error when the data service fails", async () => {
    stubData({ "/data/index.json": () => new Response("x", { status: 502 }) });
    renderAt("/");
    expect(await screen.findByText(/answered 502/)).toBeInTheDocument();
  });
});
```

(The "Severity" sort test starts from the default order, where the major row is not first, because the reviewed stereoset lint row is. The first click sorts ascending by rank, critical before major before minor.)

Run: `npx vitest run test/app/findings.test.tsx` — Expected: FAIL.

- [ ] **Step 4: Implement badges, columns, filter bar and page**

`app/components/Badges.tsx`:

```tsx
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Severity, Status } from "@/lib/types";

// Tinted washes with a strong text colour read on both themes.
const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "border-red-500/50 bg-red-500/15 text-red-700 dark:text-red-300",
  major: "border-orange-500/50 bg-orange-500/15 text-orange-700 dark:text-orange-300",
  minor: "border-wash-edge bg-wash text-foreground",
  none: "border-border bg-transparent text-muted-foreground",
};

const STATUS_CLASS: Record<Status, string> = {
  hypothesis: "border-dashed border-border bg-transparent text-muted-foreground",
  supported: "border-wash-edge bg-wash-strong text-foreground",
  qualified: "border-sky-500/50 bg-sky-500/15 text-sky-700 dark:text-sky-300",
  retracted: "border-border bg-transparent text-muted-foreground line-through",
};

export function SeverityBadge({ value }: { value: Severity }) {
  return <Badge variant="outline" className={cn(SEVERITY_CLASS[value])}>{value}</Badge>;
}

export function StatusBadge({ value }: { value: Status }) {
  return <Badge variant="outline" className={cn(STATUS_CLASS[value])}>{value}</Badge>;
}
```

`app/findings/columns.tsx`:

```tsx
import { createColumnHelper } from "@tanstack/react-table";
import { Link } from "react-router";
import { SeverityBadge, StatusBadge } from "@/components/Badges";
import { formatTime, locationKeyLabel } from "@/lib/format";
import { type FindingRow, SEVERITY_RANK } from "@/lib/types";

const column = createColumnHelper<FindingRow>();

export const columns = [
  column.accessor("eval", {
    header: "Eval",
    cell: ({ row, getValue }) => <Link to={`/evals/${row.original.slug}`}>{getValue()}</Link>,
  }),
  column.accessor("producer", { header: "Producer" }),
  column.accessor("rule", { header: "Rule" }),
  column.accessor("dimension", { header: "Dimension" }),
  column.accessor((row) => row.check ?? "", { id: "check", header: "Check" }),
  column.accessor("severity", {
    header: "Severity",
    cell: ({ getValue }) => <SeverityBadge value={getValue()} />,
    sortingFn: (a, b) =>
      SEVERITY_RANK[a.original.severity] - SEVERITY_RANK[b.original.severity],
  }),
  column.accessor("status", {
    header: "Status",
    cell: ({ getValue }) => <StatusBadge value={getValue()} />,
  }),
  column.accessor("summary", { header: "Summary", meta: { wide: true } }),
  column.accessor("location", {
    header: "Location",
    cell: ({ getValue }) => <code className="text-xs">{locationKeyLabel(getValue())}</code>,
  }),
  column.accessor((row) => row.issue ?? "", {
    id: "reviewed",
    header: "Reviewed",
    cell: ({ row }) => {
      const { issue, github } = row.original;
      if (!issue) return null;
      return github ? (
        <a href={github} target="_blank" rel="noreferrer">
          {issue}
        </a>
      ) : (
        issue
      );
    },
  }),
  column.accessor("first_seen", { header: "First seen", cell: ({ getValue }) => formatTime(getValue()) }),
  column.accessor("last_seen", { header: "Last seen", cell: ({ getValue }) => formatTime(getValue()) }),
];
```

`app/findings/FilterBar.tsx`:

```tsx
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { FACETS, type Facet, type Filters, facetOptions, NO_FILTERS, REVIEWED } from "@/lib/filters";
import type { FindingRow } from "@/lib/types";

const LABEL: Record<Facet, string> = {
  producer: "Producer",
  dimension: "Dimension",
  check: "Check",
  severity: "Severity",
  status: "Status",
};

export function FilterBar({
  rows,
  filters,
  onChange,
}: {
  rows: FindingRow[];
  filters: Filters;
  onChange: (filters: Filters) => void;
}) {
  const set = (key: keyof Filters, value: string) => onChange({ ...filters, [key]: value });
  const active = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="grid gap-1 text-sm">
        <span className="text-muted-foreground">Eval</span>
        <Input
          className="w-56"
          value={filters.eval}
          placeholder="any eval"
          onChange={(event) => set("eval", event.target.value)}
        />
      </label>
      {FACETS.map((facet) => (
        <label key={facet} className="grid gap-1 text-sm">
          <span className="text-muted-foreground">{LABEL[facet]}</span>
          <NativeSelect value={filters[facet]} onChange={(event) => set(facet, event.target.value)}>
            <NativeSelectOption value="">Any</NativeSelectOption>
            {facetOptions(rows, facet, filters[facet]).map((value) => (
              <NativeSelectOption key={value} value={value}>
                {value}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
      ))}
      <label className="grid gap-1 text-sm">
        <span className="text-muted-foreground">Reviewed</span>
        <NativeSelect value={filters.reviewed} onChange={(event) => set("reviewed", event.target.value)}>
          {REVIEWED.map((value) => (
            <NativeSelectOption key={value} value={value}>
              {value}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </label>
      {active && (
        <Button variant="ghost" onClick={() => onChange(NO_FILTERS)}>
          Clear filters
        </Button>
      )}
    </div>
  );
}
```

If the generated `NativeSelect` wraps the `<select>` so that the `<label>` does not associate with it, pass an `id` and use `htmlFor` instead; the tests find each control by its label text.

`app/findings/FindingsPage.tsx`:

```tsx
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { LoadState } from "@/components/LoadState";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDoc } from "@/lib/data";
import { applyFilters, defaultOrder, type Filters, filtersFromParams, filtersToParams } from "@/lib/filters";
import { formatTime } from "@/lib/format";
import type { IndexDoc } from "@/lib/types";
import { columns } from "./columns";
import { FilterBar } from "./FilterBar";

export function FindingsPage() {
  const loaded = useDoc<IndexDoc>("/data/index.json");
  if (loaded.state !== "ready") {
    return <LoadState loaded={loaded} notFound={<p>No export has been published yet.</p>} />;
  }
  return <FindingsTable doc={loaded.doc} />;
}

function FindingsTable({ doc }: { doc: IndexDoc }) {
  const [params, setParams] = useSearchParams();
  const query = params.toString();
  const filters = useMemo(() => filtersFromParams(new URLSearchParams(query)), [query]);
  const ordered = useMemo(() => defaultOrder(doc.findings), [doc]);
  const rows = useMemo(() => applyFilters(ordered, filters), [ordered, filters]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const onChange = (next: Filters) => setParams(filtersToParams(next), { replace: true });

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">Findings</h1>
        <p className="text-sm text-muted-foreground">data from {formatTime(doc.generated_at)}</p>
      </div>
      <FilterBar rows={doc.findings} filters={filters} onChange={onChange} />
      <p className="text-sm text-muted-foreground">
        {rows.length} of {doc.findings.length} findings
      </p>
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 font-medium"
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sorted === "asc" && <ArrowUp className="size-3" aria-hidden />}
                      {sorted === "desc" && <ArrowDown className="size-3" aria-hidden />}
                    </button>
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <TableCell
                  key={cell.id}
                  className={cell.column.id === "summary" ? "min-w-80 whitespace-normal" : undefined}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={columns.length} className="text-muted-foreground">
                {doc.findings.length === 0
                  ? `No active findings across ${doc.evals.length} evals.`
                  : "No findings match these filters."}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
```

Drop the `meta: { wide: true }` from `columns.tsx` if TypeScript rejects it (the summary width is set by column id above). In `app/App.tsx`, route `index` to `<FindingsPage />`.

- [ ] **Step 5: Run the tests**

Run: `npm run typecheck && npx vitest run --project unit && npx biome check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(app): findings table with linkable filters and sorting" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 6: The eval page

**Files:**

- Create: `app/eval/EvalPage.tsx`, `app/eval/sections.tsx`, `app/eval/checks.ts`, `test/app/eval.test.tsx`
- Modify: `app/App.tsx` (route `evals/:slug` → `<EvalPage />`)

**Interfaces:**

- Consumes: `useDoc`, `LoadState`, `formatTime`, `shortCommit`, `locationLabel`, `SeverityBadge`, `StatusBadge`, `EvalDoc`, `RunEntry`; `stubData`, `fixtureEval`; `renderAt` pattern from Task 5.
- Produces: `checkCounts(runs: RunEntry[]): { ran: number; skipped: number }`; `<EvalPage />`.

- [ ] **Step 1: Write the failing tests**

`test/app/eval.test.tsx`:

```tsx
// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { App } from "@/App";
import { checkCounts } from "@/eval/checks";
import { fixtureEval, stubData } from "./fixtures";

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <App />
    </MemoryRouter>,
  );
}

function section(name: string) {
  return screen.getByRole("region", { name });
}

describe("eval page", () => {
  it("shows the header: eval, revision, task version, last run per producer", async () => {
    stubData();
    renderAt("/evals/inspect-evals-stereoset");
    expect(await screen.findByRole("heading", { level: 1, name: "inspect_evals/stereoset" })).toBeInTheDocument();
    expect(screen.getByText(/dbd3dd25e/)).toBeInTheDocument();
    expect(screen.getByText(/4-A/)).toBeInTheDocument();
    const runs = section("Runs");
    expect(within(runs).getByText("inspect_dataset")).toBeInTheDocument();
    expect(within(runs).getByText("inspect_evals_lint")).toBeInTheDocument();
  });

  it("shows the inputs", async () => {
    stubData();
    renderAt("/evals/inspect-evals-stereoset");
    const inputs = await screen.findByRole("region", { name: "Inputs" });
    expect(within(inputs).getByText(/Dataset scanned: inspect_evals\/stereoset /)).toBeInTheDocument();
    expect(within(inputs).getByText(/through the task's own loader/)).toBeInTheDocument();
    expect(within(inputs).getByText(/inspect_evals\/multiple_choice_scorer/)).toBeInTheDocument();
    expect(within(inputs).getByText(/No logs examined/)).toBeInTheDocument();
    expect(within(inputs).getByText(/No header comparison ran/)).toBeInTheDocument();
  });

  it("groups findings by rule with counts, summaries, locations and statuses", async () => {
    stubData();
    renderAt("/evals/inspect-evals-stereoset");
    const findings = await screen.findByRole("region", { name: "Findings" });
    const groups = within(findings).getAllByRole("article");
    expect(groups).toHaveLength(2);
    expect(within(groups[0]).getByText("duplicate_inputs")).toBeInTheDocument();
    expect(within(groups[0]).getByText("sample intersentence-0042 input")).toBeInTheDocument();
    expect(within(groups[0]).getByText("hypothesis")).toBeInTheDocument();
    expect(within(groups[1]).getByText("IEBP011")).toBeInTheDocument();
    expect(within(groups[1]).getByText("src/inspect_evals/stereoset/stereoset.py:46")).toBeInTheDocument();
    expect(within(groups[1]).getByText(/shuffle defaults to True/)).toBeInTheDocument();
  });

  it("shows suppressions and issues", async () => {
    stubData();
    renderAt("/evals/inspect-evals-stereoset");
    const suppressed = await screen.findByRole("region", { name: "Suppressed" });
    expect(within(suppressed).getByText("answer_length")).toBeInTheDocument();
    expect(within(suppressed).getByText("length outliers on struct answers")).toBeInTheDocument();
    expect(within(suppressed).getByText("Matt Fisher")).toBeInTheDocument();
    const issues = section("Issues");
    expect(within(issues).getByText("StereoSet shuffles without a seed")).toBeInTheDocument();
    expect(within(issues).getByRole("link", { name: /GitHub/ })).toHaveAttribute(
      "href",
      "https://github.com/UKGovernmentBEIS/inspect_evals/issues/2650",
    );
  });

  it("says an eval with no findings was checked, and shows a skipped producer's reason in red", async () => {
    stubData();
    renderAt("/evals/inspect-evals-gsm8k");
    const { ran, skipped } = checkCounts(fixtureEval("inspect-evals-gsm8k").runs);
    expect(
      await screen.findByText(`No findings. ${ran} checks ran, ${skipped} skipped.`),
    ).toBeInTheDocument();
    expect(screen.getByText("no logs matched the declared filter")).toHaveClass("text-destructive");
    expect(screen.getByText(/mock model/)).toBeInTheDocument();
  });

  it("shows a not-found message with a way back for an unknown slug", async () => {
    stubData();
    renderAt("/evals/no-such-eval");
    expect(await screen.findByText(/No eval called no-such-eval/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the findings" })).toHaveAttribute("href", "/");
  });

  it("shows the update message for a newer schema", async () => {
    stubData({
      "/data/evals/inspect-evals-hle.json": () =>
        Response.json({ ...fixtureEval("inspect-evals-hle"), schema: 2 }),
    });
    renderAt("/evals/inspect-evals-hle");
    expect(
      await screen.findByText("This site needs updating to read the current export."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});

describe("checkCounts", () => {
  it("counts passing and failing outcomes as ran, skips as skipped", () => {
    expect(
      checkCounts([
        { run_id: "a", producer: "p", producer_version: null, timestamp: "t", duration_s: null,
          skipped: null, passing: 3, outcomes: [
            { rule: "x", status: "fail", message: "" },
            { rule: "y", status: "skip", message: "" },
          ] },
      ]),
    ).toEqual({ ran: 4, skipped: 1 });
  });
});
```

Run: `npx vitest run test/app/eval.test.tsx` — Expected: FAIL.

- [ ] **Step 2: Implement**

`app/eval/checks.ts`:

```ts
import type { RunEntry } from "@/lib/types";

/** Checks that ran (passed or failed) and checks that were skipped, across every run. */
export function checkCounts(runs: RunEntry[]): { ran: number; skipped: number } {
  let ran = 0;
  let skipped = 0;
  for (const run of runs) {
    ran += run.passing;
    for (const outcome of run.outcomes) {
      if (outcome.status === "skip") skipped += 1;
      else ran += 1;
    }
  }
  return { ran, skipped };
}
```

`app/eval/sections.tsx`:

```tsx
import type { ReactNode } from "react";
import { SeverityBadge, StatusBadge } from "@/components/Badges";
import { formatTime, locationLabel } from "@/lib/format";
import type { EvalDoc, Inputs } from "@/lib/types";
import { checkCounts } from "./checks";

export function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = `section-${title.toLowerCase()}`;
  return (
    <section aria-labelledby={id} className="grid gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function RunsSection({ doc }: { doc: EvalDoc }) {
  return (
    <Section title="Runs">
      <ul className="grid gap-1 text-sm">
        {doc.runs.map((run) => (
          <li key={run.run_id}>
            <span className="font-medium">{run.producer}</span>
            {run.producer_version && <span className="text-muted-foreground"> {run.producer_version}</span>}
            <span className="text-muted-foreground">, last run {formatTime(run.timestamp)}</span>
            {run.skipped && (
              <>
                {", skipped: "}
                <span className="text-destructive">{run.skipped}</span>
              </>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function datasetLine(dataset: Inputs["dataset"]): string {
  const where =
    dataset.mode === "task"
      ? "through the task's own loader"
      : (["config", "split", "revision"] as const)
          .filter((key) => dataset[key])
          .map((key) => `${key} ${dataset[key]}`)
          .join(", ");
  const origin = dataset.declared ? "declared in the pilot config" : "inferred from eval.yaml";
  const samples = dataset.samples !== undefined ? ` ${dataset.samples} samples.` : "";
  return `Dataset scanned: ${dataset.path}${where ? ` (${where})` : ""}, ${origin}.${samples}`;
}

export function InputsSection({ inputs }: { inputs: Inputs }) {
  const { dataset, logs, comparison } = inputs;
  const anyLogs = logs.used.length + logs.excluded.length + logs.count_excluded.length > 0;
  return (
    <Section title="Inputs">
      <ul className="grid gap-1 text-sm">
        {dataset.path ? (
          <>
            <li>{datasetLine(dataset)}</li>
            {dataset.mode === "task" && (
              <li>
                Scorers the scan assumed:{" "}
                {dataset.scorers?.length
                  ? dataset.scorers.join(", ")
                  : "none; the task declares no registered scorer"}
                .
              </li>
            )}
          </>
        ) : (
          <li>No dataset scan ran.</li>
        )}
        {anyLogs ? (
          <li>
            Logs: {logs.used.length} used, {logs.excluded.length} excluded,{" "}
            {logs.count_excluded.length} not compared for sample count.
            <ul className="ml-4 list-disc">
              {logs.used.map((path) => (
                <li key={path}>
                  used <code>{path}</code>
                </li>
              ))}
              {logs.excluded.map((entry) => (
                <li key={`x-${entry.path}`}>
                  excluded <code>{entry.path}</code>: {entry.reason}
                </li>
              ))}
              {logs.count_excluded.map((entry) => (
                <li key={`c-${entry.path}`}>
                  not compared <code>{entry.path}</code>: {entry.reason}
                </li>
              ))}
            </ul>
          </li>
        ) : (
          <li>No logs examined.</li>
        )}
        {comparison.commit || comparison.package_version ? (
          <li>
            Header checks compared against {comparison.commit ?? comparison.package_version}, task
            version {comparison.task_version ?? "unknown"}.
          </li>
        ) : (
          <li>No header comparison ran.</li>
        )}
      </ul>
    </Section>
  );
}

export function FindingsSection({ doc }: { doc: EvalDoc }) {
  if (doc.groups.length === 0) {
    const { ran, skipped } = checkCounts(doc.runs);
    return (
      <Section title="Findings">
        <p>
          No findings. {ran} checks ran, {skipped} skipped.
        </p>
      </Section>
    );
  }
  return (
    <Section title="Findings">
      {doc.groups.map((group) => (
        <article key={`${group.producer}/${group.rule}`} className="grid gap-2 rounded-lg border p-3">
          <header className="flex flex-wrap items-center gap-2 text-sm">
            <code className="font-semibold">{group.rule}</code>
            <SeverityBadge value={group.severity} />
            <span className="text-muted-foreground">
              {group.producer} · {group.dimension}
              {group.check ? ` · ${group.check}` : ""} · {group.count}
            </span>
          </header>
          <ul className="grid gap-2 text-sm">
            {group.findings.map((finding) => (
              <li key={finding.id} className="grid gap-1">
                <span>{finding.summary}</span>
                <span className="flex flex-wrap items-center gap-2 text-muted-foreground">
                  {finding.locations
                    .filter((location) => location.role === "primary")
                    .map((location) => (
                      <code key={locationLabel(location)} className="text-xs">
                        {locationLabel(location)}
                      </code>
                    ))}
                  <StatusBadge value={finding.status} />
                  {finding.issue && <span>{finding.issue}</span>}
                </span>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </Section>
  );
}

export function SuppressedSection({ doc }: { doc: EvalDoc }) {
  if (doc.suppressed.length === 0) return null;
  return (
    <Section title="Suppressed">
      <table className="text-sm">
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="pr-4 font-medium">Rule</th>
            <th className="pr-4 font-medium">Count</th>
            <th className="pr-4 font-medium">Kind</th>
            <th className="pr-4 font-medium">Reason</th>
            <th className="pr-4 font-medium">Author</th>
            <th className="font-medium">Since</th>
          </tr>
        </thead>
        <tbody>
          {doc.suppressed.map((row) => (
            <tr key={`${row.producer}/${row.rule}`}>
              <td className="pr-4">
                <code>{row.rule}</code>
              </td>
              <td className="pr-4">{row.count}</td>
              <td className="pr-4">{row.kind}</td>
              <td className="pr-4">{row.reason}</td>
              <td className="pr-4">{row.author}</td>
              <td>{row.since}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function IssuesSection({ doc }: { doc: EvalDoc }) {
  if (doc.issues.length === 0) return null;
  return (
    <Section title="Issues">
      <ul className="grid gap-1 text-sm">
        {doc.issues.map((issue) => (
          <li key={issue.id}>
            <code>{issue.id}</code> {issue.title}{" "}
            <span className="text-muted-foreground">({issue.current} current observations)</span>
            {issue.github && (
              <>
                {" "}
                <a href={issue.github} target="_blank" rel="noreferrer">
                  GitHub
                </a>
              </>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}
```

`app/eval/EvalPage.tsx`:

```tsx
import { Link, useParams } from "react-router";
import { LoadState } from "@/components/LoadState";
import { useDoc } from "@/lib/data";
import { formatTime, shortCommit } from "@/lib/format";
import type { EvalDoc } from "@/lib/types";
import { FindingsSection, InputsSection, IssuesSection, RunsSection, SuppressedSection } from "./sections";

export function EvalPage() {
  const { slug = "" } = useParams();
  const loaded = useDoc<EvalDoc>(`/data/evals/${encodeURIComponent(slug)}.json`);
  if (loaded.state !== "ready") {
    return (
      <LoadState
        loaded={loaded}
        notFound={
          <p>
            No eval called {slug} is in the export. <Link to="/">Back to the findings</Link>
          </p>
        }
      />
    );
  }
  const doc = loaded.doc;
  return (
    <div className="grid gap-8">
      <header className="grid gap-1">
        <h1 className="text-xl font-semibold">{doc.eval}</h1>
        <p className="text-sm text-muted-foreground">
          revision {shortCommit(doc.revision.commit)}
          {doc.revision.dirty ? " (dirty)" : ""}
          {doc.revision.package_version ? `, package ${doc.revision.package_version}` : ""}, task
          version {doc.task_version ?? "unknown"}, data from {formatTime(doc.generated_at)}
        </p>
      </header>
      <RunsSection doc={doc} />
      <InputsSection inputs={doc.inputs} />
      <FindingsSection doc={doc} />
      <SuppressedSection doc={doc} />
      <IssuesSection doc={doc} />
    </div>
  );
}
```

The spec's header lists "last run per producer with skipped reasons in red"; it renders as its own "Runs" section directly under the header so the test can address it as a region. The "Back to the findings" link text is shared with `PageNotFound` in `App.tsx`.

In `app/App.tsx`, route `evals/:slug` to `<EvalPage />`.

- [ ] **Step 3: Run the tests**

Run: `npm run typecheck && npx vitest run --project unit && npx biome check .`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(app): eval page with inputs, grouped findings, suppressions and issues" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 7: Playwright against wrangler dev

**Files:**

- Create: `e2e/stub-upstream.mjs`, `e2e/site.spec.ts`
- Modify: `playwright.config.ts`, `e2e/health.spec.ts` (unchanged path; keep), `.gitignore` (nothing new: `test-results/` and `playwright-report/` are already ignored)

**Interfaces:**

- Consumes: the built app, the Worker, `test/fixtures/export/`.
- Produces: `npm run test:e2e` green locally; with `SCREENSHOTS=1`, PNGs under `docs/screenshots/` for the PR.

- [ ] **Step 1: Stub upstream**

`e2e/stub-upstream.mjs`:

```js
// Stands in for raw.githubusercontent.com in the e2e tests: serves
// test/fixtures/export/<path> for any /<owner>/<repo>/<ref>/export/<path>.
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "../test/fixtures/export");
const PORT = Number(process.env.STUB_PORT ?? 8788);

createServer(async (req, res) => {
  if (req.url === "/ready") return res.end("ok");
  const match = /^\/[^/]+\/[^/]+\/[^/]+\/export\/((?:evals\/)?[a-z0-9-]+\.json)$/.exec(req.url ?? "");
  try {
    if (!match) throw new Error("no route");
    const body = await readFile(path.join(ROOT, match[1]));
    res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" }).end(body);
  } catch {
    res.writeHead(404).end("404: Not Found");
  }
}).listen(PORT, "127.0.0.1");
```

- [ ] **Step 2: Playwright config**

`playwright.config.ts` — `webServer` becomes an array; everything else as scaffolded:

```ts
webServer: [
  {
    command: "node e2e/stub-upstream.mjs",
    url: "http://127.0.0.1:8788/ready",
    reuseExistingServer: !process.env.CI,
  },
  {
    // Build first: the Worker serves dist/. The stub replaces GitHub.
    command:
      "npm run build && wrangler dev --port 8787 --var FINDINGS_ORIGIN:http://127.0.0.1:8788 --var GITHUB_TOKEN:e2e-token",
    url: "http://127.0.0.1:8787/health",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
],
```

- [ ] **Step 3: Write the flow**

`e2e/site.spec.ts`:

```ts
import { expect, type Page, test } from "@playwright/test";

const shoot = async (page: Page, name: string) => {
  if (process.env.SCREENSHOTS) await page.screenshot({ path: `docs/screenshots/${name}.png`, fullPage: true });
};

test("from the table to an eval page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/^6 of 6 findings$/)).toBeVisible();
  await shoot(page, "table-dark");

  await page.getByLabel("Severity").selectOption("major");
  await expect(page.getByText(/^1 of 6 findings$/)).toBeVisible();
  await expect(page).toHaveURL(/severity=major/);

  await page.getByRole("link", { name: "inspect_evals/stereoset" }).first().click();
  await expect(page).toHaveURL(/\/evals\/inspect-evals-stereoset$/);
  await expect(page.getByRole("heading", { level: 1, name: "inspect_evals/stereoset" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Suppressed" })).toBeVisible();
  await shoot(page, "eval-dark");

  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await shoot(page, "eval-light");
  await page.goto("/");
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await shoot(page, "table-light");
});

test("a deep link and a reload serve the app", async ({ page }) => {
  await page.goto("/evals/inspect-evals-gsm8k?x=1");
  await expect(page.getByText(/^No findings\. \d+ checks ran, \d+ skipped\.$/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/^No findings\./)).toBeVisible();
  await shoot(page, "eval-empty-dark");
});

test("a data URL opened in the browser serves JSON, not the app", async ({ page }) => {
  const response = await page.goto("/data/index.json");
  expect(response?.headers()["content-type"]).toContain("application/json");
  expect(JSON.parse((await response?.text()) ?? "{}").schema).toBe(1);
});

test("an unknown eval shows a way back", async ({ page }) => {
  await page.goto("/evals/no-such-eval");
  await expect(page.getByRole("link", { name: "Back to the findings" })).toBeVisible();
});
```

- [ ] **Step 4: Run**

```bash
npx playwright install chromium
npm run test:e2e
SCREENSHOTS=1 npm run test:e2e
```

Expected: all PASS; `docs/screenshots/{table-dark,table-light,eval-dark,eval-light,eval-empty-dark}.png` written. Open each and check them by eye: both themes readable, badges legible, nothing overflowing.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "test(e2e): drive the built site through wrangler dev against a stubbed findings repo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

______________________________________________________________________

### Task 8: README, changelog, gate

**Files:**

- Modify: `README.md`, `CHANGELOG.md`

- [ ] **Step 1: README**

Keep the template's sections that still apply. Lead with what the site is, then add:

- **Pages:** `/` findings table, `/evals/<slug>` eval page; data from `export/` in `Generality-Labs/inspect-evals-findings` (written by inspect_audit's `render_current`), via `/data/*`.
- **Local development:** `cp .dev.vars.example .dev.vars` and set `GITHUB_TOKEN`; `npm run build && npm run dev` serves the built site against the real export at `http://127.0.0.1:8787`; `npm run dev:app` runs Vite with hot reload on 5173, proxying `/data` to the wrangler server. `npm run test:e2e` runs Playwright against the fixture export, and `SCREENSHOTS=1 npm run test:e2e` writes `docs/screenshots/`.
- **Access:** a Cloudflare Access application on `audits.generality.org`, policy "Generality Labs Google accounts", created by hand in the Zero Trust dashboard. Removing it is how the site goes public. `/data/*` and the future `/mcp` sit behind the same gate; a script needs an Access service token.
- **Caching:** five-minute Cache API entry per export file; a no-op while Access fronts the Worker (Cloudflare's documented limitation), so pilot traffic goes to GitHub on every request.
- **Secrets and settings:** `GITHUB_TOKEN` (fine-grained PAT, Contents read on `inspect-evals-findings` only, put with `npm run secrets`; **expires: `<date set when created>`**); repo secrets `CLOUDFLARE_API_TOKEN` (Workers Scripts edit, Workers Routes edit, Zone read on `generality.org`) and `CLOUDFLARE_ACCOUNT_ID` (`4dd4a6b03a79f452ebc112658643f02a`); `HEALTH_URL=https://audits.generality.org/health` on the `production` environment, which needs `ACCESS_CLIENT_ID` and `ACCESS_CLIENT_SECRET` (an Access service token allowed by a Service Auth policy) because the smoke test requires a real 200.
- **Schema:** the app reads export schema 1 (`app/lib/types.ts`); a newer export shows an update message.

- [ ] **Step 2: Changelog**

Under `## [Unreleased]` → `### Added`: "The findings table and eval pages, read from the findings export through the Worker's `/data` routes."

- [ ] **Step 3: Full gate**

```bash
npm run typecheck && npm test && npm run check:bundle && git add -A && uvx pre-commit run --all-files
```

Expected: all pass (pre-commit may reformat; re-run until clean).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: README for the table site; changelog" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Whole-branch review**

A fresh reviewer on `main..feat/table-site` with this plan and the spec; minors re-graded by effect; fix what survives, with tests.

______________________________________________________________________

### Task 9: Publish (needs Matt)

Each step here is outward-facing; do it only with Matt's go-ahead.

- [ ] Create `Generality-Labs/audits-site` (private), push `main` (the scaffold commit), run `bash scripts/setup-repo.sh`, grant write to Matt and Tania.
- [ ] Push `feat/table-site` and open a PR into `main` with the template from the README's conventions, the screenshots from `docs/screenshots/` (after only; the site is new), and the "Deviations from the spec" list above.
- [ ] Matt: repo secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ACCESS_CLIENT_ID`, `ACCESS_CLIENT_SECRET`; `production` environment variable `HEALTH_URL`; the Access application on `audits.generality.org`; the `GITHUB_TOKEN` PAT via `npm run secrets`, with its expiry date written into the README.
- [ ] After merge: the deploy workflow runs; check `https://audits.generality.org/` through Access, and that `/data/index.json` returns the real export (this is the first check of raw.githubusercontent.com with a fine-grained PAT as a Bearer token).
