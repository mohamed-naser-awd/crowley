# Changelog

All notable changes to Crowley are documented here. The project follows [Semantic Versioning](https://semver.org/); until 1.0, minor versions may change the template format (`crowley: 1`) or the Python API.

## [0.1.0] - 2026-10-04

First public release.

### Template language
- YAML templates with metadata, `permissions`, `secrets`, `defaults`, `limits`, shared `schemas`, reusable template functions (`local.*`) and multiple named operations.
- Steps: `use`, `if`, `for_each`, `while`, `set`, `emit`, `return`, `break`, `continue`, `fail`, `assert`, `log`, with `when`, `timeout` and `on_error` (`skip`, `default`, `retry` with backoff, `catch`).
- `prev` piping between steps; `${{ }}` expressions with strict semantics, null-safe access, projections and about 70 helpers.
- Static validation of every template (`crowley validate`, `crowley schema`): all diagnostics with file, line, column and hints.

### Runtime
- Processes (`Crowley.init`) with `run`, `stream`, `start`/`result`, `cancel` and `run_sync`; many processes can run concurrently.
- Notifiers on every step, function call, page, exchange, emitted item and the run itself, at global, `NotifierRegistry` and process scope, with skip/replace/retry/abort actions and revalidation.
- Functions as `handler(ctx, **kwargs)` with signature checks; block functions with `ctx.body`; lazy arguments.
- JSON Schema validation of inputs, function arguments and results, emitted items and the output; limits on requests, items, depth, loop iterations, duration, response size and retries; secrets masked in errors and events.

### Adapters, extractors and stdlib
- Shared exchange pipeline: host permissions, SSRF guard, per-hop checks, rate limiting, retries, size caps, per-process sessions.
- `http` adapter (httpx, HTTP/2, cookies, `http.session`, redirects, retries with `Retry-After`).
- `html`, `xml`, `json` and `text` extractors on a shared field engine (fallback selectors, `required`, `default`, nested fields) and `extract.auto`.
- `paginate.*`, `transform.*`, `control.*`.
- `crowley run` with JSON/JSONL output and exit codes per error family.

### Not yet included
- Template composition (`template:<ref>#<op>`) and concurrent `for_each` — planned for 0.2.
- Record/replay, template `tests:`, `crowley test`/`record`/`explain` and traces — planned for 0.3.

[0.1.0]: https://github.com/mohamed-naser-awd/crowley/releases/tag/v0.1.0
