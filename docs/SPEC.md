# Crowley Template & Runtime Specification — `crowley: 1`

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Date** | 2026-10-04 |
| **Related** | [PRD.md](PRD.md), [ARCHITECTURE.md](ARCHITECTURE.md) |

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

---

## 1. Conventions

- **Template path.** A location inside a template, written as `operations.get_page_people.steps[0].do[2].with.url` or `functions.fetch_json.steps[0]`. Every error reports one.
- **Source location.** Written as `file:line:column`. Every error from a YAML template reports one when it is available.
- **Duration.** Either a number of seconds (`1.5`) or a string `<n><unit>` with unit `ms|s|m|h` (`"500ms"`, `"20s"`).
- **Schema.** JSON Schema draft 2020-12, written inline as YAML.
- **Identifiers.**
  - Operation names, step ids, variable names and template function names match `^[a-z_][a-z0-9_]*$`.
  - An operation is addressed as `<template-id>#<operation>`, e.g. `examples/company-directory#get_page_people`.
  - Function names are namespaced, `<namespace>.<name>`, with each part matching the pattern above.
- **Reserved function namespaces:** `crowley`, `paginate`, `transform`, `control`, `extract`, `local`, `template`, the built-in adapter `http`, the built-in extractors `html`, `xml`, `json`, `text`, and `browser` (kept for a future adapter).
- **Adapter and extractor names** are function namespaces too (§13). A plugin adapter or extractor owns the namespace of its name.
- **Reserved expression roots:** `prev`, `inputs`, `secrets`, `steps`, `vars`, `args`, `loop`, `run`, `page`, `item`, `result`, `error`.
- **Reserved argument name:** `prev` cannot be declared as a property in any function input schema (`E903`).

---

## 2. Template structure

A template describes **one site or API** and exposes one or more **operations**, for example `get_page_info` or `get_page_people`.

- **Shared by all operations:** everything about the site — metadata, permissions, secrets, defaults, limits, shared schemas, and **template functions** (named groups of steps).
- **Per operation:** its own inputs, steps, output and tests.

Every template has at least one operation. Callers always run one operation at a time: `cw.run(tpl, "get_page_people", inputs=...)`.

### 2.1 Top-level keys

| Key | Required | Type | Description |
|---|---|---|---|
| `crowley` | ✅ | integer | Spec version. MUST be `1`. |
| `id` | ✅ | string | `owner/name`, pattern `^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$`. |
| `version` | ✅ | string | SemVer 2.0 (`1.2.0`). Versions the whole template, including all of its operations. |
| `name` | ✅ | string | Human-readable name. |
| `description` | ✅ | string | What site or API the template covers. |
| `tags` | | string[] | |
| `authors` | | string[] | |
| `license` | | string | SPDX id. |
| `homepage` | | string (uri) | |
| `requires` | | string[] | Non-stdlib functions the template needs (§11.6). |
| `secrets` | | map | Secrets the template needs (§3.3). Shared by all operations. |
| `permissions` | ✅ | object | Hosts the template may contact (§4). |
| `defaults` | | object | Default arguments, by namespace (§5). |
| `limits` | | object | Resource limits for every operation (§6). |
| `schemas` | | map | Shared JSON Schemas, referenced with `$ref: "#/schemas/<name>"` (§2.4). |
| `functions` | | map | Template functions: named step groups that any operation can call (§11.4). |
| `operations` | ✅ | map | At least one operation (§2.2). Keys are operation names (identifiers). |
| `x-*` | | any | Extension keys. Ignored by the SDK and preserved for tools and the hub. |

Any other top-level key is an error (`E201`). An empty `operations` map is an error (`E204`).

### 2.2 Operation keys

| Key | Required | Type | Description |
|---|---|---|---|
| `name` | | string | Human-readable name. Defaults to the operation key. |
| `description` | ✅ | string | What the operation returns. |
| `tags` | | string[] | |
| `inputs` | | map | Input parameters for this operation (§3). |
| `limits` | | object | Per-operation limits, combined with the template limits (the stricter value wins) (§6). |
| `steps` | ✅ | step[] | The operation's main block (§7). |
| `output` | ✅ | object | Output definition and schema (§14). |
| `tests` | | test[] | Offline tests for this operation (§19). |
| `x-*` | | any | Extension keys. |

**Isolation:** operations don't share runtime state. Each run executes exactly one operation, with its own `inputs`, `steps`, `vars` and `prev` chain. Operations share logic only through **template functions** (`use: local.<name>`). To reuse an operation from *another* template, use composition (§11.5).

### 2.3 Complete example

```yaml
crowley: 1
id: examples/company-directory
version: 1.0.0
name: Example company directory
description: Company pages and their people from directory.example.com.
tags: [companies, example]

permissions:
  hosts: [directory.example.com]

secrets:
  API_TOKEN: { description: Directory API token }

defaults:
  http:
    headers:
      Accept: application/json
      Authorization: "Bearer ${{ secrets.API_TOKEN }}"
    timeout: 20s
    retry: { times: 2, on_status: [429, 503], backoff: exponential }

limits:
  max_requests: 200

schemas:
  person:
    type: object
    additionalProperties: false
    required: [id, name]
    properties:
      id:    { type: string }
      name:  { type: string, minLength: 1 }
      title: { type: [string, "null"] }

# ── Template functions: step groups reused by several operations ───────────────
functions:
  fetch_json:
    description: GET a directory API path and return the JSON body.
    input:
      type: object
      required: [path]
      properties:
        path:  { type: string }
        query: { type: object, default: {} }
    output: { type: object }
    steps:
      - use: http.get
        with:
          url: ${{ 'https://directory.example.com/api/' + args.path }}
          query: ${{ args.query }}
          response_type: json
      - return: ${{ prev.body }}

  normalize_person:
    description: Map a raw API person to the public person shape.
    input:
      type: object
      required: [raw]
      properties: { raw: { type: object } }
    output: { $ref: "#/schemas/person" }
    steps:
      - return:
          id:    ${{ str(args.raw.id) }}
          name:  ${{ trim(args.raw.full_name) }}
          title: ${{ args.raw?.headline }}

# ── Operations ────────────────────────────────────────────────────────────────
operations:
  get_page_info:
    description: Details of one company page.
    inputs:
      company: { type: string, minLength: 1, description: Company slug }
    steps:
      - use: local.fetch_json
        with:
          path: ${{ 'companies/' + url_encode(inputs.company) }}
    output:
      value:                                   # prev = fetch_json result
        name:      ${{ prev.name }}
        followers: ${{ prev.follower_count }}
        website:   ${{ prev?.website }}
      schema:
        type: object
        required: [name, followers]
        properties:
          name:      { type: string }
          followers: { type: integer, minimum: 0 }
          website:   { type: [string, "null"], format: uri }
    tests:
      - name: acme page
        inputs: { company: acme }
        fixtures: fixtures/page-info-acme.cassette.json
        expect: { snapshot: snapshots/page-info-acme.json }

  get_page_people:
    description: People listed on a company page.
    inputs:
      company:   { type: string, minLength: 1 }
      max_pages: { type: integer, default: 5, minimum: 1, maximum: 50 }
    steps:
      - use: paginate.by_cursor
        with:
          max_pages: ${{ inputs.max_pages }}
          next_cursor: ${{ result.next_cursor }}      # lazy: evaluated after each page
        do:
          - use: local.fetch_json
            with:
              path: ${{ 'companies/' + url_encode(inputs.company) + '/people' }}
              query:
                cursor: ${{ page.cursor }}            # null on the first page → omitted
      - for_each: ${{ flatten(prev[*].people) }}
        as: person
        do:
          - use: local.normalize_person
            with: { raw: "${{ person }}" }
          - emit: ${{ prev }}
    output:
      schema:
        type: array
        items: { $ref: "#/schemas/person" }
    tests:
      - name: two pages
        inputs: { company: acme, max_pages: 2 }
        fixtures: fixtures/people-acme.cassette.json
        expect:
          min_items: 1
          assert:
            - ${{ len(unique(output[*].id)) == len(output) }}
```

### 2.4 Shared schemas

- `schemas.<name>` holds a JSON Schema.
- Anywhere a schema is accepted (operation `inputs`, `output.schema`, template function `input`/`output`), `$ref: "#/schemas/<name>"` refers to it. References resolve against the template document.
- `$ref` to any external URL is an error (`E319`).

### 2.5 YAML rules

- YAML 1.2. Duplicate keys are an error (`E102`). Custom tags (`!foo`) are an error (`E101`). Anchors and aliases are allowed.
- In **flow** collections (`{ ... }`, `[ ... ]`), expressions MUST be quoted, because `{` and `}` are flow indicators: `query: { p: "${{ page.number }}" }`.

---

## 3. Inputs and secrets

### 3.1 Inputs

- Inputs are declared **per operation**, under `operations.<op>.inputs`.
- `inputs` maps an input name to a JSON Schema. The schema may also include `default`, `description` and `examples`.
- Crowley builds an object schema from these (`required` = inputs without a `default`) and validates the caller's inputs against it **before** step 1 (`E401`). Unknown inputs are rejected.

### 3.2 Access

- Inside an operation, including nested bodies, expressions read inputs as `inputs.<name>`. Inputs are read-only.
- **Template functions can't read `inputs`.** They get everything through `args`, so they stay reusable across operations (§8.2).
- Referencing an input not declared by the current operation causes `E315`.

### 3.3 Secrets

```yaml
secrets:
  API_TOKEN:
    description: Personal API token
    required: true        # default true
```

