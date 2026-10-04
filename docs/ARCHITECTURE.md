# Crowley SDK — Architecture

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Date** | 2026-10-04 |
| **Related** | [PRD.md](PRD.md), [SPEC.md](SPEC.md) |

Crowley uses an **onion architecture**. Dependencies point inward only. The core (domain and application) knows nothing about YAML libraries, httpx, lxml or the CLI. It defines **ports** (Python `Protocol`s), and outer layers provide **adapters** for them. This is what lets us add a browser transport, a hub template source, or a different HTML engine later without touching the core.

---

## 1. Layers

```
┌──────────────────────────────────────────────────────────────────────┐
│ 4. Interface  (crowley.interface)                                    │
│    SDK facade `Crowley`, CLI, pytest helpers — the composition root  │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ 3. Adapters                                                      │ │
│ │    crowley.infrastructure — implements ports (httpx, ruamel,     │ │
│ │                             jsonschema, lxml, files, HAR, env)   │ │
│ │    crowley.stdlib         — built-in functions (http, html,      │ │
│ │                             paginate, transform, control)        │ │
│ │ ┌──────────────────────────────────────────────────────────────┐ │ │
│ │ │ 2. Application  (crowley.application)                        │ │ │
│ │ │    use cases, compiler, static validator, runtime/executor,  │ │ │
│ │ │    function registry, event bus, ports                       │ │ │
│ │ │ ┌──────────────────────────────────────────────────────────┐ │ │ │
│ │ │ │ 1. Domain  (crowley.domain)                              │ │ │ │
│ │ │ │    template model, step AST, values, expression language,│ │ │ │
│ │ │ │    function contract, events, errors, HTTP value objects │ │ │ │
│ │ │ └──────────────────────────────────────────────────────────┘ │ │ │
│ │ └──────────────────────────────────────────────────────────────┘ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.1 Dependency rules (enforced by `import-linter` in CI)

| Layer | May import | Must not import |
|---|---|---|
| `domain` | Python standard library only | anything else in `crowley`, any third-party package |
| `application` | `domain`, Python standard library | `stdlib`, `infrastructure`, `interface`, third-party packages |
| `stdlib` | `domain`, `application` (public function API and ports) | `infrastructure`, `interface`, third-party packages |
| `infrastructure` | `domain`, `application` (ports), third-party packages | `stdlib`, `interface` |
| `interface` | everything | — |

`stdlib` and `infrastructure` are siblings in the same ring and MUST NOT depend on each other. Built-in functions reach HTML parsing and HTTP **only** through ports on `FunctionContext`. This keeps built-ins identical to third-party plugins, and lets record/replay and notifiers work for every function.

```toml
# pyproject.toml
[tool.importlinter]
root_package = "crowley"

[[tool.importlinter.contracts]]
name = "Onion layers"
type = "layers"
layers = [
  "crowley.interface",
  "crowley.stdlib | crowley.infrastructure",
  "crowley.application",
  "crowley.domain",
]

