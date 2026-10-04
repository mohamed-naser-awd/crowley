# Crowley SDK — Architecture

| | |
|---|---|
| **Status** | Draft v0.2 |
| **Date** | 2026-10-04 |
| **Related** | [PRD.md](PRD.md), [SPEC.md](SPEC.md) |

Crowley uses an **onion architecture**. Dependencies point inward only. The core (domain and application) knows nothing about HTTP, HTML, YAML libraries or the CLI.

The outer ring is built from two kinds of extension point:

- **Plugins.** These are user-facing and pluggable at runtime:
  - **adapters** (I/O: HTTP now; a browser, WebSocket or anything custom later),
  - **extractors** (turning content into data: HTML, XML, JSON, text; PDF, CSV or anything custom later),
  - **functions** and expression **helpers**.
  
  Built-in plugins register through exactly the same API as user plugins.
- **Ports and providers.** These are internal plumbing. The core defines `Protocol` **ports** (template source, YAML parser, schema validator, DNS resolver, secrets, trace sink, cassette store). Infrastructure **providers** implement them.

> **Terminology:** "adapter" in this document always means an *I/O plugin* (`BaseAdapter`, SPEC §13.1). An implementation of an internal port is called a *provider*.

---

## 1. Layers

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ 4. Interface  (crowley.interface)                                             │
│    SDK facade `Crowley`, CLI, pytest plugin — the composition root            │
│ ┌───────────────────────────────────────────────────────────────────────────┐ │
│ │ 3. Plugins & providers  (siblings, independent of each other)             │ │
│ │    crowley.adapters        built-in adapters      (http: httpx)           │ │
│ │    crowley.extractors      built-in extractors    (html, xml, json, text) │ │
│ │    crowley.stdlib          built-in functions     (paginate, transform,   │ │
│ │                                                    control, extract.auto)  │ │
│ │    crowley.infrastructure  port providers         (ruamel, jsonschema,    │ │
│ │                                                    sources, secrets, ...)  │ │
│ │ ┌───────────────────────────────────────────────────────────────────────┐ │ │
│ │ │ 2. Application  (crowley.application)                                 │ │ │
│ │ │    use cases, compiler, runtime, Process, event bus, registries,      │ │ │
│ │ │    BaseAdapter + AdapterService, BaseExtractor + field engine, ports  │ │ │
│ │ │ ┌───────────────────────────────────────────────────────────────────┐ │ │ │
│ │ │ │ 1. Domain  (crowley.domain)                                       │ │ │ │
│ │ │ │    template model, step AST, values, expressions, function /      │ │ │ │
│ │ │ │    adapter / extractor specs, Exchange, FieldSpec, events, errors │ │ │ │
│ │ │ └───────────────────────────────────────────────────────────────────┘ │ │ │
│ │ └───────────────────────────────────────────────────────────────────────┘ │ │
│ └───────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Dependency rules (enforced by `import-linter` in CI)

| Layer | May import | Must not import |
|---|---|---|
| `domain` | Python standard library only | anything else in `crowley`, any third-party package |
| `application` | `domain`, Python standard library | ring 3, `interface`, third-party packages |
| `adapters` | `domain`, `application` (BaseAdapter, FunctionSpec, ...), third-party packages | other ring-3 packages, `interface` |
| `extractors` | `domain`, `application` (BaseExtractor, ...), third-party packages | other ring-3 packages, `interface` |
| `stdlib` | `domain`, `application` (public function API) | other ring-3 packages, `interface`, third-party packages |
| `infrastructure` | `domain`, `application` (ports), third-party packages | other ring-3 packages, `interface` |
| `interface` | everything | — |

**Isolation rules:**
- The four ring-3 packages are independent of each other.
- Each adapter (`crowley.adapters.<name>`) and each extractor (`crowley.extractors.<name>`) is independent of its siblings, so any of them can later be split into a separate distribution.
- Built-ins get no shortcuts. A built-in adapter or extractor can only use what a third-party one could.

