# Crowley

Crowley is a Python SDK that runs declarative scraping templates written in YAML. A template describes one site or API as a set of named **operations** (for example `get_page_info` and `get_page_people`). Each operation is built from a registry of **functions**, such as `http.get`, `html.extract` and `paginate.by_cursor`, combined with conditions and loops. Every operation's output is checked against a schema, and invalid data stops the run. You can observe and change every step at runtime through **notifiers**.

> **Status:** pre-alpha. Milestone M0 (project skeleton) is in place, and nothing is usable yet.

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
- `stdlib` and `infrastructure` depend inward and never on each other.
- `interface` is the only layer that wires everything together.

`lint-imports` enforces these rules in CI.

## License

[MIT](LICENSE)