[[tool.importlinter.contracts]]
name = "Core has no third-party deps"
type = "forbidden"
source_modules = ["crowley.domain", "crowley.application", "crowley.stdlib"]
forbidden_modules = ["httpx", "ruamel", "jsonschema", "lxml", "cssselect", "regex", "click"]
```

---

## 2. Package layout

```
crowley/
├── pyproject.toml
├── docs/                         PRD.md, SPEC.md, ARCHITECTURE.md
├── schema/                       crowley-1.schema.json  (generated from domain, published)
├── examples/                     example templates + fixtures + snapshots
├── src/crowley/
│   ├── __init__.py               public API re-exports (Crowley, function, Notifier, errors, ...)
│   │
│   ├── domain/                   ── LAYER 1 ──────────────────────────────────────────
│   │   ├── template/             Template, Operation, TemplateFunction, Metadata, InputSpec,
│   │   │                         SecretSpec, Permissions, HostPattern, Defaults, Limits,
│   │   │                         SchemaTable (shared `schemas:`), OutputSpec, TestCase, OperationRef
│   │   ├── steps/                Step AST (frozen dataclasses): UseStep, IfStep, ForEachStep,
│   │   │                         WhileStep, SetStep, EmitStep, ReturnStep, BreakStep,
│   │   │                         ContinueStep, FailStep, AssertStep, LogStep, Block, ErrorPolicy
│   │   ├── expressions/          lexer, parser (Pratt), AST, evaluator, interpolation,
│   │   │                         helper registry + built-in pure helpers, budgets
│   │   ├── values/               value model (JSON types, Bytes, Handle), type checks, conversions
│   │   ├── functions/            FunctionSpec, FunctionKind, Requirement (+ SemVer range), Outcome
│   │   ├── events/               Event dataclasses per catalog entry, actions (Skip/Replace/Retry/Abort)
│   │   ├── http/                 HttpRequest, HttpResponse, Body variants, RetryPolicy,
│   │   │                         header merging, URL/host matching
│   │   ├── errors/               CrowleyError hierarchy, codes, SourceLocation, redaction
│   │   ├── services/             domain-service protocols: Clock, RandomSource, RegexEngine
│   │   └── common/               Duration, ByteSize, SemVer, TemplatePath, identifiers
│   │
│   ├── application/              ── LAYER 2 ──────────────────────────────────────────
│   │   ├── ports/                TemplateSource, TemplateParser, SchemaValidator, HttpTransport,
│   │   │                         HostResolver, HtmlParser, SecretsProvider, TraceSink, RateLimiter
│   │   ├── registry/             FunctionRegistry, HelperRegistry, Plugin protocol, requires resolution
│   │   ├── compiler/             RawDocument → domain AST, meta-schema check, static validator (E3xx)
│   │   ├── runtime/              Process, Executor, StepPipeline, step handlers, Scope/Frame (carries prev),
│   │   │                         ControlSignal, FunctionInvoker, HandlerBinding, FunctionContext, Body, LimitsGuard,
│   │   │                         PermissionGuard, ConcurrencyManager, OutputCollector, RunState
│   │   ├── http/                 HttpService: defaults merge, events, permission + SSRF check,
│   │   │                         rate limit, retry, sessions — sits in front of HttpTransport
│   │   ├── events/               EventBus, Notifier, NotifierSet, NotifierRegistry, NotifierHandle,
│   │   │                         filters, interception pipeline, revalidation
│   │   ├── testing/              TemplateTestRunner (expectations, snapshots)
│   │   └── use_cases/            LoadTemplate, ValidateTemplate, ExplainOperation, RunOperation,
│   │                             StreamOperation, TestTemplate, RecordOperation
│   │
│   ├── stdlib/                   ── LAYER 3 (functions) ──────────────────────────────
│   │   ├── __init__.py           StdlibPlugin (registers all namespaces)
│   │   ├── http.py               http.request/get/post/.../session
│   │   ├── html.py               html.parse/select/select_all/extract (field-spec engine)
│   │   ├── paginate.py           paginate.by_page/by_offset/by_cursor/by_next_link
│   │   ├── transform.py          transform.map/filter/dedupe/sort/group_by/flatten
│   │   └── control.py            control.retry/sleep/parallel
│   │
│   ├── infrastructure/           ── LAYER 3 (adapters) ───────────────────────────────
│   │   ├── yaml/                 RuamelTemplateParser (positions, dup keys, no custom tags)
│   │   ├── schema/               JsonSchemaValidator (jsonschema, Draft 2020-12, format checks)
│   │   ├── http/                 HttpxTransport, RecordingTransport, ReplayTransport (HAR 1.2),
│   │   │                         SystemHostResolver, TokenBucketRateLimiter
│   │   ├── html/                 LxmlHtmlParser (CSS via cssselect, XPath)
│   │   ├── regex/                RegexLibEngine (`regex` with timeouts)
│   │   ├── sources/              DirectorySource, FileSource, InMemorySource   (later: HubSource)
│   │   ├── secrets/              DictSecrets, EnvSecrets
│   │   ├── trace/                JsonTraceSink, NullTraceSink
│   │   └── system/               SystemClock, SystemRandom, FrozenClock, SeededRandom
│   │
│   └── interface/                ── LAYER 4 ──────────────────────────────────────────
│       ├── sdk.py                Crowley facade + container (wires adapters into use cases)
│       ├── config.py             Limits, HttpConfig, public config dataclasses
│       ├── cli/                  click app: run, validate, explain, test, record, functions, schema, init
│       └── pytest_plugin.py      fixtures: crowley, replay(har), run_template(...)
│
└── tests/
    ├── domain/                   pure unit tests (+ hypothesis for expression parser)
    ├── application/              runtime tests with in-memory fake ports
    ├── stdlib/                   function tests with fake HttpTransport/HtmlParser
    ├── infrastructure/           adapter contract tests (respx for httpx, real lxml)
    ├── interface/                CLI tests (click runner), SDK facade tests
    └── e2e/                      examples/ templates via ReplayTransport