```toml
# pyproject.toml
[tool.importlinter]
root_package = "crowley"
include_external_packages = true

[[tool.importlinter.contracts]]
name = "Onion layers"
type = "layers"
layers = [
  "crowley.interface",
  "crowley.adapters | crowley.extractors | crowley.stdlib | crowley.infrastructure",
  "crowley.application",
  "crowley.domain",
]

[[tool.importlinter.contracts]]
name = "Core has no third-party deps"
type = "forbidden"
source_modules = ["crowley.domain", "crowley.application", "crowley.stdlib"]
forbidden_modules = ["httpx", "ruamel", "jsonschema", "lxml", "cssselect", "regex", "jsonpath", "click"]

[[tool.importlinter.contracts]]
name = "Extractors are independent"
type = "independence"
modules = ["crowley.extractors.html", "crowley.extractors.xml",
           "crowley.extractors.json", "crowley.extractors.text"]
# "Adapters are independent" is added when a second built-in adapter lands.
```

---

## 2. Package layout

```
crowley/
├── pyproject.toml
├── docs/                         PRD.md, SPEC.md, ARCHITECTURE.md
├── schema/                       crowley-1.schema.json  (generated from domain, published)
├── examples/                     example templates + cassettes + snapshots
├── src/crowley/
│   ├── __init__.py               public API re-exports (Crowley, function, BaseAdapter, BaseExtractor,
│   │                             Plugin, NotifierRegistry, errors, ...)
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
│   │   ├── adapters/             AdapterSpec, Exchange, ExchangeResult, Target (URI), TargetKind,
│   │   │                         RetryPolicy, host-pattern matching
│   │   ├── extractors/           ExtractorSpec, FieldSpec AST, Query, QueryLanguage, MediaType matching
│   │   ├── events/               Event dataclasses per catalog entry, actions (Skip/Replace/Retry/Abort)
│   │   ├── errors/               CrowleyError hierarchy, codes, SourceLocation, redaction
│   │   ├── services/             domain-service protocols: Clock, RandomSource, RegexEngine
│   │   └── common/               Duration, ByteSize, SemVer, TemplatePath, identifiers
│   │
│   ├── application/              ── LAYER 2 ──────────────────────────────────────────
│   │   ├── ports/                TemplateSource, TemplateParser, SchemaValidator, HostResolver,
│   │   │                         SecretsProvider, TraceSink, CassetteStore
│   │   ├── registry/             Registry (functions, adapters, extractors, helpers), Plugin,
│   │   │                         HandlerBinding, requires resolution, replace-compatibility checks
│   │   ├── adapters/             BaseAdapter, AdapterContext, AdapterSession, AdapterService
│   │   │                         (exchange pipeline), PermissionGuard, RateLimiter, Recorder/Replayer
│   │   ├── extractors/           BaseExtractor, FieldExtractionEngine, generated extractor
│   │   │                         functions (<name>.parse/select/select_all/extract), media-type router
│   │   ├── compiler/             RawDocument → domain AST, meta-schema check, static validator (E3xx)
│   │   ├── runtime/              Process, Executor, StepPipeline, step handlers, Scope/Frame (carries prev),
│   │   │                         ControlSignal, FunctionInvoker, FunctionContext, Body, LimitsGuard,
│   │   │                         ConcurrencyManager, OutputCollector, RunState
│   │   ├── events/               EventBus, Notifier, NotifierSet, NotifierRegistry, NotifierHandle,
│   │   │                         filters, interception pipeline, revalidation
│   │   ├── testing/              TemplateTestRunner (expectations, snapshots)
│   │   └── use_cases/            LoadTemplate, ValidateTemplate, ExplainOperation, RunOperation,
│   │                             StreamOperation, TestTemplate, RecordOperation
│   │
│   ├── adapters/                 ── LAYER 3: built-in adapters ───────────────────────
│   │   └── http/                 HttpAdapter(BaseAdapter) on httpx; http.* functions; request/response
│   │                             models; header merging; redirects; HAR import/export
│   │
│   ├── extractors/               ── LAYER 3: built-in extractors ─────────────────────
│   │   ├── html/                 HtmlExtractor (lxml + cssselect): css, xpath
│   │   ├── xml/                  XmlExtractor (lxml): xpath, css
│   │   ├── json/                 JsonExtractor (python-jsonpath): jsonpath
│   │   └── text/                 TextExtractor (regex): regex
│   │
│   ├── stdlib/                   ── LAYER 3: built-in functions ──────────────────────
│   │   ├── __init__.py           StdlibPlugin
│   │   ├── paginate.py           paginate.by_page/by_offset/by_cursor/by_next_link
│   │   ├── transform.py          transform.map/filter/dedupe/sort/group_by/flatten
│   │   ├── control.py            control.retry/sleep/parallel
│   │   └── extract.py            extract.auto (media-type routing)
│   │
│   ├── infrastructure/           ── LAYER 3: port providers ──────────────────────────
│   │   ├── yaml/                 RuamelTemplateParser (positions, dup keys, no custom tags)
│   │   ├── schema/               JsonSchemaValidator (jsonschema, Draft 2020-12, format checks)
│   │   ├── regex/                RegexLibEngine (`regex` with timeouts) for expression helpers
│   │   ├── network/              SystemHostResolver (getaddrinfo; IP pinning against DNS rebinding)
│   │   ├── cassettes/            JsonCassetteStore (read/write cassette files)
│   │   ├── sources/              DirectorySource, FileSource, InMemorySource   (later: HubSource)
│   │   ├── secrets/              DictSecrets, EnvSecrets
│   │   ├── trace/                JsonTraceSink, NullTraceSink
│   │   └── system/               SystemClock, SystemRandom, FrozenClock, SeededRandom
│   │
│   └── interface/                ── LAYER 4 ──────────────────────────────────────────
│       ├── sdk.py                Crowley facade + container (wires providers, registers built-in plugins)
│       ├── config.py             Limits and other public config dataclasses
│       ├── cli/                  click app: run, validate, explain, test, record, functions,
│       │                         adapters, extractors, schema, init
│       └── pytest_plugin.py      fixtures: crowley, cassette(...), run_operation(...)
│
└── tests/
    ├── domain/                   pure unit tests (+ hypothesis for expression parser)
    ├── application/              runtime, pipeline and field-engine tests with fake adapters/extractors
    ├── adapters/                 adapter contract suite + http specifics (respx)
    ├── extractors/               extractor contract suite + golden tests per extractor
    ├── stdlib/                   function tests with fake adapters/extractors
    ├── infrastructure/           provider tests (yaml positions, schema, sources, cassettes)
    ├── interface/                CLI tests (click runner), SDK facade tests
    └── e2e/                      examples/ templates replayed from cassettes
```

