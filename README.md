# Crowley

**Declarative web scraping for Python.** Describe a site or API once, as a YAML template of named operations, and Crowley runs it: HTTP, pagination, HTML/XML/JSON/text extraction, validation and retries included. Every step can be observed and changed at runtime through notifiers.

```yaml
# examples/books/books.yml (abridged: the full file also cleans up price and rating)
operations:
  list_books:
    description: Books from the first pages of the catalogue.
    inputs:
      max_pages: { type: integer, default: 2 }
    steps:
      - use: paginate.by_page
        with: { max_pages: "${{ inputs.max_pages }}", flatten: true }
        do:
          - use: http.get
            with:
              url: ${{ 'https://books.toscrape.com/catalogue/page-' + str(page.number) + '.html' }}
          - use: html.extract
            with:
              root: article.product_pod
              fields:
                title: { selector: "h3 a", attr: title }
                price: { selector: .price_color }
                url:   { selector: "h3 a", attr: href }
      - for_each: ${{ prev }}
        as: book
        do:
          - emit: ${{ book }}
    output:
      schema: { type: array, items: { type: object, required: [title, price, url] } }
```

```bash
crowley run examples/books/books.yml list_books --input max_pages=2 --format jsonl
```

```json
{"title": "A Light in the Attic", "price": 51.77, "rating": "Three", "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"}
{"title": "Tipping the Velvet", "price": 53.74, "rating": "One", "url": "https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html"}
```

