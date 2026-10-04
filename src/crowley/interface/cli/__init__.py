"""Command-line interface (docs/SPEC.md §21). Subcommands are added in later milestones."""

import click

from crowley import __version__


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="crowley")
def main() -> None:
    """Crowley: run declarative scraping templates."""