---

## 3. Domain layer

The domain layer is pure, synchronous, deterministic and has no I/O.

- **Immutable models.** Everything uses `@dataclass(frozen=True, slots=True)`. A compiled `Template` is immutable and safe to share across concurrent processes.
- **Step AST.**
  - Each step kind is its own class. Every step carries its `TemplatePath` and `SourceLocation`.
  - Expressions are stored **compiled** (`Expression` AST plus original source), never as strings.
- **Expression engine.**
  - Hand-written lexer and Pratt parser, and a tree-walking evaluator with a step budget.
  - Regex helpers depend on the `RegexEngine` protocol, and `now()`/`uuid()` on `Clock`/`RandomSource`. These are domain-service protocols, implemented outside the domain.
- **Protocol-agnostic I/O model.**
  - `Exchange` = `{adapter, target: Target(URI), request: Mapping, options}`.
  - `ExchangeResult` = `{response: Mapping, meta}`.
  - The domain never names HTTP. HTTP's request and response shapes live in `adapters/http` and are described to the core only through the adapter's JSON Schemas.
- **Format-agnostic extraction model.**
  - `FieldSpec` is the parsed field tree: queries with language, fallbacks, attr, `all`, `required`, `default`, nested fields.
  - `Query` = `{language, source}`.
  - The domain never names HTML.