```

---

## 3. Domain layer

The domain layer is pure, synchronous, deterministic and has no I/O.

- **Immutable models.** Everything uses `@dataclass(frozen=True, slots=True)`. A compiled `Template` is immutable and safe to share across concurrent runs.
- **Step AST.**
  - Each step kind is its own class. Every step carries its `TemplatePath` and `SourceLocation`.
  - Expressions are stored **compiled** (`Expression` AST plus original source), never as strings.
  - A scalar is either a `Literal` or a `Compiled` value. Mixed strings become an `Interpolation` node.
- **Expression engine.**
  - Hand-written lexer and Pratt parser, and a tree-walking evaluator with a step budget.
  - Helpers are pure callables registered in a `HelperTable`.
  - Regex helpers depend on the `RegexEngine` protocol, and `now()`/`uuid()` on `Clock`/`RandomSource`. These are domain-service protocols, implemented outside the domain.
- **Errors** are domain objects. Every layer raises the same hierarchy, so callers see consistent errors whichever layer failed.
- **Function contract.** `FunctionSpec` and `Outcome` live here. The *invocation* machinery lives in application.
- **Events** are plain dataclasses with mutable payload fields. Actions are value objects (`Skip`, `Replace`, `Retry`, `Abort`).

## 4. Application layer

The application layer holds orchestration and policy. It is async (asyncio) and talks to the outside world only through ports.

### 4.1 Ports

```python
class TemplateSource(Protocol):
    def can_resolve(self, ref: str) -> bool: ...
    async def resolve(self, ref: str, *, relative_to: Origin | None) -> TemplateText: ...

class TemplateParser(Protocol):
    def parse(self, text: str, origin: Origin) -> RawDocument: ...      # dict + position map

class SchemaValidator(Protocol):
    def check_schema(self, schema: Mapping[str, Any]) -> list[SchemaViolation]: ...
    def compile(self, schema: Mapping[str, Any]) -> CompiledSchema: ...
    # CompiledSchema.validate(instance) -> list[SchemaViolation]   (path, message, keyword)

class HttpTransport(Protocol):
    async def open_session(self, config: SessionConfig) -> TransportSession: ...
    async def send(self, session: TransportSession, request: PreparedRequest) -> RawResponse: ...
    async def close_session(self, session: TransportSession) -> None: ...

class HostResolver(Protocol):
    async def resolve(self, host: str) -> list[IPAddress]: ...

class RateLimiter(Protocol):
    async def acquire(self, host: str) -> None: ...

class HtmlParser(Protocol):
    def parse(self, html: str, base_url: str | None) -> HtmlNode: ...
    def css(self, node: HtmlNode, selector: str) -> list[HtmlNode]: ...
    def xpath(self, node: HtmlNode, expr: str) -> list[HtmlNode | str]: ...
    def read(self, node: HtmlNode, attr: str) -> str | None: ...       # text/raw_text/html/inner_html/<attr>