- Values come only from the integrator, through `secrets=` or a `SecretsProvider`. A missing required secret fails the run before step 1 (`E402`).
- Expressions read them as `secrets.<NAME>`. Referencing an undeclared secret is a static error (`E316`).
- Secret values are **tainted**: any occurrence of the exact value in events, traces, logs and errors is replaced with `***`.

---

## 4. Permissions

```yaml
permissions:
  hosts:
    - api.example.com
    - "*.example.com"       # any subdomain, not the apex
    - example.com:8443      # explicit port
```

- Permissions apply to every exchange made through an adapter whose `target_kind` is `network` (§13.1), not just HTTP.
- Every exchange MUST target a permitted host. Otherwise it fails with `E604`.
- This is checked **after** notifiers run (`exchange.before`) and again at **every hop** the adapter reports, such as HTTP redirects.
- **Static check:** a literal URL (or the literal origin of an interpolated URL) whose host is not permitted causes `E318`.
- IP literals MUST be listed explicitly. `*` alone is not allowed.
- **SSRF guard:** after DNS resolution, loopback, private, link-local and multicast addresses are blocked (`E604`) unless the integrator configures the adapter with `allow_private_networks=True` (e.g. `HttpAdapter(allow_private_networks=True)`).
- A child template (§11.5) can only reach hosts that are allowed by **both** its own `permissions` and the parent's.

---

## 5. Defaults

`defaults.<namespace>` provides default arguments for a namespace.

- **Adapters:** `defaults.<adapter>` is merged into every exchange of that adapter and validated against the adapter's `defaults_schema` (§13.4). For example, `defaults.http` sets headers, timeouts and retries for all HTTP calls.
- **Other plugins:** a plugin MAY declare a defaults schema for its own namespace.
- Defaults for an unknown namespace cause `E203`.

---

## 6. Limits

```yaml
limits:
  max_requests: 500           # exchanges, any adapter; default 1000
  max_duration: 10m           # default 30m
  max_items: 10000            # emitted items, default 100000
  max_depth: 8                # nested blocks / template calls, default 16
  max_loop_iterations: 5000   # per loop, default 10000
  max_concurrency: 8          # global in-flight iterations/requests, default 16
  max_response_bytes: 10MB    # default 10MB
  rate:
    per_host: "5/s"           # default "10/s"
```

- `limits` can be set at the template level and per operation (`operations.<op>.limits`).
- **Effective limit** = the strictest of the SDK-configured value, the template value and the operation value.
- Exceeding a limit fails the run with `E701` (not catchable).
- `max_retries_per_step` (default 5) caps every retry source: `on_error`, notifier `retry()`, and HTTP retries.

---

## 7. Steps

### 7.1 Common fields

Every step is a mapping with **exactly one** kind key (§7.2). It can also have:

| Field | Type | Description |
|---|---|---|
| `id` | identifier | Unique within its operation or template function (`E307`). Required if the result is referenced. |
| `name` | string | Human label, used in traces. |
| `description` | string | |
| `when` | expression → bool | Guard. If false, the step is skipped, its result is `null`, and `step.skipped` is emitted. |
| `on_error` | see §16.3 | Error policy. Not allowed on `break`, `continue`, `return` or `set` (`E329`). |
| `timeout` | duration | Wall-clock timeout for the step, including nested blocks (`E504`). |

### 7.2 Step kinds

#### `use` — call a function
```yaml
- id: res
  use: http.request
  with: { ... }        # args, validated against the function input schema
  do: [ ... ]          # only for kind=block functions (E304)
```
- `steps.<id>` = the function result. The result also becomes `prev` for the next step (§8.4).
- The function receives `prev` as a keyword argument, alongside the `with:` args (§11.1).
- `use` targets:
  - a registered function `ns.name`,
  - `local.<name>` (§11.4),
  - `template:<ref>` (§11.5).

#### `if` — conditional
```yaml
- if: ${{ steps.res.status == 404 }}
  then: [ ... ]
  else: [ ... ]        # optional
```
- The condition MUST evaluate to a boolean (`E501`).
- `then` and `else` are blocks (§8). `if` has no `steps.<id>` result. For piping, the executed branch receives the incoming `prev`, and its last output flows out of the `if` (§8.4).
- `if` is transparent to control flow: `return`, `break`, `continue` and `emit` inside it act on the enclosing scope.

#### `for_each` — iterate a list
```yaml
- id: details
  for_each: ${{ steps.rows }}
  as: row              # default "item"
  index_as: i          # optional; loop.index is always available
  concurrency: 4       # default 1
  do: [ ... ]
```
- The `for_each` value MUST evaluate to a list (`E501`).
- **Result:** a list with one value per iteration, in input order. Each value is the iteration's block-owner value (§8.1): the `return` value, or else the body's last output. Iterations that end with `continue` add nothing.
- Inside the body, the first step's `prev` is the current item.
- `loop` exposes `index` (0-based), `first`, `last` and `length`.
- `break` stops the loop. With `concurrency > 1`, in-flight iterations are cancelled and their results discarded.

#### `while` — conditional loop
```yaml
- id: polled
  while: ${{ vars.status != 'done' }}
  max_iterations: 30   # REQUIRED (E202 if missing), capped by limits.max_loop_iterations
  do: [ ... ]
```
- The condition is evaluated before each iteration. The result is built the same way as for `for_each`.
- `loop` exposes `index` and `first`.
- Reaching `max_iterations` while the condition is still true fails with `E702`. To end silently, use `break`.

#### `set` — assign variables
```yaml
- set:
    cursor: ${{ steps.res.body.next }}
    seen: ${{ vars.seen + 1 }}
```
Scoping rules are in §8.2.

#### `emit` — produce an output item
```yaml
- emit: ${{ row }}          # or a mapping of expressions
```
The item is validated immediately against `output.schema.items` (`E404`), then delivered to `stream()` consumers and the collected output.

#### `return` — end the current block owner with a value
```yaml
- return: ${{ steps.rows }}
```

#### `break` / `continue`
```yaml
- break: true
- continue: true
```
Allowed only inside a loop body or a block-function body. Elsewhere they cause `E308`. Meaning inside a block function is defined in §11.2.

#### `fail` — stop deliberately
```yaml
- fail: ${{ 'login rejected: ' + steps.login.body.message }}
  code: login_rejected     # optional, user code, exposed as error.user_code
```
Raises `E503`. It is catchable by an enclosing `on_error`.

#### `assert` — check data mid-run
```yaml
- assert: ${{ len(steps.rows) > 0 }}
  message: no rows found — selector probably broken
```
Raises `E406` (a validation error, **not** catchable).

#### `log`
```yaml
- log: ${{ 'page ' + str(page.number) + ': ' + str(len(steps.rows)) + ' rows' }}
  level: info              # debug | info | warning | error
```
Emits a `log` event. It has no effect on data.

---

## 8. Blocks, scopes and control flow

### 8.1 Blocks and block owners

- A **block** is a list of steps that run in order.
- A **block owner** is a construct whose body can be ended with `return`:
  - one loop iteration,
  - one body invocation of a block function,
  - a template function body.
- `then`/`else` branches are blocks but **not** block owners.
- `return` in an operation's main block is an error (`E309`). Use `output.value` instead.
- A block owner's value is the `return` value. If the body finished without `return`, it is the body's final `prev` (§8.4).

### 8.2 Scopes

- Each block has a lexical scope.
- **`steps.<id>`** is visible if `<id>` is an earlier step in the same block, or an earlier step in any ancestor block. Steps inside a body are **not** visible outside it, but the owning step's result is. Violations cause `E305`.
- **`vars.<name>`**:
  - `set` assigns to the nearest enclosing scope where `<name>` already exists. Otherwise it creates `<name>` in the current block's scope.
  - Reading an undefined variable causes `E501` at runtime. The static check `E306` applies when the variable is provably never set.
  - Assigning to an outer-scope variable from inside a `for_each` with `concurrency > 1` causes `E320` (static).
- Loop and block bindings (`as`, `index_as`, `loop`, `page`, ...) are scoped to the body. Inner bindings shadow outer ones.
- **Template functions** see only `args`, `secrets`, `run` and their own `prev`, `steps` and `vars`. They don't see the calling operation's `inputs`, `steps` or `vars`, so the same function works the same way from every operation.
- Step ids are unique per operation and per template function. Two operations may use the same step id.

### 8.3 Execution order

- Steps run in order.
- A step's `with` values are evaluated **just before** it runs, after `when`.
- Lazy arguments (§11.3) are evaluated by the function itself.

### 8.4 Piping: `prev`

The output of every step is passed to the next step. Inside expressions it is called `prev`. Functions receive it as the `prev` keyword argument (§11.1). Templates can still name results with `id` and read them with `steps.<id>`; `prev` is just the implicit link between neighbouring steps.

**Initial `prev` of a block:**

| Block | Initial `prev` |
|---|---|
| Operation main block | The operation's validated `inputs` object |
| `for_each` body | The current item |
| `while` body | The `prev` before the loop, or the previous iteration's value from the second iteration on |
| Block-function body | The `input` passed by the function to `body.run(input=...)`. If none is passed, the `prev` the block function itself received. |
| Template function body | The caller's `prev` (also available as `args.prev`) |
| `then` / `else` | The `prev` coming into the `if` |

**What each step kind passes on as `prev`:**