- **Specs.** `FunctionSpec`, `AdapterSpec` and `ExtractorSpec` are data. The behaviour (base classes, invocation, pipelines) lives in the application layer.
- **Errors** are domain objects. Every layer raises the same hierarchy.
- **Events** are plain dataclasses with mutable payload fields. Actions are value objects (`Skip`, `Replace`, `Retry`, `Abort`).

## 4. Application layer

The application layer holds orchestration and policy. It is async (asyncio) and reaches the outside world only through **adapters** (network and other I/O) and **ports** (everything else).

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

class HostResolver(Protocol):                                          # used by PermissionGuard (SSRF)
    async def resolve(self, host: str) -> list[IPAddress]: ...

class CassetteStore(Protocol):
    def load(self, path: str) -> Cassette: ...
    def save(self, path: str, cassette: Cassette) -> None: ...

class SecretsProvider(Protocol):
    def get(self, name: str) -> str | None: ...

class TraceSink(Protocol):
    def write(self, event: SerializedEvent) -> None: ...
```

### 4.2 Registry and plugins

There is one `Registry` per `Crowley` instance, holding four tables: functions, adapters, extractors and helpers.

- `Registry.add_adapter(a)`:
  - validates the `AdapterSpec` schemas (`E903`),
  - checks name conflicts and `replace=` compatibility (`E906`),
  - registers `a.functions()` under the adapter's namespace.
- `Registry.add_extractor(e)`:
  - validates the `ExtractorSpec`,
  - generates `<name>.parse/select/select_all/extract` from the shared field engine,
  - registers the router entry for its `media_types`.
- `Registry.add_function(f)` computes the `HandlerBinding` once with `inspect.signature`. An incompatible signature fails with `E903`.
- **Built-ins** are registered by the composition root as `HttpAdapterPlugin`, `BuiltinExtractorsPlugin` and `StdlibPlugin`, using the same calls as any third-party `Plugin`.
- **Per-process overrides** (`cw.init(..., adapters=..., extractors=...)`) are layered over the registry in a `ProcessRegistryView`. The shared registry is never mutated.

### 4.3 Use cases

| Use case | Steps |
|---|---|
| `LoadTemplate` | source.resolve → parser.parse → `template.loaded` event (patchable) → meta-schema → `Compiler.compile` → `StaticValidator.check` (functions, adapters, extractors, query languages, …) → `CompiledTemplate` (cached by content hash) |
| `ValidateTemplate` | Same as load, but collects **all** diagnostics into a `ValidationReport` instead of raising |
| `ExplainOperation` | Renders one operation's compiled AST as a plan (functions, adapters, extractors, loops, hosts, limits). Template-function calls are expanded inline. |
| `RunOperation` | Selects the operation (`E904`) → builds `RunState` → validates inputs and secrets → `Executor.run(operation.steps)` → validates output → closes adapter sessions → `RunResult` |
| `StreamOperation` | `RunOperation` with an async-queue `OutputCollector` that yields emitted items |
| `TestTemplate` | For each operation, for each test: `AdapterService` in **replay** mode with the test's cassette + FrozenClock + SeededRandom → run → check expectations |
| `RecordOperation` | `AdapterService` in **record** mode → run → `CassetteStore.save` (scrubbed); optional HAR export via the http adapter |

### 4.4 Runtime

- **Executor.** An AST walker with one handler per step kind.
  - Control flow (`return`, `break`, `continue`) travels as typed `ControlSignal` **return values**.
  - Errors travel as `CrowleyError` exceptions.
- **Scope stack.** `Frame`s hold `steps`, `vars`, bindings and the current `prev`. Concurrent iterations get forked frames.
- **Guards** are checked at choke points: `LimitsGuard`, `PermissionGuard` (inside `AdapterService`) and the cancellation token.
- **Modules** (`application/runtime`): `frame.py` (`Frame`), `signals.py` (`Return`/`Break`/`Continue`, `HandlerResult`, `StepOutcome`), `state.py` (`RunState`, `RunStats`; `publish` builds event scope views only when an event has listeners, with secrets masked), `pipeline.py` (`StepPipeline`), `executor.py` (`Executor`), `invoker.py` (`FunctionInvoker`, which also runs `local.*` template functions), `context.py` (`FunctionContext`, `Body`), `limits.py` (`LimitsGuard`), `output.py` (`OutputCollector`). `Process` and `RunOperation` live in `application/use_cases` (`process.py`, `run.py`), because they orchestrate the runtime.

**Step pipeline.** Every step kind goes through this one code path. Step handlers are registered *only* through `StepPipeline`, so there is no back door around the events:

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

**Function invocation pipeline** (`FunctionInvoker`, inside the `use` step handler):

```
evaluate with: (non-lazy args) → apply schema defaults → fill x-crowley-from-prev args
  → kwargs = {**args, "prev": prev}
  → emit function.before        (notifiers may change kwargs / skip / replace)
  → validate kwargs             (E403)  ← input schema (args) + prev schema (if declared)
  → bind kwargs to the handler signature (precomputed HandlerBinding)
  → handler(ctx, **bound)       (ctx.body set for block functions)
  → validate result             (E407)  ← output schema
  → emit function.after         (notifiers may change / replace / retry)
  → revalidate if changed
  → on error: emit function.error → step on_error policy
