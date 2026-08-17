"""`audit` CLI."""

import click

from .. import __version__


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="audit")
def audit() -> None:
    """Audit the validity of an Inspect eval."""


def main() -> None:
    audit()


if __name__ == "__main__":
    main()