| Step kind | Outgoing `prev` |
|---|---|
| `use` | The function result |
| `for_each`, `while` | The loop result (list) |
| `if` | The last output of the executed branch. If no branch ran, or the branch produced nothing, it passes the incoming `prev` through. |
| `set`, `emit`, `log`, `assert` | Passes the incoming `prev` through unchanged |
| step skipped by `when` | Passes the incoming `prev` through unchanged |
| step that failed and was handled by `on_error` | The `skip` → `null` or `default` value |
| `return`, `break`, `continue`, `fail` | — (control leaves the block) |

**Notes:**
- `prev` is read-only in expressions. Notifiers can change it (`step.before`, see §17.1).
- With `concurrency > 1`, every iteration has its own `prev` chain, so there is no sharing.
- `prev` is always available. Referencing it never causes a static error, but its value may be `null`.

---

## 9. Values

Runtime values are:

| Type | Notes |
|---|---|
| `null`, `bool`, `int`, `float`, `string`, `list`, `object` | JSON-compatible. Objects keep insertion order. |
| `bytes` | Binary bodies. Cannot appear in output (`E405`). |
| `handle` | An opaque reference created by a function (e.g. an HTML node or session). Can be passed between functions but cannot be inspected beyond the members its type exposes, and cannot appear in output. |

Strictness rules:
- `int` and `float` are distinct. `/` always returns `float`, and `//` returns `int`.
- `==` between different types (other than int and float) is `false`.
- Ordering comparisons between different types cause `E501`.

---

## 10. Expression language

### 10.1 Embedding

- A YAML scalar that is **exactly** one `${{ expr }}` (whitespace allowed, quoted or not) evaluates to the expression's **typed** value.
- A scalar that contains `${{ }}` together with other text is **interpolated** into a string. In that case:
  - strings are inserted as-is,
  - numbers and booleans are inserted as their JSON form,
  - lists and objects are inserted as compact JSON,
  - **`null` is an error** (`E501`). Use `default(x, '')`.
- To write a literal `${{`, use `$${{`.

### 10.2 Grammar (informal, by increasing precedence)

```
expr        := lambda | conditional
lambda      := IDENT '=>' expr | '(' IDENT (',' IDENT)* ')' '=>' expr     # only as a helper argument
conditional := or_expr ('if' or_expr 'else' expr)?
or_expr     := and_expr ('or' and_expr)*
and_expr    := not_expr ('and' not_expr)*
not_expr    := 'not' not_expr | comparison
comparison  := additive (('=='|'!='|'<'|'<='|'>'|'>='|'in'|'not in') additive)*
additive    := term (('+'|'-') term)*
term        := unary (('*'|'/'|'//'|'%') unary)*
unary       := '-' unary | postfix
postfix     := primary ( '.' IDENT | '?.' IDENT | '[' expr ']' | '[' slice ']' | '[*]' | call )*
call        := '(' (expr (',' expr)*)? ')'                                 # only on helper names
primary     := literal | IDENT | '(' expr ')' | list | object
literal     := 'null' | 'true' | 'false' | NUMBER | STRING                  # STRING: '...' or "..."
list        := '[' (expr (',' expr)*)? ']'
object      := '{' (key ':' expr (',' key ':' expr)*)? '}'                   # key: IDENT | STRING
```

### 10.3 Semantics

- **Member access.**
  - `a.b` on an object with no key `b` causes `E501`. `a?.b` returns `null` if `a` is `null` or the key is missing.
  - `a[i]` with an out-of-range index causes `E501`. Negative indexes count from the end. Slices use Python syntax.
- **Projection.** `xs[*].title` maps the member access over a list. Projections chain: `pages[*].items[*].id` gives a nested list, and `flatten()` flattens it.
- **`+`** works on numbers, strings (concatenation) and lists (concatenation). Mixing types causes `E501`.
- **`in`** works on substrings, list membership and object keys.
- **Strict booleans.** Operands of `and`, `or`, `not`, `if` conditions, `when`, `while` and `assert` MUST be booleans (`E501`).
- **Sandbox limits.**
  - No assignment.
  - No attribute access on Python objects.
  - Calls are allowed only to registered helpers.
  - Max expression source length: 4,096 characters.
  - Max evaluation steps: 100,000 per evaluation (`E703`).
  - Regex helpers run with a 100 ms timeout (`E703`).

### 10.4 Built-in helpers (v1)

| Group | Helpers |
|---|---|
| Types | `str(x)`, `int(x)`, `float(x)`, `bool(x)`, `type_of(x)`, `is_null(x)` |
| Null handling | `default(x, fallback)` (fallback if `x` is null), `coalesce(a, b, ...)` |
| Strings | `len`, `trim`, `lower`, `upper`, `replace(s, old, new)`, `split(s, sep)`, `join(xs, sep)`, `starts_with`, `ends_with`, `contains`, `pad_left`, `pad_right`, `slugify` |
| Regex | `regex_match(s, p)` → bool, `regex_find(s, p, group=0)` → str or null, `regex_find_all(s, p)` → list, `regex_replace(s, p, r)` |
| Numbers | `round(x, n)`, `abs`, `min`, `max`, `sum`, `parse_number(s, locale='en')` (handles `1,234.5`, `1.234,5`, `12k`) |
| Lists | `flatten(xs, depth=1)`, `unique(xs)`, `sort(xs)`, `sort_by(xs, fn)`, `map(xs, fn)`, `filter(xs, fn)`, `find(xs, fn)`, `any(xs, fn)`, `all(xs, fn)`, `first(xs)`, `last(xs)`, `range(a, b, step=1)`, `chunk(xs, n)`, `zip(a, b)`, `group_by(xs, fn)` |
| Objects | `keys`, `values`, `entries`, `merge(a, b, ...)`, `pick(o, keys)`, `omit(o, keys)`, `get(o, path, default=null)` |
| URLs | `url_join(base, rel)`, `url_parse(u)` → object, `url_encode(s)`, `url_decode(s)`, `query_get(u, name)` |
| Encoding | `json_parse(s)`, `json_dump(x)`, `base64_encode(s)`, `base64_decode(s)`, `html_unescape(s)`, `md5(s)`, `sha256(s)` |
| Dates | `now()` (UTC ISO-8601), `parse_date(s, format=null, tz='UTC')` → ISO-8601, `format_date(iso, format)` |
| Misc | `uuid()` |

- Integrators and plugins MAY register more helpers. A helper MUST be pure and deterministic. `now()` and `uuid()` are the only exceptions, and they go through the `Clock` and `RandomSource` ports so tests can stub them.
- Calling an unknown helper causes `E313`. A wrong number of arguments causes `E314`. Both are static errors.

---

## 11. Functions

### 11.1 Contract

Every function, built-in or custom, is described by a `FunctionSpec`:

| Field | Description |
|---|---|
| `name` | `namespace.name` |
| `version` | SemVer of the function contract |
| `kind` | `plain` or `block` |
| `input` | JSON Schema for `with:` (object schema). MUST NOT declare `prev` (`E903`). |
| `prev` | Optional JSON Schema for the piped `prev` value. If it is set, `prev` is validated like an argument (`E403`). If it is omitted, any `prev` is accepted. |
| `output` | JSON Schema for the result |
| `description` | Shown by `crowley functions show` |
| `events` | Custom event names this function may emit (e.g. `page.before`) |

**Calling convention: everything is a keyword argument.** Crowley calls every handler as

```python
handler(ctx, **kwargs)        # kwargs = validated `with:` args + prev
```

`kwargs` contains:
- every argument present in `with:`, after defaults are applied, notifiers have run and validation has passed,
- `prev` (always present, possibly `None`).

A handler takes what it needs and ignores the rest:

| Handler signature | What it receives |
|---|---|
| `async def f(ctx, **kwargs)` | All `with:` args plus `prev` |
| `async def f(ctx, text, prev=None, **kwargs)` | `text` and `prev` by name; everything else in `kwargs` |
| `async def f(ctx, text)` (no `**kwargs`) | Only the parameters it names. `prev` and any other args are dropped. |

Rules:
- The signature is inspected **once**, at registration.
- A parameter without a default that is not `required` in the input schema (and is not `prev`) is a registration error (`E903`). This catches handlers that could be called without an argument they depend on.
- Optional arguments that are absent from `with:` and have no schema `default` are **not** passed. The handler's own Python default applies.

```python
from crowley import function, FunctionContext

@function(
    name="acme.slugify",
    version="1.0.0",
    input={"type": "object",
           "properties": {"text": {"type": "string"}, "sep": {"type": "string", "default": "-"}},
           "additionalProperties": False},
    output={"type": "string"},
)
async def slugify(ctx: FunctionContext, **kwargs) -> str:
    text = kwargs.get("text") or kwargs["prev"]        # use the piped value when `text` isn't given
    return make_slug(text, kwargs["sep"])

@function(name="acme.each_region", version="1.0.0", kind="block",
          input={"type": "object", "required": ["regions"],
                 "properties": {"regions": {"type": "array"}}},
          output={"type": "array"})
async def each_region(ctx: FunctionContext, regions: list, **kwargs) -> list:
    results = []
    for region in regions:
        outcome = await ctx.body.run(input=region, bindings={"region": region})
        if outcome.is_break:
            break
        if outcome.has_value:
            results.append(outcome.value)
    return results
```

- **Defaulting from `prev`.** An input property marked `"x-crowley-from-prev": true` is filled from `prev` when it is absent from `with:`. This happens before validation, so `required` still applies. For example, `<extractor>.extract.source` (and the other extractor functions) and `transform.*.items` work this way. The static validator treats such properties as satisfied when they are omitted.
- Handlers MAY be sync. Sync handlers run in a worker thread.
- Handlers MUST NOT perform I/O except through `ctx.exchange(...)`. This is a contract rule, so that permissions, limits, notifiers and record/replay stay effective. To support a new protocol, write an adapter (§13.1), not a function that does its own I/O.

