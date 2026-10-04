# Crowley

Crowley is a Python SDK that runs declarative scraping templates written in YAML. A template describes one site or API as a set of named **operations** (for example `get_page_info` and `get_page_people`). Each operation is built from a registry of **functions**, such as `http.get`, `html.extract` and `paginate.by_cursor`, combined with conditions and loops. All I/O goes through pluggable **adapters** (HTTP is built in), and all parsing goes through pluggable **extractors** (HTML, XML, JSON and text are built in). You can register your own. Every operation's output is checked against a schema, and invalid data stops the run. You can observe and change every step at runtime through **notifiers**.

> **Status:** pre-alpha. Templates load, compile and are statically validated (M1). Operations run with notifiers, `prev` piping, template functions and your own Python functions (M2). The `http` adapter, the `html`/`xml`/`json`/`text` extractors and the `paginate.*`/`transform.*`/`control.*`/`extract.*` stdlib are built in (M3). Template composition (M4) and cassette-based tests (M5) come next.

## Validate a template

```bash
uv run crowley validate examples/company-directory/company-directory.yml
uv run crowley schema --out crowley.schema.json   # meta-schema for editor autocompletion
```

```python
from crowley import Crowley

report = Crowley().validate("examples/company-directory/company-directory.yml")
for diagnostic in report.diagnostics:
    # E305 at operations.op.steps[1].with.url (site.yml:14:9): unknown step 'rows'
    print(diagnostic)
```

## Run an operation

From the command line:

```bash
uv run crowley run examples/company-directory/company-directory.yml get_page_people --input company=acme --secret API_TOKEN=... --format jsonl
```

From Python, with your own functions next to the built-in ones:

```python
import asyncio

from crowley import Crowley, FunctionContext, function

TEMPLATE = """
crowley: 1
id: acme/greetings
version: 1.0.0
name: Greetings
description: Greets people.
permissions: { hosts: [api.example.com] }
requires: ["acme.*@^1"]
operations:
  greet:
    description: One greeting per name.
    inputs:
      names: { type: array, items: { type: string } }
    steps:
      - for_each: ${{ inputs.names }}
        as: name
        do:
          - use: acme.shout
            with: { text: "${{ 'hello ' + name }}" }
          - emit: ${{ prev }}
    output:
      schema: { type: array, items: { type: string } }
"""


@function(
    name="acme.shout",
    input={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
    output={"type": "string"},
)
async def shout(ctx: FunctionContext, text: str) -> str:
    return text.upper()


async def main() -> None:
    cw = Crowley(functions=[shout])
    template = cw.load_text(TEMPLATE)

    process = cw.init(template, "greet", inputs={"names": ["ada", "alan"]})
    process.add_notifier("item.emit", lambda event: print("emitted", event.item))
    result = await process.run()
    print(result.output)  # ['HELLO ADA', 'HELLO ALAN']


asyncio.run(main())
```

Every step fires `step.before` and `step.after` (and every function call fires `function.before` and `function.after`). Notifiers can change a step's incoming `prev`, its arguments and its result, or skip, replace, retry and abort it. Attach notifiers globally (`cw.add_notifier`), to a reusable `NotifierRegistry`, or to one process.

## Documentation

- [Product requirements](docs/PRD.md)
- [Template & runtime specification](docs/SPEC.md)
- [Architecture (onion layers)](docs/ARCHITECTURE.md)

## Development

Requires [uv](https://docs.astral.sh/uv/). uv installs the pinned Python (3.11) automatically.

```bash
uv sync                      # create .venv and install runtime + dev dependencies
uv run pytest                # tests
uv run ruff check            # lint
uv run ruff format           # format
uv run mypy                  # type check (strict)
uv run lint-imports          # enforce onion layer rules
uv run crowley --version     # CLI smoke test
```

Layer rules (see [ARCHITECTURE.md §1.1](docs/ARCHITECTURE.md)):
- `domain` uses only the Python standard library.
- `application` depends only on `domain`.
- `adapters` (I/O, e.g. `http`), `extractors` (parsing, e.g. `html`, `json`), `stdlib` and `infrastructure` depend inward and never on each other.
- `interface` is the only layer that wires everything together.

`lint-imports` enforces these rules in CI.

## License

[MIT](LICENSE)