class SecretsProvider(Protocol):
    def get(self, name: str) -> str | None: ...

class TraceSink(Protocol):
    def write(self, event: SerializedEvent) -> None: ...
```

### 4.2 Use cases

| Use case | Steps |
|---|---|
| `LoadTemplate` | source.resolve → parser.parse → `template.loaded` event (patchable) → meta-schema → `Compiler.compile` → `StaticValidator.check` → `CompiledTemplate` (cached by content hash) |
| `ValidateTemplate` | Same as load, but collects **all** diagnostics into a `ValidationReport` instead of raising |
| `ExplainOperation` | Renders one operation's compiled AST as a plan (functions, loops, hosts, limits). Template-function calls are expanded inline. |
| `RunOperation` | Selects the operation (`E904` if missing or unknown) → builds `RunState` (template-level config + that operation's limits) → resolves and validates the operation's inputs and the secrets → `Executor.run(operation.steps)` → validates against the operation's output schema → `RunResult` |
| `StreamOperation` | `RunOperation` with an async-queue `OutputCollector` that yields emitted items |
| `TestTemplate` | For each operation, for each `tests[]`: ReplayTransport + FrozenClock + SeededRandom → run → check expectations |
| `RecordOperation` | RecordingTransport around the live transport → run → write a scrubbed HAR |

### 4.3 Runtime

- **Executor.** An AST walker with one handler per step kind (`dict[type[Step], StepHandler]`).
  - Control flow (`return`, `break`, `continue`) travels as typed `ControlSignal` **return values**, not exceptions. This keeps it cheap and explicit.
  - Errors travel as `CrowleyError` exceptions.
- **Scope stack.** `Frame`s hold `steps`, `vars` and bindings. Frames are created per block, per loop iteration and per body invocation. Concurrent iterations get forked frames.
- **Guards.** These are injected into every run and checked at choke points:
  - `LimitsGuard` (requests, items, duration, depth, iterations),
  - `PermissionGuard` (hosts and SSRF),
  - the cancellation token.

**Step pipeline.** Every step kind goes through this one code path, and the executor cannot run a step any other way. Every step handler is registered *only* through `StepPipeline`, so there is no back door around the events:

```
prev_in ─► when? ──false──► step.before → step.skipped ─► prev_out = prev_in
              │
              true
              ▼
           step.before        (notifiers may change prev / kwargs, skip, replace, abort)
              ▼
           handler(step, frame, prev_in)
              ▼
           step.after         (notifiers may change result, replace, abort)
              ▼
           prev_out = result (or pass-through per SPEC §8.4)

           error ─► step.error (retry / replace / abort) ─► on_error policy
```

`Frame` carries the current `prev`. The block runner threads it from step to step: `prev = await pipeline.run(step, frame, prev)`.

**Function invocation pipeline** (`FunctionInvoker`, inside the `use` step handler):

```
evaluate with: (non-lazy args) → apply schema defaults → fill x-crowley-from-prev args
  → kwargs = {**args, "prev": prev}
  → emit function.before        (notifiers may change kwargs / skip / replace)
  → validate kwargs             (E403)  ← input schema (args) + prev schema (if declared)
  → bind kwargs to the handler signature (precomputed at registration: **kwargs → all, else named only)
  → handler(ctx, **bound)       (ctx.body set for block functions)
  → validate result             (E407)  ← output schema
  → emit function.after         (notifiers may change / replace / retry)
  → revalidate if changed
  → on error: emit function.error → step on_error policy