`FunctionContext` provides:

| Member | Purpose |
|---|---|
| `ctx.body` | `Body` of the `do:` block (block functions only; `None` otherwise) |
| `ctx.exchange(adapter, request, **options)` | Perform an exchange through the shared pipeline (§13.3). Returns the adapter's response. |
| `ctx.adapter(name)` | The process's view of an adapter: `.spec`, `.session`, `.exchange(request)` |
| `ctx.extractor(name)` | An extractor: `.parse`, `.select`, `.read`, and `.extract(source, fields, root=None)` using the shared field engine (§13.9) |
| `ctx.evaluate(expr, bindings)` | Evaluate a lazy expression argument |
| `ctx.emit_event(name, **data)` | Emit a declared custom event (interceptable) |
| `ctx.log(level, msg)` | Log event |
| `ctx.defaults` | Resolved defaults for this namespace |
| `ctx.run` | Run info (id, template id, started_at) |
| `ctx.step` | Current step info (id, path) |
| `ctx.cancelled` | Cancellation token |

### 11.2 Block functions and `Body`

- `body.run(input=..., bindings={...})` runs the `do:` block once and returns an `Outcome`.
  - `input` becomes the body's initial `prev` (§8.4).
  - `bindings` are extra names visible in the body's expressions (e.g. `page`).
- `Outcome` contains:
  - `value`, `has_value` (false only when the body ended with `continue`),
  - `is_break`, `is_continue`.
- `break` in the body asks the function to stop iterating. `continue` ends the current invocation without a value.
- Each `body.run` is one block-owner invocation (§8.1).

### 11.3 Lazy arguments

- An input property marked `"x-crowley-lazy": true` is **not** evaluated before the call. The function receives a compiled `Expression`, which it evaluates with `ctx.evaluate(expr, bindings)`.
- Static validation still checks the expression's syntax and references. The function's spec declares the bindings it provides, via `"x-crowley-bindings": ["result", "page"]`, and the validator uses that list when resolving references.

### 11.4 Template functions (YAML step groups)

A **template function** is a named group of steps, declared once under the top-level `functions:` key, that any operation can call. This is how operations share logic, such as an authenticated fetch, a normalization, or a whole paginated listing.

```yaml
functions:
  fetch_json:
    description: GET a directory API path and return the JSON body.
    input:                                  # JSON Schema for `with:` (validated, E403)
      type: object
      required: [path]
      properties:
        path:  { type: string }
        query: { type: object, default: {} }
    output: { type: object }                # JSON Schema for the result (validated, E407)
    steps:
      - use: http.get
        with:
          url: ${{ 'https://directory.example.com/api/' + args.path }}
          query: ${{ args.query }}
      - return: ${{ prev.body }}

operations:
  get_page_info:
    steps:
      - use: local.fetch_json
        with: { path: "${{ 'companies/' + inputs.company }}" }
    # ...
  get_page_people:
    steps:
      - use: paginate.by_cursor
        with: { next_cursor: "${{ result.next_cursor }}" }
        do:
          - use: local.fetch_json
            with: { path: "${{ 'companies/' + inputs.company + '/people' }}" }
    # ...
```

| Key | Required | Description |
|---|---|---|
| `description` | ✅ | |
| `input` | | JSON Schema of `with:`. Defaults to an empty object schema. May use `"x-crowley-from-prev": true` on properties (§11.1). |
| `prev` | | Optional JSON Schema for the incoming `prev` |
| `output` | ✅ | JSON Schema of the result |
| `steps` | ✅ | The function body |

- Called with `use: local.<name>`. Template functions are `plain` functions. Calls go through the same invocation pipeline as Python functions: args validated, `function.before`/`function.after` fired, `on_error` applied.
- Inside the body:
  - `args` holds the validated arguments.
  - The initial `prev` is the **caller's** `prev`, also available as `args.prev`, so a function can continue a pipe.
  - The value is the `return` value, or else the body's last output.
- **Scope:** the function sees `args`, `secrets`, `run` and its own `steps`/`vars`/`prev`. It does not see `inputs` (§8.2).
- Template functions MAY call other template functions. Recursion, direct or indirect, is a static error (`E317`).
- `emit` inside a template function is an error (`E323`). Functions return values, and operations decide what to emit.
- A template function that no operation (directly or indirectly) uses is warning `W003`.

### 11.5 Template composition

```yaml
- id: session
  use: template:acme/example-login@^1#login   # <template ref>#<operation>
  with: { username: "${{ inputs.user }}" }    # the child operation's inputs
  secrets: { PASSWORD: ${{ secrets.PASSWORD }} }
```

