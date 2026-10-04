"""Command-line interface (docs/SPEC.md §21). ``test``/``record``/``explain`` come in M5."""

import json
import sys
from pathlib import Path
from typing import Any

import click

from crowley import __version__
from crowley.domain.common import ByteSize, Duration, Rate
from crowley.domain.errors import CrowleyError, Diagnostic, ValidationReport
from crowley.domain.template import Limits
from crowley.domain.template.meta_schema import META_SCHEMA

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_VALIDATION = 2
EXIT_EXECUTION = 3
EXIT_CONFIG = 4

_LIMIT_PARSERS: dict[str, Any] = {
    "max_requests": int,
    "max_items": int,
    "max_depth": int,
    "max_loop_iterations": int,
    "max_concurrency": int,
    "max_retries_per_step": int,
    "max_duration": Duration.parse,
    "max_response_bytes": ByteSize.parse,
    "rate_per_host": Rate.parse,
}


def exit_code_for(error: CrowleyError) -> int:
    """SPEC §21: 1 template, 2 validation, 3 execution/exchange, 4 limit/control/config."""
    family = error.code[1]
    if family in "123":
        return EXIT_INVALID
    if family == "4":
        return EXIT_VALIDATION
    if family in "56":
        return EXIT_EXECUTION
    return EXIT_CONFIG


def _printable(text: str) -> str:
    """Fall back to ASCII punctuation when the console cannot encode it."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return text.replace("—", "-").replace("→", "->").replace("…", "...")
    return text


def _safe_output() -> None:
    """Never crash on consoles that cannot print every character (e.g. cp1252 pipes)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="crowley")
def main() -> None:
    """Crowley: run declarative scraping templates."""
    _safe_output()


def _diagnostic_json(d: Diagnostic) -> dict[str, object]:
    location = d.location
    return {
        "code": d.code,
        "message": d.message,
        "path": d.path,
        "file": location.file if location else None,
        "line": location.line if location else None,
        "column": location.column if location else None,
        "hint": d.hint,
    }


def _exit_code(reports: list[ValidationReport]) -> int:
    codes = {d.code for r in reports for d in r.errors}
    if any(code == "E104" or code.startswith("E9") for code in codes):
        return EXIT_CONFIG
    return EXIT_INVALID if codes else EXIT_OK


@main.command()
@click.argument("templates", nargs=-1, required=True)
@click.option(
    "--format", "fmt", type=click.Choice(["text", "json"]), default="text", show_default=True
)
def validate(templates: tuple[str, ...], fmt: str) -> None:
    """Check templates without running them (parse, schema and static checks)."""
    from crowley.interface.sdk import Crowley

    crowley = Crowley()
    results = [(ref, crowley.validate(ref)) for ref in templates]
    if fmt == "json":
        payload = [
            {
                "template": ref,
                "ok": report.ok,
                "errors": [_diagnostic_json(d) for d in report.errors],
                "warnings": [_diagnostic_json(d) for d in report.warnings],
            }
            for ref, report in results
        ]
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        errors = warnings = 0
        for ref, report in results:
            click.echo(f"{'OK  ' if report.ok else 'FAIL'} {ref}")
            for d in report.diagnostics:
                click.echo(_printable(f"  {d}"))
            errors += len(report.errors)
            warnings += len(report.warnings)
        click.echo(f"{len(results)} template(s), {errors} error(s), {warnings} warning(s)")
    sys.exit(_exit_code([report for _, report in results]))


@main.command()
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), help="Write to a file.")
def schema(out: Path | None) -> None:
    """Print the template meta-schema (JSON Schema) for editors and tools."""
    text = json.dumps(META_SCHEMA, indent=2, ensure_ascii=False) + "\n"
    if out is None:
        click.echo(text, nl=False)
    else:
        out.write_text(text, encoding="utf-8", newline="\n")
        click.echo(f"wrote {out}")


def _pairs(values: tuple[str, ...], what: str) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        key, sep, rest = value.partition("=")
        if not sep or not key:
            raise click.BadParameter(f"expected KEY=VALUE, got {value!r}", param_hint=what)
        pairs.append((key, rest))
    return pairs


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _limits(values: tuple[str, ...]) -> Limits | None:
    if not values:
        return None
    parsed: dict[str, Any] = {}
    for key, value in _pairs(values, "--limit"):
        parser = _LIMIT_PARSERS.get(key)
        if parser is None:
            known = ", ".join(sorted(_LIMIT_PARSERS))
            raise click.BadParameter(
                f"unknown limit {key!r} (known: {known})", param_hint="--limit"
            )
        try:
            parsed[key] = parser(value)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--limit") from None
    return Limits(**parsed)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@main.command()
@click.argument("template")
@click.argument("operation", required=False)
@click.option("--input", "inputs", multiple=True, help="KEY=VALUE; VALUE is JSON if it parses.")
@click.option(
    "--inputs-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A JSON object of inputs.",
)  # fmt: skip
@click.option("--secret", "secrets", multiple=True, help="KEY=VALUE secret.")
@click.option("--secrets-env", help="Read secrets from environment variables with this prefix.")
@click.option("--limit", "limits", multiple=True, help="Stricter limit, e.g. max_requests=50.")
@click.option(
    "--format", "fmt", type=click.Choice(["json", "jsonl"]), default="json", show_default=True,
    help="json: the whole output; jsonl: one emitted item (or the output) per line.",
)  # fmt: skip
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), help="Write to a file.")
def run(
    template: str,
    operation: str | None,
    inputs: tuple[str, ...],
    inputs_file: Path | None,
    secrets: tuple[str, ...],
    secrets_env: str | None,
    limits: tuple[str, ...],
    fmt: str,
    out: Path | None,
) -> None:
    """Run one operation of a template and print its output."""
    from crowley.infrastructure.secrets import EnvSecrets
    from crowley.interface.sdk import Crowley

    values: dict[str, Any] = {}
    if inputs_file is not None:
        loaded = json.loads(inputs_file.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise click.BadParameter("must contain a JSON object", param_hint="--inputs-file")
        values.update(loaded)
    values.update({k: _json_or_text(v) for k, v in _pairs(inputs, "--input")})
    given_secrets = dict(_pairs(secrets, "--secret"))
    crowley = Crowley(
        secrets=EnvSecrets(prefix=secrets_env) if secrets_env is not None else None,
    )
    try:
        result = crowley.run_sync(
            template,
            operation,
            inputs=values,
            secrets=given_secrets,
            limits=_limits(limits),
        )
    except CrowleyError as exc:
        click.echo(_printable(str(exc)), err=True)
        for related in exc.related:
            click.echo(_printable(f"  {related}"), err=True)
        sys.exit(exit_code_for(exc))
    if fmt == "jsonl":
        rows = result.items if result.items else [result.output]
        text = "".join(_dump(row) + "\n" for row in rows)
    else:
        text = json.dumps(result.output, indent=2, ensure_ascii=False, default=str) + "\n"
    if out is None:
        click.echo(text, nl=False)
    else:
        out.write_text(text, encoding="utf-8", newline="\n")
        stats = result.stats
        click.echo(
            f"wrote {out} ({stats['items']} item(s), {stats['requests']} request(s), "
            f"{stats['duration_ms']} ms)",
            err=True,
        )
    sys.exit(EXIT_OK)
