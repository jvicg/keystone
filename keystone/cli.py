#!/usr/bin/env python3

"""
Main entry point for the CLI application.
"""

# TODO: Add `--dry-run` mode
# TODO: Validate that the user is running script in a valid Arch ISO before install()
# TODO: Check a way to make easier to the user to get their config file inside of the ISO

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from .__version__ import __title__, __version__
from .contract import load_config_file
from .exceptions import (
    ERR_KEYBOARD_INTERRUPTED,
    ERROR_UNEXPECTED,
    ERROR_VALIDATION_ERROR,
    InstallationFailed,
    KeystoneException,
    NotArchISOEnvironment,
)
from .plan import Plan
from .probe import is_archiso
from .runner import run_install

_DEFAULT_CONFIG_FILE = Path("config.yml")

_PanelType = Literal["info", "error", "warning", "result", "success"]
"""String literals representing valid panel types for rich panels."""

_ConfigFileOption = Annotated[
    Path,
    typer.Option(help="Path to the configuration file.", exists=True, dir_okay=False, readable=True),
]
"""Annotated alias used for `--config-file` option."""

app = typer.Typer(rich_markup_mode="rich", no_args_is_help=True)
console = Console()

# ----------------------
# Helper functions
# ----------------------


def _print_panel(msg: str, panel_type: _PanelType) -> None:
    """
    Generic function to print a rich.Panel with a given type. The type determines the style of the panel.
    """
    styles = {
        "info": ("Info", "white"),
        "error": ("[red]Error[/red]", "red"),
        "warning": ("[yellow]Warning[/yellow]", "yellow"),
        "result": ("[blue]Result[/blue]", "blue"),
        "success": ("[green]Success[/green]", "green"),
    }

    if panel_type not in styles:
        raise ValueError(f"Invalid panel type: {panel_type}")

    title, border_style = styles[panel_type]
    console.print(Panel.fit(msg, title=title, border_style=border_style, title_align="left"))


def _print_success(msg: str) -> None:
    """
    Print info in pretty format using `rich.Panel`.
    """
    _print_panel(msg, panel_type="success")


def _print_error(msg: str) -> None:
    """
    Print error in pretty format using `rich.Panel`.
    """
    _print_panel(msg, panel_type="error")


def _print_validation_errors(err: ValidationError) -> None:
    """
    Render a Pydantic ValidationError as a readable list of field-level problems.

    Args:
        err (ValidationError): The exception raised by `contract.Config.model_validate`.
    """
    count = err.error_count()
    plural = "error" if count == 1 else "errors"
    msg = f"{count} validation {plural}"

    for error in err.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        current_msg = error["msg"].removeprefix("Value error, ")
        msg += f"\n  [bold]{location}[/]\n    {current_msg}"

    _print_error(msg)


# ----------------------
# Exception handling
# ----------------------


def handle_exceptions[**P](func: Callable[P, None]) -> Callable[P, None]:
    """
    Catch expected errors, render them, and exit with the matching status code.

    Unexpected exceptions are keystone bugs, not user errors, so their traceback
    is printed rather than flattened into a one-line message.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            func(*args, **kwargs)

        except KeystoneException as e:
            _print_error(e.msg)
            raise typer.Exit(e.exit_code) from None

        except ValidationError as e:
            _print_validation_errors(e)
            raise typer.Exit(code=ERROR_VALIDATION_ERROR) from None

        except KeyboardInterrupt:
            console.print("\nOperation cancelled by user.")
            raise typer.Exit(ERR_KEYBOARD_INTERRUPTED) from None

        except (typer.Exit, typer.Abort):
            raise

        except Exception as e:
            _print_error(f"Unexpected error ocurred: {e}")
            raise typer.Exit(ERROR_UNEXPECTED) from None

    return wrapper


# ----------------------
# Command-related functions
# ----------------------


def _version_callback(value: bool | None, ctx: typer.Context) -> None:
    """
    Callback function to show the program's version and exit.
    """
    if ctx.resilient_parsing:
        return

    if value:
        console.print(f"{__title__} v{__version__}", highlight=False)
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            help="Show program version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """
    Automatic installation of Arch Linux using Ansible as backend tool.
    """
    pass


@app.command()
@handle_exceptions
def validate(
    config_file: _ConfigFileOption = _DEFAULT_CONFIG_FILE,
    schema_only: Annotated[bool, typer.Option("--schema-only", help="Skip semantic validation.")] = False,
) -> None:
    """
    Validate the configuration file.
    """
    load_config_file(config_file, schema_only)
    mode = " - (schema only)" if schema_only else ""
    _print_success(f"File [b][u]{config_file}[/][/] is valid{mode}")


@app.command()
@handle_exceptions
def install(
    config_file: _ConfigFileOption = _DEFAULT_CONFIG_FILE,
    no_confirm: Annotated[bool, typer.Option("--no-confirm", help="Avoid prompting for any confirmation.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Don't print Ansible installation progress.")] = False,
) -> None:
    """
    Install Arch Linux via TUI or configuration file.
    """
    if not is_archiso():
        raise NotArchISOEnvironment("You must run the script from an Arch ISO environment.")

    config = load_config_file(config_file)

    # Ask user for confirmation before touching anything
    if not no_confirm:
        typer.confirm(
            f"WARNING! All the data in '{config.disks.disk}' will be erased. Are you sure you want to proceed?",
            abort=True,
        )

    plan = Plan(config)
    runner = run_install(plan, quiet=quiet)

    if runner.status != "successful":
        raise InstallationFailed(f"Installation {runner.status} (rc={runner.rc})")