- `<ref>` is resolved by the configured `TemplateSource` chain. It can be a path (`template:./login.yml#login`) or an id with a SemVer range.
- `#<operation>` is required unless the child template has exactly one operation. An unknown operation causes `E324`.
- A template can't call its own operations through `template:`. Share logic between them with template functions instead (`E324`).
- The child runs in an isolated scope, with its own validation, limits (capped by the parent's) and permissions (intersected, §4).
- **Result:** the child's validated output. The child's `emit`s are collected into its result and are **not** forwarded to the parent's stream.
- Cycles cause `E317`. Nesting counts toward `max_depth`.

### 11.6 Registry, plugins and `requires`

- Registration:
  - `Crowley(functions=[...])`
  - `crowley.register(fn)`
  - `Crowley(plugins=[...])`, where a plugin implements `register(registry)`. One plugin can bundle any mix of functions, adapters, extractors and expression helpers:

    ```python
    class MyCorpPlugin(Plugin):
        def register(self, r: Registry) -> None:
            r.add_adapter(SoapAdapter())          # also registers its soap.* functions
            r.add_extractor(PdfExtractor())       # also generates pdf.extract / select / ...
            r.add_function(decode_token)          # mycorp.decode_token
            r.add_helper("slug", slugify)
    ```
  - Entry points (group `crowley.plugins`) load **only** with `load_entry_points=True`.
- A plugin owns its namespace. Registering into a reserved or already-taken namespace causes `E901` at startup.
- `requires` lists everything the template needs beyond the built-ins:
  - functions: `"mycorp.decode_token@^1"`, `"mycorp.*@^2"`,
  - adapters: `"adapter:ws@^1"`,
  - extractors: `"extractor:pdf@^1"`.
  
  Built-in functions, adapters and extractors don't need to be declared. A missing entry or an incompatible version causes `E302`.
- A function used in a template but not in the stdlib and not listed in `requires` causes `E301`.

---

## 12. Built-in functions v1

Schemas are summarized here. The source of truth is each function's spec, printed by `crowley functions show <name>
crowley adapters list | show <name>                          # registered adapters, schemas, defaults_schema
crowley extractors list | show <name>                        # registered extractors, media types, languages`.

Functions come from three places, and all of them are registered the same way (§11.6):

| Source | Namespaces |
|---|---|
| **Stdlib** (this section) | `paginate.*`, `transform.*`, `control.*`, `extract.*` |
| **Adapters** (§13.1–§13.7) | One namespace per adapter. The built-in `http` adapter provides `http.*` (§13.7). |
| **Extractors** (§13.8–§13.11) | One namespace per extractor. The built-ins provide `html.*`, `xml.*`, `json.*` and `text.*` (§13.11). |

### 12.1 `paginate.*` (block functions)

All four have these in common:
- they bind `page` in the body,
- the result is a list of the body's per-page values (§8.1),
- `break` in the body stops pagination,
- `max_pages` defaults to 100 and is also capped by `limits.max_loop_iterations`,
- they emit `page.before` and `page.after` events.

| Function | Args | `page` binding | Stops when |
|---|---|---|---|
| `paginate.by_page` | `start=1`, `step=1`, `max_pages`, `stop_when` (lazy; binds `result`, `page`), `stop_on_empty=true` | `{number, index}` | `max_pages`; `stop_when` is true; body returned `null` or `[]` (if `stop_on_empty`) |
| `paginate.by_offset` | `start=0`, `limit` (required), `max_pages`, `max_items`, `stop_when`, `stop_on_short_page=true` | `{offset, limit, index}` | as above; result list shorter than `limit`; `max_items` reached |
| `paginate.by_cursor` | `initial=null`, `next_cursor` (lazy, required; binds `result`, `page`), `max_pages` | `{cursor, index}` | `next_cursor` is `null`/`""`; the cursor repeats (`E705`, loop detected) |
| `paginate.by_next_link` | `start_url` (required), `next` (lazy, required; binds `result`, `page`), `max_pages` | `{url, index}` | `next` is `null`; URL already visited (stops quietly) |

`flatten: true` (all four) concatenates list results instead of nesting them.

### 12.2 `transform.*`

Use these when a transformation is too large for one expression or must be named in traces.

| Function | Args → result |
|---|---|
| `transform.map` | `{items, fields}` → list. `fields` maps names to lazy expressions binding `item` and `index`. |
| `transform.filter` | `{items, where}` → list (`where` is lazy and binds `item`) |
| `transform.dedupe` | `{items, by}` → list (keeps the first; `by` is lazy) |
| `transform.sort` | `{items, by, order='asc'}` → list |
| `transform.group_by` | `{items, by}` → object of lists |
| `transform.flatten` | `{items, depth=1}` → list |

In every `transform.*` function, `items` is `x-crowley-from-prev`. If it is omitted, the previous step's output is used.

### 12.3 `control.*`

| Function | Kind | Description |
|---|---|---|
| `control.retry` | block | `{times, backoff='exponential', delay='1s', max_delay='30s', until?}` re-runs the body on catchable errors, or until the lazy `until` (binds `result`) is true. |
| `control.sleep` | plain | `{duration}` |
| `control.parallel` | block | `{branches: N}` runs the body N times concurrently with `branch.index`. Result: list. |

### 12.4 `extract.*`

| Function | Description |
|---|---|
| `extract.auto` | `{source, root?, fields}`. Picks the extractor from the source's media type (§13.10), then behaves like `<extractor>.extract`. `using: <name>` forces a specific extractor. |

---

## 13. Adapters and extractors

Crowley is built around two kinds of plugin:

- **Adapters** talk to the outside world. Each call is an **exchange**: send a request to a **target** (a URI) and get a result back. HTTP is the built-in adapter. A browser, WebSocket, GraphQL, gRPC or a custom protocol are just more adapters.
- **Extractors** turn content into structured data. HTML, XML, JSON and plain text are built in. CSV, PDF or a custom format are just more extractors.

The core (domain and application layers) knows neither HTTP nor HTML. Built-in adapters and extractors register through exactly the same API as user-written ones, and they get no special privileges.

### 13.1 Adapter contract

```python
from crowley import BaseAdapter, AdapterContext, AdapterSession, Exchange, ExchangeResult

class BaseAdapter(ABC):
    name: ClassVar[str]                      # namespace, e.g. "http", "ws", "mycorp_soap"
    version: ClassVar[str]                   # SemVer of the adapter contract
    schemes: ClassVar[tuple[str, ...]]       # target URI schemes handled, e.g. ("http", "https")
    target_kind: ClassVar[Literal["network", "local", "none"]]   # selects the permission checks (§13.3)
    config_schema: ClassVar[dict]            # integrator-side config (proxies, pool size, ...)
    defaults_schema: ClassVar[dict]          # what `defaults.<name>` may contain in templates
    request_schema: ClassVar[dict]           # shape of Exchange.request
    response_schema: ClassVar[dict]          # shape of ExchangeResult.response

    async def open(self, ctx: AdapterContext) -> AdapterSession: ...          # per process (cookies, pools)
    async def send(self, session: AdapterSession, exchange: Exchange) -> ExchangeResult: ...
    async def close(self, session: AdapterSession) -> None: ...
    def functions(self) -> list[FunctionSpec]: ...                            # e.g. http.get, ws.send
    def serialize(self, exchange: Exchange, result: ExchangeResult) -> dict: ...   # record/replay; default provided
    def deserialize(self, data: dict) -> tuple[Exchange, ExchangeResult]: ...
```

- **`Exchange`** = `{adapter, target (URI), request (adapter-specific object), options}`.
- **`ExchangeResult`** = `{response (adapter-specific object), meta (elapsed_ms, bytes_in, ...)}`.
- **Adapters only move data.** Notifiers, permissions, limits, rate limiting, retries, size caps, redaction and record/replay all live in the shared exchange pipeline (§13.3). A user-written adapter gets all of those guarantees without implementing them.
- **Functions never call `send` directly.** They use `ctx.exchange(adapter, request)` (§11.1).

### 13.2 Registration and `requires`

```python
cw = Crowley(adapters=[WsAdapter(ping_interval=20)])        # at construction
cw.register_adapter(MyGraphQLAdapter())                     # later, global
cw.register_adapter(CurlCffiHttpAdapter(), replace="http")  # swap a built-in, keep the template API
process = cw.init(tpl, "op", adapters={"http": FakeHttp()}) # per-process override (tests, special routing)
```

- Adapter names follow the function-namespace rules (§1). Two adapters with the same name cause `E906`, unless `replace=` is used.
- `replace="<name>"` requires a compatible contract: same major `version`, and `request_schema`/`response_schema` accepting the same shapes. Otherwise `E906`. Templates keep working unchanged.
- A per-process override follows the same compatibility rule and only affects that process.
- Templates declare non-built-in adapters in `requires: ["adapter:ws@^1"]`. A missing adapter or incompatible version causes `E302`. Using a non-built-in adapter's functions without declaring it causes `E301`.
- An adapter's `functions()` are registered in its own namespace when the adapter is registered.

### 13.3 Exchange pipeline

Every exchange, for every adapter, runs through the same pipeline in `AdapterService`:

```
merge: adapter config < defaults.<adapter> < function args → Exchange
  → exchange.before        (notifiers may change the request, skip/replace it, abort)
  → permission guard       (target_kind = network: host allow-list, SSRF guard; §4)
  → limits                 (max_requests counts every exchange; per-host rate limit)
  → replay?                (in tests: answer from the cassette; no match → E605)
  → adapter.send()         (redirect-like hops reported back are permission-checked again)
  → size cap               (max_response_bytes → E606)
  → recorder?              (when recording: serialize into the cassette)
  → exchange.after         (notifiers may change the result, replace it, retry)
  → retry policy           (emits exchange.retry)
  → result
  error at any point → exchange.error (retry / replace / abort) → E6xx
```

- `target_kind: local` adapters (e.g. files) are checked against `permissions.paths` (reserved for a later version). In v1, only `network` and `none` are allowed.
- Secrets are redacted in every serialized exchange (events, traces, cassettes).

### 13.4 Defaults and precedence

`defaults.<adapter>` is validated against that adapter's `defaults_schema` (unknown adapter: `E203`). Values are merged in this order, lowest priority first:

1. adapter config (set by the integrator when constructing the adapter)
2. template `defaults.<adapter>`
3. function arguments (`with:`)
4. `exchange.before` notifiers

Objects are deep-merged. Scalars are replaced. Each adapter documents any special merge rules (for example, case-insensitive headers in `http`).

### 13.5 Sessions

- `adapter.open()` is called lazily, on a process's first exchange with that adapter.
- The session lives until the process ends. Then `close()` is called, even on failure or cancellation.
- Sessions are never shared between processes.
- Child templates get their own sessions.
- An adapter MAY offer a block function for nested isolated sessions (e.g. `http.session`).

### 13.6 Record and replay (cassettes)

- **Cassettes** are adapter-agnostic. A cassette is a JSON file holding a list of exchanges, each serialized by its adapter (`serialize`/`deserialize`). This makes record/replay work for every adapter, including user adapters.
- **Matching** is per adapter. The default is `adapter + target + request` fingerprint. Templates can narrow it with `match:` (§19).
- **Recording** scrubs secrets and adapter-declared sensitive fields (`Authorization`, `Cookie` for http).
- **HAR import:** the `http` adapter can import HAR 1.2 files, and `crowley record --har` exports one, for interop with browser tools.

### 13.7 Built-in adapter: `http`

`HttpAdapter` uses httpx. Its `schemes` are `http` and `https`, and its `target_kind` is `network`.

**Config** (integrator side): `HttpAdapter(user_agent=..., proxy=..., timeout=..., http2=True, allow_private_networks=False, max_connections=100)`.

**Functions:**

| Function | Kind | Description |
|---|---|---|
| `http.request` | plain | Full request (arguments below) |
| `http.get`, `http.post`, `http.put`, `http.patch`, `http.delete`, `http.head` | plain | `http.request` with `method` fixed |
| `http.session` | block | Runs `do:` with an isolated cookie jar and connection pool. Args: `headers`, `cookies` (seed values). Result: the body's value. |

**Request arguments (`http.request`):**

| Arg | Type | Default | Notes |
|---|---|---|---|
| `method` | enum | `GET` | `GET HEAD POST PUT PATCH DELETE OPTIONS` |
| `url` | string (uri) | — | Required. Must be absolute. This is the exchange target. |
| `query` | object | `{}` | Values: scalar or list (repeated keys). `null` values are omitted. Merged with any query already in `url`. |
| `headers` | object | `{}` | Header names are case-insensitive. A `null` value removes a default header. |
| `cookies` | object | `{}` | Added to the session jar for this request. |
| `body` | object | — | Exactly one of `json`, `form` (object), `raw` (string), `bytes` (base64 string), `multipart` (list of `{name, value \| content_base64, filename?, content_type?}`). Sets `Content-Type` unless it is given explicitly. |
| `auth` | object | — | `{bearer: str}` or `{basic: {username, password}}` |
| `timeout` | duration | `30s` | |
| `follow_redirects` | bool | `true` | Each hop is permission-checked. |
| `max_redirects` | int | `10` | |
| `proxy` | string | — | Usually set by the integrator or a notifier, not the template. |
| `verify_tls` | bool | `true` | |
| `expect_status` | int[] or `"2xx"`-style strings | `["2xx"]` | Other statuses cause `E601`. |
| `response_type` | enum | `auto` | `auto json text bytes`. `auto` decides from `Content-Type`. |
| `encoding` | string | auto | Overrides text decoding. |
| `retry` | object | none | `{times, backoff: fixed\|exponential, delay='1s', max_delay='30s', on_status=[429,502,503,504], on_network_error=true, respect_retry_after=true}` |

**Response value:**

```yaml
status: 200
ok: true
url: https://final.url/after/redirects
headers: { content-type: "text/html; charset=utf-8", ... }   # lower-cased; multi-value joined with ", "
media_type: text/html                                        # used by extract.auto (§13.10)
cookies: { session: "..." }
body: <json value | string | bytes>      # per response_type
elapsed_ms: 132
redirects: [ "https://..." ]
request: { method, url, headers }        # as actually sent (secrets redacted in traces)
```

**Defaults:** `defaults.http` accepts `headers`, `query`, `cookies`, `timeout`, `follow_redirects`, `max_redirects`, `verify_tls`, `retry`, `expect_status` and `response_type`. Headers are merged case-insensitively. The default `User-Agent` is `crowley/<version> (+https://github.com/mohamed-naser-awd/crowley)` unless it is overridden.

**Sessions:** each process has one implicit http session (cookie jar and connection pool). `http.session` nests an isolated one. `Retry-After` is honoured when `respect_retry_after` is set.

### 13.8 Extractor contract

```python
from crowley import BaseExtractor, Node, Value

class BaseExtractor(ABC):
    name: ClassVar[str]                          # namespace, e.g. "html", "json", "pdf"
    version: ClassVar[str]
    media_types: ClassVar[tuple[str, ...]]       # e.g. ("text/html", "application/xhtml+xml"), for extract.auto
    query_languages: ClassVar[tuple[str, ...]]   # e.g. ("css", "xpath") | ("jsonpath",) | ("regex",)
    default_language: ClassVar[str]              # what a bare `selector:` means
    attributes: ClassVar[tuple[str, ...]]        # readable attrs, e.g. text, html, href…; "*" = any attribute name
    default_attribute: ClassVar[str]             # used when a field has no `attr`

    def parse(self, raw: str | bytes, *, base_url: str | None, media_type: str | None) -> Node: ...
    def select(self, node: Node, query: str, language: str) -> list[Node]: ...
    def read(self, node: Node, attr: str) -> Value: ...
```

- **Three methods only.** An extractor implements `parse`, `select` and `read`. Everything else is shared: the field engine (§13.9), the generated functions, fallback handling, validation and events.
- **`Node`** is an opaque handle (`handle<html.node>`, `handle<json.node>`, ...) (§9).
- **Purity:** extractors MUST be pure and do no I/O. Network access belongs in adapters.
- **Registration** mirrors adapters:
  - `Crowley(extractors=[...])`,
  - `cw.register_extractor(x)` or `cw.register_extractor(x, replace="html")` (compatible `query_languages` and `attributes` required, else `E906`),
  - per-process `cw.init(..., extractors={...})`,
  - templates declare non-built-in extractors with `requires: ["extractor:pdf@^1"]`.

### 13.9 Field extraction engine

Every extractor gets these functions, generated from the contract:

| Function | Args → result |
|---|---|
| `<name>.parse` | `{source}` → `handle<<name>.node>` |
| `<name>.select` | `{source, selector, attr?}` → value of the first match, or `null` |
| `<name>.select_all` | `{source, selector, attr?}` → list of values |
| `<name>.extract` | `{source, root?, fields}` → object (no `root`) or list of objects (one per `root` match) |

**`source`** is `x-crowley-from-prev`. If it is omitted, it is taken from `prev`:
- an adapter response with a `body` → that body; its `url` becomes `base_url` and its `media_type` is passed to `parse`,
- a string, bytes or one of this extractor's node handles → used as-is,
- anything else → `E403`.

**Field spec** (nestable). `selector` uses the extractor's `default_language`. A field can name a language explicitly instead, with a key equal to that language name:

```yaml
fields:
  title:  { selector: "h1" }                                 # html: css
  price:  { xpath: "//span[@itemprop='price']/@content" }    # explicit language
  links:  { selector: "a", attr: href, all: true }           # list
  tags:   { selector: [".tag", ".label"], all: true }        # fallback list: first query that matches wins
  sku:    { selector: ".sku", required: true }               # no match → E502
  stock:  { selector: ".stock", default: "unknown" }
  author:                                                    # nested object
    selector: ".byline"
    fields:
      name: { selector: ".name" }
      url:  { selector: "a", attr: href }
  variants:                                                  # nested list of objects
    selector: ".variant"
    all: true
    fields:
      color: { attr: data-color }
```

```yaml
# same engine, JSON extractor
- use: json.extract
  with:
    root: "$.data.items[*]"
    fields:
      id:    { selector: "$.id" }
      price: { selector: "$.pricing.amount", required: true }
```

- A field with no query reads from the current node itself.
- **Static checks:**
  - a language key the extractor doesn't support → `E326`,
  - an `attr` outside `attributes` (unless the extractor allows `*`) → `E326`,
  - a literal query that doesn't compile → `E327`.
- When a fallback query (not the first) matches, a `selector.fallback` event is emitted with `extractor`, `field`, `index` and `query`.

### 13.10 Media-type routing (`extract.auto`)

`extract.auto` picks the extractor in this order:
1. an explicit `using:` argument,
2. the source's `media_type` matched against registered extractors' `media_types` (exact, then `+suffix` such as `+json`/`+xml`, then wildcard),
3. otherwise `E403` with a hint listing the candidate extractors.

### 13.11 Built-in extractors

| Extractor | Library | Media types | Query languages (default first) | Attributes |
|---|---|---|---|---|
| `html` | lxml + cssselect | `text/html`, `application/xhtml+xml` | `css`, `xpath` | `text` (normalized whitespace), `raw_text`, `html` (outer), `inner_html`, any attribute name |
| `xml` | lxml | `application/xml`, `text/xml`, `*+xml` | `xpath`, `css` | `text`, `raw_text`, `xml`, any attribute name |
| `json` | python-jsonpath | `application/json`, `*+json` | `jsonpath` | `value` (default), `keys`, `length` |
| `text` | regex | `text/plain`, `*` (fallback) | `regex` (match = node; named groups readable as attributes) | `text` (default), `group:<n\|name>` |

---

## 14. Output

Every operation has its own `output` (`operations.<op>.output`).

```yaml
output:
  value: ${{ steps.summary }}    # optional — mutually exclusive with emit
  schema: { ... }                # required, JSON Schema 2020-12
```

| Mode | When | Final result | Validation |
|---|---|---|---|
| **emit** | The operation contains `emit` steps | List of emitted items (completion order when `concurrency > 1`) | `output.schema.type` MUST be `array` (`E311`). Each item is checked against `output.schema.items` when it is emitted (`E404`), and the whole list is checked at the end (`E405`). |
| **value** | `output.value` is set | The expression's value, evaluated after the last step (`prev` is the main block's final `prev`). `value` may also be a mapping or list containing expressions. | Checked against `output.schema` (`E405`) |
| **pipe** | Neither `emit` nor `output.value` | The main block's final `prev` (§8.4) | Checked against `output.schema` (`E405`) |