> **Status: 0.1.0, alpha.** The template language, the runtime, the `http` adapter, the four extractors and the stdlib are complete and tested. Template composition, concurrent `for_each`, recorded test fixtures and browser rendering come in later releases (see [Roadmap](#roadmap)). The template format (`crowley: 1`) may still change before 1.0.

## Why Crowley

- **Templates, not scripts.** A template describes *what* to fetch and extract. Crowley handles the *how*. One template per site, split into operations such as `get_page_info` and `get_page_people`.
- **Function-based.** Crowley has no built-in idea of "pagination" or "login". Everything is a function: `http.get`, `paginate.by_cursor`, `html.extract`, `transform.dedupe` or your own. Functions can be combined, replaced and extended.
- **Strict by default.** Inputs, function arguments, results, every emitted item and the final output are validated against JSON Schema. Invalid data stops the run with a precise error.
- **Caught before it runs.** `crowley validate` reports every mistake, such as unknown functions, hosts that aren't permitted, broken CSS/XPath/JSONPath/regex, unknown steps or variables, and recursion, with file, line, column and a "did you mean" hint. It does this without making a single request.
- **Notifiers everywhere.** Every step, function call, page, request and emitted item fires events. Notifiers can watch them, change data, skip, retry, replace or abort, globally or per run.
- **Safe by default.** Requests only go to hosts listed in the template. Private and loopback addresses are blocked (SSRF guard), redirects are checked hop by hop, and limits cap requests, items, duration and response size. Secrets never appear in errors or event payloads.
- **Pluggable I/O and parsing.** HTTP is just one adapter, and HTML is just one extractor. Add WebSocket, GraphQL, CSV or PDF support without touching the core.

## Install

```bash
pip install git+https://github.com/mohamed-naser-awd/crowley@v0.1.0
```

Requires Python 3.11 or newer.

## Quick start

### 1. Validate

```bash
crowley validate examples/books/books.yml
crowley schema --out crowley.schema.json      # JSON Schema for editor autocompletion
```

A typo is reported before anything runs:

```
FAIL books.yml
  E313 at operations.list_books.steps[1].do[0].emit.price (books.yml:43:23): unknown helper 'to_number' — did you mean 'parse_number'?
```

### 2. Run from the command line

```bash
crowley run <template> [operation] --input key=value --secret NAME=value --format json|jsonl --out file
```

| Option | |
|---|---|
| `--input k=v` | An input. The value is parsed as JSON if possible (`--input 'names=["a","b"]'`). |
| `--inputs-file f.json` | Inputs from a JSON object |
| `--secret K=V`, `--secrets-env PREFIX` | Secrets from the command line or from `PREFIX<NAME>` environment variables |
| `--limit key=value` | Stricter limits, e.g. `max_requests=50`, `max_duration=2m` |
| `--format json\|jsonl` | The whole output, or one emitted item per line |

Exit codes: `0` ok, `1` invalid template, `2` validation failure, `3` execution or HTTP failure, `4` limit, control or configuration failure.

### 3. Run from Python

```python
import asyncio

from crowley import Crowley, NotifierRegistry


async def main() -> None:
    cw = Crowley()

    # global notifier: applies to every run and every http request
    @cw.on("exchange.before", adapter="http")
    def user_agent(event):
        event.exchange.request.setdefault("headers", {})["User-Agent"] = "my-bot/1.0"

    # a reusable bundle of notifiers
    progress = NotifierRegistry("progress")
    progress.add_notifier("page.after", lambda e: print("page", e.page["number"], "done"))

    # a process is one run of one operation, with its own notifiers
    process = cw.init("examples/books/books.yml", "list_books", inputs={"max_pages": 2})
    process.add_notifier_registry(progress)
    process.add_notifier("item.emit", lambda e: e.skip() if e.item["rating"] == "One" else None)

    result = await process.run()
    print(len(result.items), "books;", result.stats["requests"], "requests")


asyncio.run(main())
```

You can also stream items as they arrive (`async for item in process.stream()`), run in the background (`process.start()`, `await process.result()`, `process.cancel()`), run synchronously (`cw.run_sync(...)`), and run many processes concurrently on one `Crowley` instance.

## Templates in brief

A template has metadata, `permissions`, optional `secrets`, `defaults`, `limits`, shared `schemas`, reusable `functions`, and one or more `operations`. Each operation has typed `inputs`, `steps` and an `output` schema.

| Step | Purpose |
|---|---|
| `use` | Call a function: `http.get`, `html.extract`, `local.my_function`, … |
| `if` / `for_each` / `while` | Conditions and loops |
| `set` | Assign variables |
| `emit` | Produce an output item (validated immediately) |
| `return`, `break`, `continue`, `fail`, `assert`, `log` | Control flow and checks |

- **Piping.** Each step's output is the next step's `prev`, and functions receive it as a keyword argument.
- **Expressions.** `${{ ... }}` is a small, safe expression language with about 70 helpers (`len`, `trim`, `replace`, `parse_number`, `parse_date`, `unique`, `flatten`, …).
- **Template functions.** Groups of steps declared under `functions:` and called as `use: local.<name>`. Use them to share logic between operations.
- **Error handling.** `on_error` can `skip`, use a `default`, `retry` with backoff, or `catch` specific error codes.

The full language is in [docs/SPEC.md](docs/SPEC.md). A complete multi-operation example is in [examples/company-directory](examples/company-directory/company-directory.yml).

## Built in

| Kind | Name | What it does |
|---|---|---|
| Adapter | `http` | httpx, HTTP/1.1 and HTTP/2. JSON/form/raw/multipart bodies, auth, cookies per run, isolated `http.session`, redirects checked per hop, `expect_status`, retries with `Retry-After` |
| Extractor | `html` | lxml + cssselect: CSS and XPath; `text`, `html`, `inner_html` or any attribute; links resolved to absolute URLs |
| Extractor | `xml` | XPath and CSS, namespaces, safe against XXE |
| Extractor | `json` | JSONPath; works directly on parsed response bodies |
| Extractor | `text` | Regular expressions with named and numbered groups |
| Functions | `paginate.*` | `by_page`, `by_offset`, `by_cursor`, `by_next_link`, with loop detection |
| Functions | `transform.*` | `map`, `filter`, `dedupe`, `sort`, `group_by`, `flatten` |
| Functions | `control.*` | `retry`, `sleep`, `parallel` |
| Functions | `extract.auto` | Picks the extractor from the response's media type |

Every extractor provides `<name>.parse`, `.select`, `.select_all` and `.extract`. Extraction supports fallback selectors (`selector: [".new", ".old"]`), `required`, `default`, `all` and nested fields.

## Extending Crowley

```python
from crowley import Crowley, FunctionContext, function


@function(
    name="acme.slugify",
    input={"type": "object", "properties": {"text": {"type": "string"}}},
    output={"type": "string"},
)
async def slugify(ctx: FunctionContext, text: str | None = None, prev=None) -> str:
    return (text or prev).lower().replace(" ", "-")


cw = Crowley(functions=[slugify])  # templates declare it with requires: ["acme.*@^1"]
```

- **Functions** receive `handler(ctx, **kwargs)`: the validated `with:` arguments plus `prev`. A handler takes only the arguments it names. Through `ctx` they can call `ctx.exchange(...)` (all I/O goes through adapters), `ctx.extractor(...)`, `ctx.evaluate(...)` for lazy arguments, `ctx.body.run(...)` for block functions, and `ctx.emit_event(...)`.
- **Adapters** (new protocols): subclass `BaseAdapter` and implement `send`. Permissions, limits, rate limits, retries and notifiers come from the shared pipeline.
- **Extractors** (new formats): subclass `BaseExtractor` and implement `parse`, `select` and `read`. The field engine, the generated functions and `extract.auto` routing come for free.
- **Swap a built-in:** `cw.register_adapter(MyHttp(), replace="http")`. To swap one only for a single run, use `cw.init(..., adapters={"http": MyHttp()})`.

## Roadmap

| Version | Planned |
|---|---|
| 0.2 | Template composition (`use: template:<ref>#<operation>`), concurrent `for_each`, a directory of templates |
| 0.3 | Record and replay (cassettes, HAR), `tests:` in templates, `crowley test` / `record` / `explain`, run traces |
| Later | Browser rendering adapter (Playwright), template hub |

## Documentation

- [Template & runtime specification](docs/SPEC.md): the template language, functions, adapters, extractors, events and errors
- [Product requirements](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md): onion layers and extension points
- [Changelog](CHANGELOG.md)

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # create .venv with runtime + dev dependencies
uv run pytest                # tests
uv run ruff check            # lint
uv run ruff format           # format
uv run mypy                  # type check (strict)
uv run lint-imports          # enforce the onion layer rules
```

Layer rules (see [ARCHITECTURE.md §1.1](docs/ARCHITECTURE.md)):
- `domain` uses only the Python standard library.
- `application` depends only on `domain`.
- `adapters`, `extractors`, `stdlib` and `infrastructure` depend inward and never on each other.
- `interface` is the only layer that wires everything together.

## License

[MIT](LICENSE)