```

### 4.5 Exchange pipeline (`AdapterService`)

Every exchange from every adapter goes through this pipeline, reached via `ctx.exchange(...)`:

```
resolve adapter (process override → registry; missing → E607)
  → open session lazily (adapter.open, once per process per adapter)
  → merge: adapter config < defaults.<adapter> < function args → Exchange
  → validate request against adapter.request_schema   (E403)
  → emit exchange.before         (change request / skip / replace with synthetic result / abort)
  → PermissionGuard (target_kind=network): host allow-list, HostResolver + private-range block (E604)
  → LimitsGuard: max_requests; RateLimiter (per host token bucket)
  → mode = replay?  → Replayer.match(exchange) or E605
          else      → adapter.send(session, exchange)   (reported hops re-checked by PermissionGuard)
  → size cap (E606) → mode = record? → Recorder.append(adapter.serialize(...))
  → validate response against adapter.response_schema (E407)
  → emit exchange.after          (change result / replace / retry)
  → retry policy (emit exchange.retry) → result
  error anywhere → emit exchange.error (retry / replace / abort) → ExchangeError (E6xx)
```

Adapter sessions are closed in `RunOperation`'s `finally` block (success, failure or cancellation).

### 4.6 Extraction (`FieldExtractionEngine`)

The engine is format-agnostic. It walks a compiled `FieldSpec` and calls only the three extractor methods:

```
source (from args or prev) → extractor.parse(raw, base_url, media_type) → root node
  → root query? → extractor.select(node, root.query, root.language) → nodes
  → for each field: try queries in order (fallbacks) → extractor.select(...)
        first match at index > 0 → emit selector.fallback
        no match → default | null | E502 (required)
        attr → extractor.read(node, attr); nested fields → recurse
  → object or list of objects
