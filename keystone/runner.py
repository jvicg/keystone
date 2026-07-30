#!/usr/bin/env python3

"""
Thin wrapper around ansible-runner, wiring it to the Ansible content bundled inside
this package `keystone.ansible`.

This is the only module that interacts directly with Ansible.
"""

# TODO: Confirm LUKS_PASSPHRASE / WIFI_PSK env vars actually reach the ansible-playbook

from collections.abc import Callable
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import ansible_runner

from .plan import Plan

_MAX_ARTIFACTS = 3
_PLAYBOOK = "site.yml"
_INVENTORY = "inventory.yml"
_PRIVATE_DATA_DIR = Path("/tmp/keystone-private-data-dir")
_PRIVATE_DATA_DIR.mkdir(exist_ok=True)


def run_install(
    plan: Plan,
    *,
    quiet: bool = True,
    event_handler: Callable[[dict[str, Any]], bool] | None = None,
) -> ansible_runner.runner.Runner:
    """
    Run the bundled `site.yml` against a validated config.

    Resolves the playbook and roles shipped inside keystone/ansible/
    and hands them to ansible-runner along with the config as extravars.

    Args:
        plan: The formatted execution plan.
        event_handler: Optional callback invoked for every Ansible event.

    Returns:
        ansible_runner.runner.Runner: The `Runner` object.
    """
    extravars = {"keystone": plan.ansible_vars}

    with as_file(files("keystone.ansible")) as ansible_root:
        return ansible_runner.run(
            private_data_dir=_PRIVATE_DATA_DIR,
            project_dir=str(ansible_root),
            playbook=_PLAYBOOK,
            roles_path=str(ansible_root / "roles"),
            inventory=str(ansible_root / _INVENTORY),
            extravars=extravars,
            rotate_artifacts=_MAX_ARTIFACTS,
            event_handler=event_handler,
            quiet=quiet,
        )  # type: ignore