```

`HandlerBinding` is computed once, in `FunctionRegistry.register`, using `inspect.signature`. It records whether the handler accepts `**kwargs`, which parameter names it takes, and which parameters are required. An incompatible signature fails registration with `E903`.

**HTTP pipeline** (`HttpService`, used through `ctx.http`):

```
merge: HttpConfig < defaults.http < with: → build HttpRequest
  → emit request.before        (mutate / replace with synthetic response / abort)
  → PermissionGuard: host allow-list + resolve DNS + block private ranges (E604)
  → LimitsGuard: max_requests
  → RateLimiter.acquire(host)
  → transport.send             (redirects followed here, each hop permission-checked)
  → enforce max_response_bytes (E606), decode per response_type
  → emit response.after        (mutate / replace / retry)
  → expect_status check (E601) → retry policy (emit request.retry) → result
```

### 4.4 Event bus, notifier scopes and processes

- `NotifierSet` is an ordered, versioned collection of notifiers and nested `NotifierRegistry` references. The `Crowley` instance (global scope), every `NotifierRegistry` and every `Process` each own one.
- `Process` (in `application/runtime`) owns:
  - one `RunState`,
  - one `EventBus`,
  - its `NotifierSet`,
  - a cancellation token.

  Its bus dispatches over **global set → process registries → process set**, merged by priority (SPEC §17.3).
- **Index cache.** The bus caches a per-event-name index of matching notifiers. The cache key is the combined versions of all reachable sets, so adding or removing a notifier anywhere (even inside a shared registry) invalidates it without any locking. Everything runs on the event loop.
- The shared parts (compiled templates, the function registry, the HTTP connection pool, the global `NotifierSet`) are read-mostly and safe for concurrent processes. Per-run state lives only in the `Process`.


- `EventBus.intercept(event)` runs the matching interceptors in priority order and returns the first `Action`, or `None`.
- `EventBus.notify(event)` sends a frozen copy to observers and to the `TraceSink`.
- Notifier filters are pre-indexed by event name, so events with no notifiers cost almost nothing.
- `expression.evaluated` is compiled out unless it is enabled.
- **Revalidation.** The bus marks an event as `dirty` when a mutable field is assigned (through tracked proxies for dicts and lists). The emitting pipeline then revalidates, according to the SPEC §17.1 table.

---

## 5. Adapters

| Port | v1 adapter | Library | Notes |
|---|---|---|---|
| TemplateParser | `RuamelTemplateParser` | ruamel.yaml | Position map for every node; rejects duplicate keys and custom tags |
| SchemaValidator | `JsonSchemaValidator` | jsonschema | Draft 2020-12 with format checking (`uri`, `date-time`, `email`, ...) |
| HttpTransport | `HttpxTransport` | httpx | HTTP/1.1 + HTTP/2. Redirects handled manually so every hop is checked |
| HttpTransport | `RecordingTransport`, `ReplayTransport` | — | HAR 1.2. Decorator/standalone. Used by `record`/`test` |
| HostResolver | `SystemHostResolver` | asyncio `getaddrinfo` | The resolved IP is pinned for the connection to prevent DNS rebinding |
| RateLimiter | `TokenBucketRateLimiter` | — | Per host |
| HtmlParser | `LxmlHtmlParser` | lxml, cssselect | |
| RegexEngine | `RegexLibEngine` | regex | Per-call timeout |
| TemplateSource | `DirectorySource`, `FileSource`, `InMemorySource` | — | `HubSource` later |
| SecretsProvider | `DictSecrets`, `EnvSecrets` | — | |
| TraceSink | `JsonTraceSink`, `NullTraceSink` | — | |
| Clock / RandomSource | `SystemClock`, `SystemRandom`, `FrozenClock`, `SeededRandom` | — | |

The **stdlib** registers itself through `StdlibPlugin`, exactly like a third-party plugin. The only difference is that it may use reserved namespaces.

## 6. Interface layer and composition root

`interface/sdk.py` is the **only** place where concrete adapters are created and wired:

```python
class Crowley:
    def __init__(self, *, sources=None, functions=(), plugins=(), helpers=None,
                 secrets=None, limits=None, http=None, load_entry_points=False,
                 transport: HttpTransport | None = None,          # override for tests/custom stacks
                 html_parser: HtmlParser | None = None, ...):
        self._container = Container.build(...)                    # ports → adapters
        self._registry = FunctionRegistry.with_plugins([StdlibPlugin(), *plugins], functions)
        self._bus = EventBus()
        ...
