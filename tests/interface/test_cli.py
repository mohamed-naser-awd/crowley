from click.testing import CliRunner

from crowley import __version__
from crowley.interface.cli import main


def test_version_option() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"crowley, version {__version__}"
