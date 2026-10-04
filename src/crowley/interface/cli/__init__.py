"""Command-line interface (docs/SPEC.md §21). ``run``/``test``/``record`` come with the runtime."""

import json
import sys
from pathlib import Path

import click

from crowley import __version__
from crowley.domain.errors import Diagnostic, ValidationReport
from crowley.domain.template.meta_schema import META_SCHEMA

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_CONFIG = 4


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