- Using both `emit` and `output.value` causes `E310`.
- Output MUST NOT contain `bytes` or `handle` values (`E405`).
- `limits.max_items` applies to emitted items.

---

## 15. Validation pipeline

| Stage | When | Checks | Error range |
|---|---|---|---|
| 1. Parse | load | YAML syntax, custom tags and merge keys (`E101`), duplicate keys (`E102`), unsupported `crowley` version (`E103`), template file or ref not found (`E104`) | E1xx |
| 2. Meta-schema | load | Structure against the published template meta-schema (`crowley schema`): unknown key (`E201`), missing required key (`E202`), defaults for an unknown namespace (`E203`), no operations (`E204`), wrong type, pattern or format, including YAML values that aren't plain JSON data such as unquoted dates (`E205`) | E2xx |
| 3. Static semantic | load (no I/O) | See the list below | E3xx |
| 4. Runtime | per run | inputs, secrets, function args (after notifiers), function results (after notifiers), emitted items, `assert` | E4xx |
| 5. Output | end of run | Final value against `output.schema`, and no bytes or handles | E405 |

Stages 1–3 run once per template. The compiled template is immutable and reusable. `crowley validate` runs stages 1–3 and reports **all** errors, not just the first.

**Static checks (stage 3):**

| Code | Check |
|---|---|
| E301 | Unknown function / not in stdlib and not in `requires` |
| E302 | A required function is missing or has an incompatible version |
| E303 | Literal `with:` values violate the function's input schema. Expression-valued properties are validated only for presence. |
| E304 | `do:` on a plain function, or missing `do:` on a block function |
| E305 | `steps.<id>` is unknown or not visible from here |
| E306 | Variable is never assigned on any path |
| E307 | Duplicate step id |
| E308 | `break`/`continue` outside a loop or block-function body |
| E309 | `return` outside a block owner (e.g. in an operation's main block) |
| E310 | Both `emit` and `output.value` used |
| E311 | `emit` used but `output.schema` is not an array schema |
| E313 | Unknown expression helper |
| E314 | Wrong number of arguments to a helper |
| E315 | `inputs.<name>` not declared by the current operation, or `inputs` used inside a template function |
| E316 | Undeclared `secrets.<NAME>` |
| E317 | Recursion between template functions, or a template composition cycle |
| E318 | Literal URL host not in `permissions.hosts` |
| E319 | `output.schema` / `inputs` / function schemas are not valid JSON Schema |
| E320 | Outer variable assigned inside `for_each` with `concurrency > 1` |
| E321 | Expression syntax error |
| E322 | Lambda used outside a helper argument |
| W001 | (warning) Unreachable steps after `return`/`break`/`continue`/`fail` |
| E323 | `emit` inside a template function |
| E324 | Unknown operation in `template:<ref>#<op>`, missing `#<op>` for a multi-operation child, or a `template:` reference to the template itself |
| W002 | (warning) Step result is never used and the step has no `id` reference (pure functions only) |
| E326 | Field spec uses a query language or `attr` the extractor doesn't support |
| E327 | Literal extractor query doesn't compile (bad CSS / XPath / JSONPath / regex) |
| E328 | Unknown expression root or binding, e.g. `${{ row }}` when the loop says `as: person` |
| E329 | `on_error` on a step kind that doesn't allow it (`break`, `continue`, `return`, `set`) |
| W003 | (warning) Template function not used by any operation |

---

## 16. Errors

### 16.1 Shape

```python
class CrowleyError(Exception):
    code: str            # "E405"
    message: str
    path: str | None     # "steps[0].do[1].with.selector" or "output[14].rank"
    location: SourceLocation | None   # file, line, column
    value: Any           # offending value, redacted and truncated
    hint: str | None
    cause: BaseException | None
    trace: RunTrace | None            # set on run failures
```

### 16.2 Code ranges and catchability

| Range | Class | Catchable by `on_error` |
|---|---|---|
| E1xx | `TemplateParseError` | — (load time) |
| E2xx | `TemplateSchemaError` | — (load time) |
| E3xx | `TemplateSemanticError` | — (load time) |
| E4xx | `ValidationError` (`E401` inputs, `E402` secrets, `E403` function args, `E404` emitted item, `E405` output, `E406` assert, `E407` function result) | **Never** |
| E5xx | `ExecutionError` (`E501` expression, `E502` function raised, `E503` `fail` step, `E504` step timeout) | Yes |
| E6xx | `ExchangeError`, raised by the exchange pipeline for any adapter: `E601` unexpected status / adapter-reported failure response, `E602` connection / transport error, `E603` timeout, `E604` target not permitted, `E605` no cassette match in replay, `E606` response too large, `E607` adapter not available | Yes, **except** E604, E605 and E607 |
| E7xx | `LimitError` (`E701` limit exceeded, `E702` `while` hit `max_iterations`, `E703` expression budget or regex timeout, `E705` pagination loop) | **Never** |
| E8xx | `ControlError` (`E801` aborted by notifier, `E802` notifier raised, `E803` cancelled by caller) | **Never** |
| E9xx | `ConfigurationError` (`E901` registry conflict, `E902` bad SDK config, `E903` invalid function spec or handler signature, `E904` operation not specified or unknown, `E905` process already started, `E906` adapter or extractor name conflict / incompatible `replace`) | — |

### 16.3 `on_error`

```yaml
on_error: fail                     # default
on_error: skip                     # result = null, run continues
on_error: { default: [] }          # result = given value
on_error:
  retry: { times: 3, backoff: exponential, delay: 1s, max_delay: 20s }
  then: skip                       # after retries are exhausted: fail | skip | { default: ... }
on_error:
  catch: [E601, E603]              # optional: only these codes; others propagate
  then: { default: null }
```

- Inside the policy, expressions can read `error` (`{code, message, path, user_code}`), e.g. `default: ${{ {'failed': true, 'reason': error.code} }}`.
- Retries re-evaluate `with:` arguments.
- A `default` value is still validated against the function's output schema (`E407`).

---

## 17. Events and notifiers

Notifiers are part of the core runtime, not an add-on.

**Guarantee:** every step of every kind, at every nesting depth, fires `step.before` and then either `step.after` or `step.error`. This includes:
- `use`, `if`, `for_each`, `while`, `set`, `emit`, `return`, `break`, `continue`, `fail`, `assert`, `log`,
- steps inside loops, block-function bodies, template functions and child templates.

A step skipped by `when` fires `step.before` and then `step.skipped`. There is no code path that runs a step without these events. This is enforced structurally, because the executor wraps every step handler in the same step pipeline (ARCHITECTURE §4.3), and a conformance test covers it.

With these events, a notifier can change any step at runtime:
- **before it runs:** its incoming `prev`, its evaluated `with:` args (`kwargs`), its condition or loop items, or whether it runs at all,
- **after it runs:** its result, which is also the `prev` for the next step.

### 17.1 Event catalog

| Event | Mutable fields | Allowed actions |
|---|---|---|
| `run.start` | `inputs` | abort |
| `run.end` | `output` (revalidated) | — |
| `run.error` | — | — |
| `template.loaded` | `document` (raw dict, before compile; re-validated stages 2–3) | abort |
| `template.validated` | — (read-only compiled template) | abort |
| `inputs.resolve` | `inputs` (validated afterwards) | abort |
| `step.before` | `prev` (incoming value), `kwargs` (`use` steps: evaluated `with:` args, revalidated) | skip, replace, abort |
| `step.after` | `result`. This is also the outgoing `prev`, so changing it changes what the next step receives. Revalidated when the step is a function call. | replace, abort |
| `step.error` | — | retry, replace, abort |
| `step.skipped` | — | — |
| `function.before` | `kwargs` (args + `prev`, exactly what the handler will receive; revalidated against the `input`/`prev` schemas) | skip, replace, abort |
| `function.after` | `result` (revalidated against output schema) | replace, retry, abort |
| `function.error` | — | retry, replace, abort |
| `condition.evaluated` | `value` (MUST stay bool) | abort |
| `loop.start` | `items` (`for_each` only) | abort |
| `loop.iteration.before` | `binding` (the `as` value) | skip (= continue), abort |
| `loop.iteration.after` | `value`, `stop` (bool → break) | abort |
| `loop.end` | `result` | abort |
| `page.before` | `page` | skip, abort |
| `page.after` | `value`, `stop` (bool) | abort |
| `exchange.before` | `exchange.target`, `exchange.request` (adapter-specific; for `http`: method, url, query, headers, cookies, body, timeout, proxy, verify_tls). Permission re-checked afterwards. | skip/replace (synthetic response), abort |
| `exchange.after` | `result.response` (adapter-specific; for `http`: status, headers, body) | replace, retry, abort |
| `exchange.error` | — | retry, replace (synthetic response), abort |
| `exchange.retry` | `delay` | abort |
| `selector.fallback` | — (payload: `extractor`, `field`, `index`, `query`) | abort |
| `variable.set` | `value` | abort |
| `item.emit` | `item` (validated after notifiers) | skip (drop item), abort |
| `output.before_validate` | `output` | abort |
| `output.validated` | — | — |
| `log` | — | — |
| `expression.evaluated` | `value` | abort — **opt-in** via `Crowley(trace_expressions=True)` |

Plugins MAY emit custom events through `ctx.emit_event`, if they are declared in their `FunctionSpec.events` and namespaced (`mycorp.token.refresh`). `page.*` is shared by all paginate-like functions.

### 17.2 Common event payload

```
name, timestamp, run (id, template_id, template_version, operation), step (id, kind, path, function?),
adapter (exchange.* events), extractor (extraction events),
iteration (index or None), page (or None), depth, scope (read-only view of inputs/vars/steps)
```

### 17.3 Notifiers, registries and processes

A **notifier** is a callable attached to an event name. It can watch an event or change it (actions and mutable fields are listed in §17.1).

One `Crowley` instance can run many operations at the same time, often of different templates, and each run usually needs its own behaviour (its own proxy, its own progress reporting, its own fixes). Notifiers can therefore be attached at **three scopes**:

| Scope | Attached with | Applies to |
|---|---|---|
| **Global** | `cw.add_notifier(...)`, `@cw.on(...)`, `cw.add_notifier_registry(...)` | Every process created by this `Crowley` instance. Global notifiers **always apply**; a process cannot opt out. |
| **Registry** | `NotifierRegistry`, a reusable named bundle of notifiers | Every scope it is added to (global or process) |
| **Process** | `process.add_notifier(...)`, `@process.on(...)`, `process.add_notifier_registry(...)` | That one process only. This covers everything that runs inside it, including template functions, block bodies and child templates. |

#### Processes

`cw.init(...)` creates a **process**: one execution of one operation, with its own notifiers, state, cancellation and trace. Creating it doesn't run anything.

```python
cw = Crowley()

# ── global: applies to every process ───────────────────────────────────────────
@cw.on("exchange.before", adapter="http")
def user_agent(event):
    event.exchange.request["headers"]["User-Agent"] = "acme-bot/1.0"

# ── a reusable registry ────────────────────────────────────────────────────────
rotating_proxies = NotifierRegistry("rotating-proxies")

@rotating_proxies.on("exchange.before", adapter="http", priority=10)
def add_proxy(event):
    event.exchange.request["proxy"] = pool.next()

rotating_proxies.add_notifier("exchange.after", backoff_on_429, adapter="http")

# ── two processes running at the same time, each with its own notifiers ────────
people = cw.init(tpl, "get_page_people", inputs={"company": "acme"})
people.add_notifier_registry(rotating_proxies)
people.add_notifier("item.emit", lambda e: progress.tick())
people.add_notifier("step.after", drop_bots, step="normalize")

info = cw.init(tpl, "get_page_info", inputs={"company": "acme"})

@info.on("function.after", function="local.fetch_json")
def patch_info(event):
    event.result.setdefault("website", None)

people_result, info_result = await asyncio.gather(people.run(), info.run())
```

#### Signature

```python
add_notifier(
    event: str,                     # event name or glob: "step.after", "exchange.*", "*"
    fn: Callable[[Event], Action | None | Awaitable[Action | None]],
    *,
    step: str | None = None,        # step id glob
    function: str | None = None,    # function name glob ("http.*", "local.fetch_json")
    operation: str | None = None,   # operation name glob
    template: str | None = None,    # template id glob
    adapter: str | None = None,     # adapter name glob (exchange.* events)
    extractor: str | None = None,   # extractor name glob (selector.fallback, extractor function events)
    priority: int = 0,
    observe: bool = False,          # True → read-only observer (frozen copy, runs after interceptors)
    revalidate: bool = True,
    name: str | None = None,        # optional label, shown in traces and errors
) -> NotifierHandle
```

The same signature is available on `Crowley`, `NotifierRegistry` and `Process`. `on(event, **filters)` is the decorator form. `add_notifier_registry(registry) -> NotifierHandle` attaches a whole registry. `remove_notifier(handle)` removes a single notifier or a whole registry.

#### Registries

- `NotifierRegistry(name)` holds notifiers and nested registries: `registry.include(other_registry)`.
- A registry can be attached to any number of processes, and globally.
- Registries are attached **by reference**. A notifier added to a registry later also applies to the processes it is attached to, starting from the next event.
- The same registry attached twice to one scope is only counted once.

#### Dispatch and order

For each event, the process's event bus collects matching notifiers from:
1. global notifiers, including global registries,
2. the process's registries, in the order they were attached,
3. the process's own notifiers.

Matching notifiers are sorted by `priority` (highest first). Ties keep the scope order above, then registration order. So with equal priority, global runs first and the process's own notifiers run last. Observers run after all interceptors.

- A notifier is selected only if **all** of its filters match.
- Notifiers can be added or removed while a process is running. The change applies from the next event on.

#### Process lifecycle

| Member | Description |
|---|---|
| `cw.init(template, operation=None, *, inputs=None, secrets=None, limits=None, trace="summary")` | Resolves the template, selects the operation (`E904`) and **validates inputs and secrets immediately** (`E401`/`E402`). Returns a `Process` in state `created`. |
| `process.id`, `.template`, `.operation`, `.inputs`, `.status` | `status`: `created` → `running` → `succeeded` / `failed` / `cancelled` |
| `await process.run()` | Runs to completion and returns a `RunResult` |
| `process.stream()` | Async iterator of emitted items (emit-mode operations) |
| `process.start()` / `await process.result()` | Starts in the background, then awaits the result |
| `process.cancel()` | Cancels the run (`E803`) |
| `process.run_sync()` | Sync wrapper |

- A process runs **once**. Calling `run`, `stream` or `start` again causes `E905`. Create a new process with `cw.init` instead.
- Processes are independent: separate event bus, scopes, limits counters, cookie session, trace and cancellation token. Many processes can run concurrently on one `Crowley` instance.
- The shared parts (compiled templates, the function registry, the HTTP connection pool and global notifiers) are safe to use concurrently.

#### Actions

Actions are returned from the notifier:
- `event.skip(result=None)`
- `event.replace(value)`
- `event.retry(after=None)`
- `event.abort(reason)`, which fails the run with `E801`.

The first action returned wins, and later interceptors for that event don't run. Returning `None` passes the event to the next notifier.

#### Revalidation

Changes are revalidated as listed in §17.1. `revalidate=False` turns off schema revalidation for changes made by that notifier. It **never** turns off:
- permission checks,
- limits,
- the bool type of `condition.evaluated`,
- emitted-item validation,
- final output validation.

#### Failures

- An exception in an interceptor fails the run with `E802`. The error names the notifier (`name` or the qualified function name) and its scope.
- An exception in an observer is reported as a `log` event at level `error`, unless `Crowley(strict_observers=True)`.

Templates can't declare, change or disable notifiers.

---

## 18. Concurrency

- `for_each.concurrency` and `control.parallel` run iterations as asyncio tasks, bounded by both the local setting and `limits.max_concurrency`.
- `steps.<id>` results are kept in input order. Emitted items arrive in completion order.
- If an iteration fails with an uncaught error, the other in-flight iterations are cancelled and the error propagates. If several fail at the same time, the first is raised and the rest are attached to `error.related`.
- Shared state: only `emit`, and `set` on iteration-local variables, are allowed inside concurrent bodies (`E320`).

---

## 19. Template tests

Tests are declared per operation (`operations.<op>.tests`).

```yaml
operations:
  get_page_people:
    # ...
    tests:
      - name: two pages
        inputs: { company: acme, max_pages: 2 }
        secrets: { API_TOKEN: test-token }
        fixtures: fixtures/people-acme.cassette.json   # path relative to the template file
        match: [method, url]                 # default; may add "body"
        expect:
          min_items: 1
          max_items: 100
          snapshot: snapshots/people-acme.json   # exact match; `crowley test --update` rewrites
          assert:
            - ${{ all(output, p => len(p.name) > 0) }}
            - ${{ len(unique(output[*].id)) == len(output) }}
      - name: unknown company fails cleanly
        inputs: { company: nope }
        fixtures: fixtures/people-404.cassette.json
        expect:
          error: E601
```

- `crowley test` runs every test of every operation. `--operation <op>` narrows it.

- Tests run in **replay** mode: every adapter's exchanges are answered from the cassette (§13.6). An exchange with no match fails with `E605`. The network is never used.
- For `http`, `fixtures:` may also point to a `.har` file, which is imported on the fly.
- `now()` and `uuid()` are frozen during tests (`2026-01-01T00:00:00Z`, and a seeded generator).
- `crowley record <template> <operation> --input ... --out fixtures/x.cassette.json` runs live and records every exchange. Secrets and adapter-declared sensitive fields (for `http`: `Authorization`, `Cookie`) are scrubbed. `--har` additionally exports the http exchanges as HAR.

---

## 20. Python SDK API

```python
from crowley import Crowley, Limits, DirectorySource, EnvSecrets
from crowley.adapters.http import HttpAdapter

cw = Crowley(
    sources=[DirectorySource("./templates")],     # resolves template:<ref> and string refs
    functions=[slugify],
    plugins=[MyCorpPlugin()],
    helpers={"slug": my_pure_helper},
    secrets=EnvSecrets(prefix="CROWLEY_"),        # or a dict
    limits=Limits(max_requests=1_000),
    adapters=[HttpAdapter(user_agent="acme-bot/1.0", allow_private_networks=False), WsAdapter()],
    extractors=[PdfExtractor()],                  # built-ins (html, xml, json, text) are always present
    load_entry_points=False,
)

cw.register_adapter(CurlCffiHttpAdapter(), replace="http")   # swap a built-in (§13.2)
cw.register_extractor(SelectolaxHtml(), replace="html")

tpl = cw.load("company-directory.yml")  # → Template (compiled, stages 1–3); raises on error
report = cw.validate("company-directory.yml")   # → ValidationReport(errors, warnings); never raises

tpl.operations                          # → {"get_page_info": Operation, "get_page_people": Operation}
tpl.operations["get_page_people"].inputs_schema / .output_schema / .description
plan = cw.explain(tpl, "get_page_people")       # → human-readable execution plan

# ── global notifiers (apply to every process) ──
cw.add_notifier("exchange.before", add_proxy, adapter="http")
cw.add_notifier_registry(audit_registry)

# ── processes: one operation run each, with their own notifiers (§17.3) ──
process = cw.init(tpl, "get_page_people", inputs={"company": "acme"})   # validates inputs now
process.add_notifier("item.emit", on_item)
process.add_notifier_registry(rotating_proxies)
result = await process.run()                          # → RunResult
result.output; result.items; result.stats; result.trace; result.run_id; result.operation

async for person in cw.init(tpl, "get_page_people", inputs=...).stream():   # emit-mode operations
    ...

p = cw.init(tpl, "get_page_people", inputs=...); p.start(); ...; p.cancel()   # background (E803)

# ── shortcuts: init + run with no process-level notifiers ──
result = await cw.run(tpl, "get_page_info", inputs={"company": "acme"})
result = cw.run_sync(tpl, "get_page_info", inputs={"company": "acme"})
result = await cw.run("examples/company-directory@^1#get_page_info", inputs=...)   # ref form
async for item in cw.stream(tpl, "get_page_people", inputs=...): ...
```

- `cw.run`, `cw.run_sync` and `cw.stream` are shortcuts for `cw.init(...)` followed by `.run()`/`.run_sync()`/`.stream()`. They accept `notifiers=` (a list of `Notifier` objects or a `NotifierRegistry`), which are attached at process scope.
- The operation argument may be omitted only when the template has exactly one operation. Otherwise, or for an unknown name, `E904` is raised.
- `RunResult.stats` contains `requests`, `bytes_in`, `items`, `duration_ms`, `retries` and `errors_caught`.
- `RunResult.trace` is a list of serialized events with secrets redacted. Detail is controlled by `trace="off" | "summary" | "full"` (default `"summary"`).

---

## 21. CLI

```
crowley run <template> <operation> [--input k=v]... [--inputs-file f.json] [--secret K=V]... [--secrets-env PREFIX]
                       [--out out.json|out.jsonl] [--format json|jsonl] [--trace trace.json] [--limit key=value]...
crowley validate <template>... [--format text|json]          # exit 1 on errors
crowley explain <template> [<operation>]
crowley operations <template>                                # list operations with inputs/outputs
crowley test <template|dir>... [--operation op] [--update]
crowley record <template> <operation> --out fixtures/x.cassette.json [--har] [--input k=v]...
crowley functions list [--namespace ns]
crowley functions show <name>
crowley schema [--out crowley.schema.json]                   # template meta-schema for editors
crowley init <dir>                                           # scaffold template with one operation + fixtures + test
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Template invalid (E1–E3) |
| 2 | Validation failure (E4) |
| 3 | Execution or HTTP failure (E5–E6) |
| 4 | Limit, control or configuration failure (E7–E9) |
| 5 | Tests failed |

---

## 22. Versioning and compatibility

- `crowley: 1` is frozen once SDK 1.0 ships.
- Additive changes, such as new helpers, stdlib functions, optional keys or events, are allowed in minor releases.
- Breaking changes require `crowley: 2`. The SDK then supports both versions for at least one major release.
- Stdlib functions are versioned individually. A template can pin one with `requires: ["http.request@^1"]`; this is optional for stdlib.
- `x-*` keys are reserved for extensions and never interpreted by the SDK.