```

Literal queries are compiled and checked at template load time, so the static validator can report `E326`/`E327` before any network I/O.

### 4.7 Event bus, notifier scopes and processes

- `NotifierSet` is an ordered, versioned collection of notifiers and nested `NotifierRegistry` references. The `Crowley` instance (global scope), every `NotifierRegistry` and every `Process` each own one.
- `Process` owns one `RunState`, one `EventBus`, its `NotifierSet`, its `ProcessRegistryView`, its adapter sessions and a cancellation token. Its bus dispatches over **global set → process registries → process set**, merged by priority (SPEC §17.3).
- **Index cache.** The bus caches a per-event-name index of matching notifiers. The cache key is the combined versions of all reachable sets, so adding or removing a notifier anywhere invalidates it without locking.
- `EventBus.intercept(event)` returns the first `Action`. `EventBus.notify(event)` sends frozen copies to observers and the `TraceSink`.
- **Revalidation.** `intercept` reports `changed` when at least one interceptor with `revalidate=True` ran, and the emitting pipeline then revalidates the event's mutable fields (SPEC §17.1). Interceptors usually edit payloads in place (`event.kwargs["x"] = ...`), which attribute-level dirty tracking can't see, so any revalidating interceptor counts as a change.
- **Modules.** `domain/events` holds the catalog (`CATALOG`, one dataclass per event with `MUTABLE` and `ALLOWED`) and the actions. `application/events/notifiers.py` holds `Notifier`, `NotifierSet`, `NotifierRegistry`, `NotifierHandle` and the `NotifierScope` mixin shared by `Crowley` and `Process`. `application/events/bus.py` holds `EventBus` (`intercept`, `notify`, `publish`).
- **Shared parts:** compiled templates, the registry, adapter instances (not sessions) and the global `NotifierSet` are read-mostly and safe for concurrent processes.

---

## 5. Built-in plugins and providers

### 5.1 Plugins (ring 3, registered like third-party plugins)

| Kind | Name | Package | Library | Notes |
|---|---|---|---|---|
| Adapter | `http` | `crowley.adapters.http` | httpx | HTTP/1.1 + HTTP/2. Redirects handled by the adapter and reported as hops, so each hop is permission-checked. Provides `http.*`. HAR import/export. |
| Extractor | `html` | `crowley.extractors.html` | lxml, cssselect | css (default), xpath |
| Extractor | `xml` | `crowley.extractors.xml` | lxml | xpath (default), css |
| Extractor | `json` | `crowley.extractors.json` | python-jsonpath | jsonpath |
| Extractor | `text` | `crowley.extractors.text` | regex | regex (fallback for any media type) |
| Functions | `paginate`, `transform`, `control`, `extract` | `crowley.stdlib` | — | |

### 5.2 Providers (ring 3, implement application ports)

| Port | Provider | Library | Notes |
|---|---|---|---|
| TemplateParser | `RuamelTemplateParser` | ruamel.yaml | Position map for every node; rejects duplicate keys and custom tags |
| SchemaValidator | `JsonSchemaValidator` | jsonschema | Draft 2020-12 with format checking |
| HostResolver | `SystemHostResolver` | asyncio `getaddrinfo` | The resolved IP is pinned for the connection (DNS-rebinding protection) |
| CassetteStore | `JsonCassetteStore` | — | |
| RegexEngine | `RegexLibEngine` | regex | Per-call timeout, for expression helpers |
| TemplateSource | `DirectorySource`, `FileSource`, `InMemorySource` | — | `HubSource` later |
| SecretsProvider | `DictSecrets`, `EnvSecrets` | — | |
| TraceSink | `JsonTraceSink`, `NullTraceSink` | — | |
| Clock / RandomSource | `SystemClock`, `SystemRandom`, `FrozenClock`, `SeededRandom` | — | |

## 6. Interface layer and composition root

`interface/sdk.py` is the **only** place where providers are created and built-in plugins are registered:

```python
class Crowley:
    def __init__(self, *, sources=None, functions=(), adapters=(), extractors=(), plugins=(),
                 helpers=None, secrets=None, limits=None, load_entry_points=False,
                 providers: Providers | None = None, ...):        # override ports (tests, custom stacks)
        self._providers = providers or Providers.default()
        self._registry = Registry()
        for plugin in (HttpAdapterPlugin(), BuiltinExtractorsPlugin(), StdlibPlugin(), *plugins):
            plugin.register(self._registry)
        for a in adapters:   self._registry.add_adapter(a, allow_builtin_replace=True)
        for e in extractors: self._registry.add_extractor(e, allow_builtin_replace=True)
        ...
    def register_adapter(self, adapter, *, replace: str | None = None) -> None: ...
    def register_extractor(self, extractor, *, replace: str | None = None) -> None: ...
