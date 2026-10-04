# Crowley SDK — Product Requirements Document

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Date** | 2026-10-04 |
| **Scope** | Python SDK and CLI only. The template hub is out of scope here, but the SDK is designed so a hub can be added later. |
| **Companion docs** | [SPEC.md](SPEC.md) (template language and runtime semantics), [ARCHITECTURE.md](ARCHITECTURE.md) (onion architecture and package layout) |

---

## 1. Overview

Crowley is a Python SDK that runs **declarative scraping templates** written in YAML. A template covers one site or API and is split into named **operations**, for example `get_page_info` and `get_page_people`. Each operation describes *what* to fetch and *how* to turn responses into structured data, and is run on its own. Operations share logic through **template functions**: named groups of steps declared once in the template. It does this by combining **functions** from a registry with a few control-flow constructs: conditions, loops and variables.

Crowley is **function-based**. The engine has no built-in idea of "pagination", "login" or "API". It only knows how to:

1. resolve a function by name from a registry,
2. validate its arguments,
3. call it,
4. store its result so later steps can use it.

Features like pagination exist only as functions (`paginate.by_page`, `paginate.by_offset`, ...). Developers can register their own functions in exactly the same way.

Every template has an **output schema**, and Crowley **force-validates** at every boundary. Invalid data stops the run with a precise, typed error. Data is never silently corrupted.

Developers keep full control at runtime through **notifiers**. Every step, function call, condition, loop iteration, HTTP request and response emits an event, and that event can be observed or modified.

## 2. Problem

