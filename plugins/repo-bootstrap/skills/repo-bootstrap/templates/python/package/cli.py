from __future__ import annotations

import click
from loguru import logger


@click.group()
@click.version_option(package_name="{{DIST_NAME}}")
def main() -> None:
    """{{DESCRIPTION}}"""


@main.command()
def hello() -> None:
    """TODO(bootstrap): replace with the first real command."""
    logger.debug("hello invoked")
    click.echo("Hello from {{DIST_NAME}}!")