```

Passing a configured built-in in `adapters=` (e.g. `HttpAdapter(user_agent=...)`) replaces the default instance of that built-in. The CLI is a thin client of the same facade.

---

## 7. Extension points

| Need | How |
|---|---|
| New functions | `@function` + `register()`, or a `Plugin` |
| New expression helpers | `Crowley(helpers={...})` or `Registry.add_helper` (pure functions only) |
| **New protocol / transport** (WebSocket, GraphQL, gRPC, SOAP, files…) | Subclass `BaseAdapter`, implement `open`/`send`/`close` (+ `functions()`), register it. Permissions, limits, notifiers, retries and record/replay come from `AdapterService`. |
| **Browser rendering (later)** | A `browser` adapter (Playwright) in `crowley.adapters.browser`, with `browser.*` functions. No core change. |
| **Different HTTP stack** (e.g. curl-cffi) | `register_adapter(MyHttp(), replace="http")` with a compatible contract. Templates are unchanged. |
| **New content format** (CSV, PDF, YAML, Markdown…) | Subclass `BaseExtractor`, implement `parse`/`select`/`read`, register it. The field engine, generated functions, `extract.auto` routing and validation come for free. |
| **Different HTML engine** (e.g. selectolax) | `register_extractor(MyHtml(), replace="html")` |
| **Hub (later)** | `HubSource` implementing `TemplateSource` |
| Other template formats | Implement `TemplateParser` (e.g. JSON or TOML) |
| Output sinks (later) | `OutputSink` port consuming the `OutputCollector` stream |

---

## 8. Concurrency model

- One event loop per process run. `for_each`, `control.parallel` and paginate prefetch (if added) use `asyncio.TaskGroup`, bounded by semaphores (local `concurrency` and global `limits.max_concurrency`).
- Sync user functions run in `asyncio.to_thread`. Extractors run in place by default. An extractor MAY declare `offload_threshold_bytes` so that large documents are parsed in a thread.
- `run_sync()` uses `asyncio.run`. If an event loop is already running, it raises `E902`.
- `Process.cancel()` cancels that process's root task only. Adapter sessions are still closed. Cancellation surfaces as `E803`.

## 9. Testing strategy

| Layer | Approach |
|---|---|
| Domain | Pure unit tests. Property-based tests (hypothesis) for the lexer, parser and evaluator; the parser may only fail with `E321`. |
| Application | Runtime tests with fake adapters and extractors (`FakeAdapter`, `FakeExtractor`, `InMemorySource`). Static-validator tests: one fixture template per error code. **Notifier conformance test** (every step kind fires before/after exactly once; changes reach the next step). **Piping test** (SPEC §8.4). **Process isolation test** (SPEC §17.3). **Exchange pipeline test:** permissions, limits, retries, replay/record and events behave the same for a fake adapter as for `http`. |
| Adapters | A reusable **adapter contract suite**, `crowley.testing.adapter_contract`, run against `http` and available to plugin authors. HTTP specifics with respx (redirect hops, headers merge, HAR import/export). |
| Extractors | A reusable **extractor contract suite**, `crowley.testing.extractor_contract`, run against every built-in and available to plugin authors. Golden field-spec tests per extractor. |
| Stdlib | Each function tested against fake adapters and extractors. |
| Infrastructure | Provider tests: YAML positions, schema validation, sources, cassette round-trip. |
| Interface | click `CliRunner` tests for every command and exit code. SDK facade smoke tests. |
| E2E | `examples/` templates run through `crowley test` (cassette replay) in CI. |
| Architecture | `lint-imports` in CI (layers, forbidden third-party, independence). A test that `schema/` matches the meta-schema generated from the domain. |

## 10. Tooling

- **Runtime dependencies** (each used only by the ring-3 package that needs it):

  | Dependency | Used by |
  |---|---|
  | `httpx[http2]` | `adapters.http` |
  | `lxml`, `cssselect` | `extractors.html` and `extractors.xml` |
  | `python-jsonpath` | `extractors.json` |
  | `regex` | `extractors.text` and `infrastructure.regex` |
  | `ruamel.yaml`, `jsonschema[format]` | `infrastructure` |
  | `click` | `interface` |

- **Dev tooling:** `uv`, `ruff` (lint and format), `mypy --strict`, `pytest`, `pytest-asyncio`, `hypothesis`, `respx`, `import-linter`, `coverage`.
- **Packaging:** `src/` layout, a hatchling build backend, a `crowley` console script, and a `py.typed` marker. Later, built-in adapters and extractors MAY move behind extras (`crowley[http]`), because each one is an independent package.
