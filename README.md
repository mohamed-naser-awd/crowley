# Crowley

Crowley is a Python SDK that runs declarative scraping templates written in YAML. A template describes one site or API as a set of named **operations** (for example `get_page_info` and `get_page_people`). Each operation is built from a registry of **functions**, such as `http.get`, `html.extract` and `paginate.by_cursor`, combined with conditions and loops. All I/O goes through pluggable **adapters** (HTTP is built in), and all parsing goes through pluggable **extractors** (HTML, XML, JSON and text are built in). You can register your own. Every operation's output is checked against a schema, and invalid data stops the run. You can observe and change every step at runtime through **notifiers**.

> **Status:** pre-alpha. The template language is in place (milestone M1): templates load, compile and are statically validated. Running operations arrives with the runtime (M2).

## Validate a template

```bash
uv run crowley validate examples/company-directory/company-directory.yml
uv run crowley schema --out crowley.schema.json   # meta-schema for editor autocompletion
```

```python
from crowley import Crowley

report = Crowley().validate("examples/company-directory/company-directory.yml")
for diagnostic in report.diagnostics:
    print(diagnostic)   # E305 at operations.op.steps[1].with.url (site.yml:14:9): unknown step 'rows'
```

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