```

Advanced users can replace any adapter through constructor arguments. Every adapter is just a `Protocol` implementation. The CLI is a thin client of the same facade.

---

## 7. Extension points

| Need | How |
|---|---|
| New functions | `@function` + `register()`, or a `Plugin` with its own namespace |
| New expression helpers | `Crowley(helpers={...})` (pure functions only) |
| Different HTTP stack | Implement `HttpTransport` (e.g. curl-cffi) and pass `transport=` |
| **Browser rendering (later)** | New port `BrowserPort` in `application/ports`, a `PlaywrightBrowser` adapter in `infrastructure/browser`, and a `browser.*` plugin in `stdlib`-style. It reuses `PermissionGuard`, `LimitsGuard` and the `request.*`/`response.*` events. No core changes are needed beyond adding the port to `FunctionContext`. |
| **Hub (later)** | `HubSource` implementing `TemplateSource` (resolves `owner/name@range`, checks hashes and signatures, caches) |
| Other template formats | Implement `TemplateParser` (e.g. JSON or TOML) |
| Output sinks (later) | `OutputSink` port consuming the `OutputCollector` stream |

---

## 8. Concurrency model

- Single event loop per run. `for_each`, `control.parallel` and paginate prefetch (if added) use `asyncio.TaskGroup`, bounded by semaphores (local `concurrency` and global `limits.max_concurrency`).
- Sync user functions run in `asyncio.to_thread`. CPU-heavy parsing (lxml) runs in place by default. `HtmlParser` MAY offload large documents (> 1 MB) to a thread.
- `run_sync()` uses `asyncio.run`. If an event loop is already running, it raises `E902` and tells the caller to use `await run()`.
- Cancellation: `Process.cancel()` cancels that process's root task only. Other processes are unaffected. Every guard and port respects `CancelledError`, which surfaces as `E803`.

## 9. Testing strategy

| Layer | Approach |
|---|---|
| Domain | Pure unit tests. Property-based tests (hypothesis) for the lexer, parser and evaluator round-trip, and to check that no input crashes the parser with anything other than `E321`. |
| Application | Runtime tests with in-memory fakes (`FakeTransport`, `FakeHtmlParser`, `InMemorySource`). Static-validator tests: one fixture template per error code. **Notifier conformance test:** a template that uses every step kind at several nesting depths must produce exactly one `step.before` and one `step.after`/`step.error`/`step.skipped` per executed step, and changes to `prev`, `kwargs` and `result` must reach the next step. **Piping test:** checks the `prev` rules for every step kind (SPEC §8.4). **Process isolation test:** several concurrent processes, each with different process notifiers and registries plus shared globals, must each see only global + their own notifiers, and the dispatch order must match SPEC §17.3. |
| Stdlib | Each function tested against fake ports. Golden tests for `html.extract` field specs. |
| Infrastructure | Contract tests shared across adapters. httpx with respx. A real lxml parser on fixture HTML. HAR round-trip. |
| Interface | click `CliRunner` tests for every command and exit code. SDK facade smoke tests. |
| E2E | `examples/` templates run through `crowley test` (ReplayTransport) in CI. |
| Architecture | `lint-imports` in CI. A test that the meta-schema in `schema/` matches the one generated from the domain. |

## 10. Tooling

- **Runtime dependencies:** `httpx[http2]`, `ruamel.yaml`, `jsonschema[format]`, `lxml`, `cssselect`, `regex`, `click`.
- **Dev tooling:** `uv`, `ruff` (lint and format), `mypy --strict`, `pytest`, `pytest-asyncio`, `hypothesis`, `respx`, `import-linter`, `coverage`.
- **Packaging:** `src/` layout, a hatchling build backend, a `crowley` console script, and a `py.typed` marker.
