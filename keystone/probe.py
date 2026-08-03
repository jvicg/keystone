#!/usr/bin/env python3

"""
Live-system probing functions used for semantic config validation.
"""

# TODO: Improve the way of getting the disk available size since `lsblk` doesn't care about used/free space
# TODO: Improve `disk_exists` so its only True when device is not already partitioned

import json
import subprocess
from pathlib import Path

_FIRST_PARTITION_START_RESERVED = 1 * (1024**2)  # 1 MiB - the first start partition offset
_GPT_END_BYTES_RESERVED = 1 * (1024**2)  # 1 MiB - saved at the end of disk for GPT backup
_LVM_PE_RESERVED = 4 * (1024**2)  # 4 MiB - 1 Physical Extend reserved for LVM metadata
_LUKS2_CONTAINER_RESERVED = 16 * (1024**2)  # 16 MiB for LUKS container
_RESERVED_BYTES = _FIRST_PARTITION_START_RESERVED + _GPT_END_BYTES_RESERVED

_ZONEINFO_ROOT = Path("/usr/share/zoneinfo")
_LOCALE_GEN = Path("/etc/locale.gen")
_EFI_CHECK_FILE = Path("/sys/firmware/efi/fw_platform_size")
_ARCHISO_ROOT = Path("/run/archiso/airootfs")


def is_archiso() -> bool:
    """
    Check whether script is running in the official Arch Linux live ISO or not.
    """
    return _ARCHISO_ROOT.exists()


def is_boot_efi() -> bool:
    """
    Check whether the system boot in UEFI mode or not.
    """
    return _EFI_CHECK_FILE.exists()


def disk_exists(device: str) -> bool:
    """
    Check whether a block device exists on this machine.

    Args:
        device (str): Device path, e.g. "/dev/nvme0n1".

    Returns:
        bool: True if lsblk reports the device, False otherwise.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,PATH"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

    data = json.loads(result.stdout)
    paths = {dev.get("path") for dev in data.get("blockdevices", [])}
    return device in paths


def disk_usable_space(device: str, is_encrypted: bool, is_lvm: bool) -> int:
    """
    Space safely available for user partitions, accounting for all reserves:
    1 MiB start offset, GPT end, LVM PE overhead, LUKS metadata.
    Used by contract to validate that the user's requested sizes fit.

    Args:
        device (str): Device path, e.g. "/dev/nvme0n1".
        is_encrypted (bool): Whether the disk will be encrypted or not.
        is_lvm (bool): Whether the partition layout is `lvm` or not.

    Returns:
        int: Device usable space in bytes.
    """
    lvm_reserved = _LVM_PE_RESERVED if is_lvm else 0
    luks_reserved = _LUKS2_CONTAINER_RESERVED if is_encrypted and is_lvm else 0
    reserved_total = luks_reserved + _RESERVED_BYTES + lvm_reserved

    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "PATH,SIZE"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 0

    data = json.loads(result.stdout)

    for dev in data.get("blockdevices", []):
        if dev.get("path") == device and dev.get("size") is not None:
            size = int(dev["size"])
            return size - reserved_total

    return 0


def locale_exists(locale: str) -> bool:
    """
    Check whether a locale is listed in /etc/locale.gen.

    Args:
        locale (str): Locale identifier, e.g. "en_US.UTF-8".

    Returns:
        bool: True if found in /etc/locale.gen (commented or not), False otherwise.
    """
    if not _LOCALE_GEN.exists():
        return False
    return locale in _LOCALE_GEN.read_text()


def timezone_exists(time_region: str) -> bool:
    """
    Check whether a Region/City timezone exists under /usr/share/zoneinfo.

    Args:
        time_region (str): Timezone identifier, e.g. "Europe/Madrid".

    Returns:
        bool: True if the corresponding zoneinfo file exists, False otherwise.
    """
    return (_ZONEINFO_ROOT / time_region).is_file()


def keymap_exists(keymap: str) -> bool:
    """
    Check whether a keymap is available via localectl.

    Args:
        keymap (str): Keymap identifier, e.g. "us".

    Returns:
        bool: True if localectl lists the keymap, False otherwise (including if localectl itself is unavailable).
    """
    try:
        result = subprocess.run(
            ["localectl", "list-keymaps"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

    return keymap in result.stdout.splitlines()