- Scraping code is mostly boilerplate: requests, retries, pagination, selectors, cleanup. It is rewritten for every site and breaks silently when a site changes.
- Existing declarative scrapers are either too rigid (they can't express real logic) or let templates run arbitrary code (so shared templates are unsafe).
- Scraped output is rarely validated. Downstream systems find broken data late.
- Developers who embed a scraper need hooks to inject proxies, auth, logging and fixes without forking the scraper.

## 3. Goals

| # | Goal |
|---|---|
| G1 | Describe everything Crowley can do with a site in one YAML file, with no Python. The file holds several operations, and each operation is a complete job (fetch, paginate, extract, transform, output). |
| G2 | Templates are **data, not code**: safe to download and run from untrusted sources. |
| G3 | **Strict by default**: templates, inputs, function args and results, and final output are all validated, and the run fails fast with exact paths. |
| G4 | **Extensible**: custom functions and plugins use the same contract as the built-ins. |
| G5 | **Total runtime control**: notifiers on every step and every phase, able to change data, skip, replace, retry or abort. |
| G6 | **Hub-ready**: stable template ids and versions, a `requires` declaration, declared host permissions, and template tests. The hub can be added later without changing the template format. |
| G7 | **Clean architecture**: onion layering, so transports (HTTP now, browser later), parsers and sources are swappable adapters. |

## 4. Non-goals (v1)

- The template hub (server, publishing, search, accounts).
- Browser rendering or JavaScript execution. The design leaves room for a `browser.*` plugin namespace and a `BrowserPort` later.
- A GUI template builder, or AI-generated templates.
- Distributed or multi-machine crawling and job scheduling.
- Bypassing CAPTCHAs or anti-bot measures.

## 5. Personas

| Persona | Description | Primary needs |
|---|---|---|
| **Template author** | Writes YAML templates for a site or API. May not be a Python expert. | Clear syntax, good error messages with line and column, ready-made functions, offline tests. |
| **Integrator** | Python developer embedding Crowley in an app or pipeline. | Simple API, typed results, notifiers to inject proxies, auth and logging, predictable failures. |
| **Plugin author** | Python developer adding domain-specific functions (e.g. `mycorp.decode_token`). | A small, documented function contract; namespaces; access to HTTP and HTML through ports. |

## 6. User stories

1. As a template author, I write one template for a site with several operations (`get_page_info`, `get_page_people`), run `crowley run site.yml get_page_people --input company=acme`, and get JSON that matches that operation's schema.
1. As a template author, I put the authenticated fetch and the normalization in template functions once, and call them from every operation.
2. As a template author, `crowley validate` finds a typo in a function name or a reference to a step that doesn't exist **before** any request is made.
3. As a template author, I record real responses once (`crowley record`) and then run `crowley test` offline whenever I change selectors.
4. As an integrator, I call `await crowley.run("t.yml", inputs={...})` and get a validated result, or a typed exception that tells me exactly which field or step failed.
5. As an integrator, I attach a `request.before` notifier that adds a proxy and auth header to every request, without editing the template.
5. As an integrator running several operations at once, I give each process its own notifiers (`process.add_notifier`) and share common bundles (`process.add_notifier_registry`), while global notifiers still apply to all of them.
6. As an integrator, I attach a `step.after` notifier to a specific step id to fix data before it continues through the template.
7. As an integrator, I stream items one at a time (`async for item in crowley.stream(...)`) for large crawls.
8. As a plugin author, I register `mycorp.*` functions with input and output schemas. Templates declare `requires: [mycorp.x@^1]`, and Crowley refuses to run them if the plugin is missing.
9. As an integrator running third-party templates, I trust that a template can only contact the hosts it declares and can't reach my private network.

## 7. Functional requirements

### 7.1 Templates
- **FR-1** Templates are YAML documents that follow spec version `crowley: 1`.
  - **Template-level** keys, shared by all operations: metadata (`id`, `version`, `name`, `description`), `secrets`, `permissions`, `defaults`, `limits`, `requires`, `schemas` and `functions`.
  - **`operations`**: a map with at least one operation. Each operation has `description`, `inputs`, `limits`, `steps`, `output` and `tests`.
- **FR-1a** **Operations from day one.** Every template exposes named operations, and every run executes exactly one of them (`cw.run(tpl, "get_page_people", inputs=...)`). Operations are isolated at runtime. They share only template-level configuration and template functions.
- **FR-1b** Shared JSON Schemas (`schemas:`) can be referenced from any operation or function with `$ref: "#/schemas/<name>"`.
- **FR-2** Templates can be loaded from a file path, a string, a dict, or any registered `TemplateSource`.
- **FR-3** Unknown top-level keys are rejected, except extension keys prefixed with `x-`.
- **FR-4** Parse and validation errors report a template path (e.g. `steps[0].do[1].with.selector`) and a source location (file, line, column).

### 7.2 Steps and control flow
- **FR-5** Step kinds: `use` (function call), `if`/`then`/`else`, `for_each`, `while`, `set`, `emit`, `return`, `break`, `continue`, `fail`, `assert`, `log`.
- **FR-6** Every step supports `id`, `name`, `when` (guard), `on_error` and `timeout`.
- **FR-7** `for_each` supports `concurrency` (default 1), with results kept in input order.
- **FR-8** `while` requires `max_iterations`, so a loop can't run forever.
- **FR-9** Block functions (e.g. pagination, sessions, retry) receive a `do:` body and control when and how often it runs.
- **FR-9a** **Piping:** every step's output is passed to the next step as `prev`. It is available in expressions as `prev` and passed to every function as the `prev` keyword argument. Steps that produce no output pass `prev` through unchanged.
- **FR-9b** If the template has no `emit` and no `output.value`, the output is the main block's final `prev`.

### 7.3 Expressions
- **FR-10** Values can contain `${{ expr }}` expressions in a sandboxed language: literals, member and index access, projection, arithmetic, comparison, boolean logic, conditional expressions, helper calls, and lambdas as helper arguments.
- **FR-11** Expressions can't access Python objects, the filesystem, environment variables or the network.
- **FR-12** Integrators and plugins can register more pure helpers.

### 7.4 Functions and registry
- **FR-13** Every function declares `name` (namespaced), `version`, `kind` (`plain` or `block`), `input` schema, an optional `prev` schema, and an `output` schema.
- **FR-13a** Every handler is called as `handler(ctx, **kwargs)`, where `kwargs` holds the validated args plus `prev`.
  - A handler with `**kwargs` receives everything. Otherwise it receives only the parameters it names, so any argument, `prev` included, is optional for the handler.
  - The signature is checked against the schema when the function is registered.
- **FR-13b** An input property can be marked to default from `prev` when it is omitted (e.g. `html.extract.html`, `transform.*.items`).
- **FR-14** Functions can be registered with a decorator, a `register()` call, or a plugin object. Entry-point discovery is opt-in.
- **FR-15** Templates can define **template functions**: named groups of steps under `functions:`, each with an input schema and an output schema. Any operation calls them with `use: local.<name>`.
  - They are validated and observable like any other function.
  - They can call each other, but not recursively.
  - They can't read operation `inputs` or `emit`, so they behave the same from every operation.
- **FR-16** Templates can call an operation of another template with `use: template:<ref>#<operation>`.
- **FR-17** The v1 standard library includes `http.*`, `html.*`, `paginate.*`, `transform.*` and `control.*` (see SPEC §12).

### 7.5 HTTP
- **FR-18** Requests support any method, URL, query, headers, cookies, a body (`json` | `form` | `raw` | `multipart`), timeout, redirects, proxy, TLS verification, auth, expected status codes, response type, and retries with backoff.
- **FR-19** Template-level `defaults.http` merge into every request. Precedence: SDK config < template defaults < step args < notifiers.
- **FR-20** Every run has an implicit cookie session. `http.session` creates an isolated nested session.
- **FR-21** Requests are allowed only to hosts declared in `permissions.hosts`. Private and loopback networks are blocked unless the integrator enables them. Redirects are checked at every hop.
- **FR-22** The HTTP transport is a port. httpx is the v1 adapter. Record and replay adapters support testing.

### 7.6 Validation (force validate)
- **FR-23** Validation happens in five stages: (1) parse, (2) meta-schema, (3) static semantic checks, (4) runtime validation of inputs, function args, function results and emitted items, (5) final output.
- **FR-24** Static checks run before any network I/O. They cover unknown functions, missing requirements, invalid literal args, unresolved references, misplaced `break`/`continue`/`return`, expression syntax, undeclared hosts for literal URLs, and more (SPEC §15).
- **FR-25** Validation errors are **never** caught by `on_error`. Strict mode can't be turned off for output validation.

### 7.7 Notifiers
- **FR-25a** **Notifiers exist from day one.** The event bus and the step pipeline are part of the first runnable version of the runtime (M2). No step kind may ship without its before/after events.
- **FR-25b** Every step of every kind, at every nesting depth, fires `step.before` and then `step.after` (or `step.error`/`step.skipped`). In `step.before`, notifiers can change the step's incoming `prev` and evaluated args. In `step.after`, they can change its result, which is the next step's `prev`.
- **FR-26** Events are emitted for the run, template, inputs, steps, functions, conditions, loops, pages, HTTP requests and responses, variables, emitted items, output and logs (SPEC §17).
- **FR-27** Interceptors can change event data and return actions: `skip`, `replace`, `retry`, `abort`. Observers are read-only.
- **FR-27a** **Notifier scopes.** Notifiers can be registered at three scopes:
  - **global**, on the `Crowley` instance; these always apply,
  - **reusable `NotifierRegistry` bundles**,
  - **process**, for one operation run.
  `cw.init(template, operation, inputs=...)` creates a `Process`, and `process.add_notifier(event, fn, **filters)` / `process.add_notifier_registry(registry)` attach notifiers that affect only that process. Many processes can run concurrently on one instance, each with its own notifiers.
- **FR-28** Notifiers can be filtered by event name (with glob), step id, function name and template id, and are ordered by priority.
- **FR-29** Changes made by notifiers are validated again. The final output check, permission checks and limits can never be bypassed.
- **FR-30** Templates can't register or affect notifiers.

### 7.8 Errors
- **FR-31** All errors are subclasses of `CrowleyError` and carry a code (`E###`), message, template path, source location, redacted offending value, and hint.
- **FR-32** `on_error` per step: `fail` (default), `skip`, `default: <value>`, or `retry` with backoff. It applies only to catchable error classes (SPEC §16).

### 7.9 Output
- **FR-33** Output is one of:
  - the list of items produced by `emit` steps,
  - an explicit `output.value` expression,
  - the final piped `prev` (FR-9b).
- **FR-34** `output.schema` (JSON Schema 2020-12) is required. Emitted items are validated one by one against `output.schema.items`.
- **FR-35** The SDK offers `run()` (collect everything), `stream()` (async iterator of emitted items) and `run_sync()`.

### 7.10 Limits and safety
- **FR-36** Limits: `max_requests`, `max_duration`, `max_items`, `max_depth`, `max_loop_iterations`, `max_concurrency`, `max_response_bytes`, and a per-host rate limit. SDK-level limits cap template limits (the stricter value wins).
- **FR-37** Secrets come only from the integrator and are redacted in events, traces, logs and errors.

### 7.11 Testing and tooling
- **FR-38** Each operation can include a `tests:` block with inputs, fixtures (HAR files), and expectations (snapshot, item counts, assertions, expected error code).
- **FR-39** CLI commands: `run <template> <operation>`, `operations`, `validate`, `explain`, `test`, `record`, `functions list|show`, `schema`, `init`.
- **FR-40** Each run can write a JSON trace of all events (secrets redacted).
- **FR-41** The meta-schema is published as a JSON Schema so editors can autocomplete and validate YAML.

## 8. Non-functional requirements

| # | Category | Requirement |
|---|---|---|
| NFR-1 | Platform | Python ≥ 3.11, async-first (asyncio), with a sync wrapper. Linux, macOS and Windows. |
| NFR-2 | Architecture | Onion architecture, with layer rules enforced in CI by `import-linter`. The domain layer uses only the standard library. |
| NFR-3 | Typing | `mypy --strict` passes. The public API is fully typed. |
| NFR-4 | Security | No code execution from templates, sandboxed expressions, enforced host allow-list, SSRF protection, regex timeouts, response size cap, secret redaction. |
| NFR-5 | Performance | Engine overhead per non-I/O step ≤ 1 ms (p95). Expression evaluation ≤ 50 µs (p95) for typical expressions. Templates are compiled once and can be reused across runs. |
| NFR-6 | Reliability | Same inputs and same fixtures always give the same output (except `uuid()` and `now()`, which can be stubbed). |
| NFR-7 | DX | Every error has a code, path, location and hint. `crowley validate` runs in < 200 ms for a typical template. |
| NFR-8 | Quality | ≥ 90% line coverage in domain and application. Every stdlib function has unit tests plus replay-based integration tests. |
| NFR-9 | Compatibility | The `crowley: 1` spec is stable inside major version 1 of the SDK. Breaking spec changes require `crowley: 2` and a migration path. |

## 9. Scope

### In v1 (0.1 → 1.0)
- Template spec v1 with operations, template functions and shared schemas, plus the compiler, static validator and published meta-schema
- Expression language with the core helper set
- Runtime: all step kinds, scopes, concurrency, limits, permissions, `on_error`, `emit` and streaming
- Notifier system (interceptors and observers) with the full event catalog
- Stdlib: `http`, `html`, `paginate`, `transform`, `control`
- Template composition (`template:<ref>#<op>`), fallback selectors
- Record and replay (HAR), the `tests:` block, trace output
- CLI and Python SDK facade

### Later
- `browser.*` plugin (Playwright adapter for a `BrowserPort`)
- Hub client (`HubSource` adapter for `TemplateSource`), signatures, lockfile
- Persistent `state:` between runs (incremental crawls), checkpoint and resume
- HTTP response cache for development, output sinks (CSV, SQL, webhook)
- Codegen of typed models from `output.schema`
- Language server for editors

## 10. Success metrics

- A new user writes and runs their first working template in **< 15 minutes** using the docs.
- **≥ 80%** of template mistakes in the test corpus are caught by `crowley validate` (static), not at runtime.
- The example corpus (10+ templates for real sites) passes `crowley test` offline in CI.
- **Zero** cases in the test suite where invalid output reaches the caller without an error.

## 11. Milestones

| Milestone | Content |
|---|---|
| **M0 Skeleton** | Repository, packaging (uv/hatch), layer packages, import-linter contracts, CI (ruff, mypy, pytest). |
| **M1 Language** | Domain model (template, **operations**, template functions, shared schemas, steps AST, values, errors, events). Templates are multi-operation from the first commit; there is no single-operation format to migrate from. Also: expression lexer, parser and evaluator, YAML loader with positions, meta-schema, compiler, static validator. `crowley validate` and `crowley schema`. |
| **M2 Runtime** | `Process` (one operation run each, safe to run several concurrently) with global, registry and process notifier scopes. Event bus and `StepPipeline` built **first**, then the executor on top of them, so every step kind has before/after notifiers from its first commit. Then: `prev` piping, `**kwargs` handler binding, template functions (`local.*`), scopes, control flow, limits, runtime validation, `emit` and streaming. Exit criterion: the notifier conformance test passes. |
| **M3 Stdlib & HTTP** | httpx transport, permissions and SSRF guard, rate limiting, `http.*`, `html.*` (lxml), `paginate.*`, `transform.*`, `control.*`. `crowley run`. |
| **M4 Composition** | `template:<ref>#<op>` calls, `on_error`, `for_each` concurrency, fallback selectors, plugins and `requires`. |
| **M5 Testing & release** | HAR record and replay, `tests:` block, `crowley test`/`record`/`explain`, traces, docs, example corpus. Release 0.1.0. |

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The expression language grows into a general programming language | Fixed grammar in the spec. New power comes from helpers and functions, not syntax. Lambdas are allowed only as helper arguments. |
| Strict validation makes authoring feel heavy | Clear errors with hints, `default()`/`?.` for optional data, per-step `on_error` for expected failures, fast `validate`. |
| Selectors break when sites change | Fallback selector lists, a `selector.fallback` event, and offline tests with recorded fixtures. |
| Malicious templates (SSRF, data exfiltration, ReDoS, resource exhaustion) | Host allow-list, private-network block, redirect checks, regex timeouts, limits, no code execution. |
| Notifier changes produce invalid data | Revalidation after changes, and final output validation that can't be bypassed. |
| The async-only core is awkward for sync users | `run_sync()` and a sync CLI. Sync user functions run in a thread pool. |

## 13. Decisions log

| Decision | Choice |
|---|---|
| Language | Python ≥ 3.11 |
| Transport | HTTP only in v1, behind a `HttpTransport` port. Browser comes later as a plugin. |
| Architecture | Onion: domain → application → (stdlib, infrastructure) → interface |
| Expression syntax | `${{ ... }}` with a custom sandboxed, Python-flavoured grammar |
| Output model | `emit` (streamable list), **or** `output.value`, **or** the final `prev` (pipe mode) |
| Data flow | Each step's output is piped to the next step as `prev` |
| Template shape | One template per site or API, split into named operations. Each run executes one operation. Reuse comes from template functions and shared schemas. |
| Function calling convention | `handler(ctx, **kwargs)`, bound to whatever the handler's signature accepts |
| Notifiers | Core runtime feature from M2's first commit, not an add-on. Three scopes: global (always applied), `NotifierRegistry` bundles, per-`Process`. |
| Schema dialect | JSON Schema 2020-12 |
| HTML engine | lxml + cssselect (CSS and XPath) |
| YAML engine | ruamel.yaml (source positions, duplicate-key detection) |
| Fixture format | HAR 1.2 |
| License | MIT. Fully open source; users can do whatever they want with the SDK. |
| Tooling | uv (env + `uv.lock`), ruff, mypy strict, pytest, import-linter. CI on GitHub Actions: Linux, macOS and Windows × Python 3.11–3.14. |

## 14. Open questions

1. Is the PyPI package name `crowley` available? Otherwise, which fallback name?
2. Should robots.txt be checked by default (configurable), or opt-in?
3. Should persistent `state:` (incremental crawls) move into v1?
