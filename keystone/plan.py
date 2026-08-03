#!/usr/bin/env python3

"""
Parsing of the user input into serialized data that Ansible can understand.

This module exposes `Plan` object that will be consumed by Ansible through `runner` module.
"""

# TODO: Avoid importing `convert_size_to_bytes` from `contract`. Maybe move those functions to `helpers` module

from typing import Any, NotRequired, TypedDict

from .contract import REST_OF_DISK_KEYWORD, Config, convert_size_to_bytes
from .probe import disk_usable_space, is_boot_efi

# Partition and filesystem related constants
_FIRST_PARTITION_START_MIB = 1
_PARTED_UNIT = "MiB"
_SYSTEM_ROOT = "/mnt"
_STANDARD_FSTYPE = "ext4"
_BOOT_FSTYPE = "vfat"

# LVM-related constants
_PESIZE_MIB = 4
_LVM_PARTITION_NAME = "lvm_pv"
_LVM_PARTITIONS = ("root", "var", "home")
"""List of partitions that will live inside of the LVM's VG (if layout is lvm)."""

# Encryption-related constants
_LUKS_CONTAINER_SIZE_MIB = 16
_CRYPT_DEVICE_PREFIX = "crypt_"
"""Prefix used to name the LUKS containers."""


# ----------------------
# Helper functions
# ----------------------


def _bytes_to_mib(b: int) -> int:
    """
    Convert bytes to MiB.
    """
    return b // (1024**2)


def _align_down(x: int, multiple: int) -> int:
    """
    Returns the `x` closest multiple of `multiple` rounding down.
    """
    return (x // multiple) * multiple


def _mount_path(partition_name: str) -> str:
    """
    Returns the filesystem path of a given partition.
    """
    if partition_name == "root":
        return _SYSTEM_ROOT

    return _SYSTEM_ROOT + "/" + partition_name


def _partition_device_name(device: str, number: int) -> str:
    """
    Returns the partition device path for a given disk device and partition number.
    """
    suffix = "p" if device[-1].isdigit() else ""
    return f"{device}{suffix}{number}"


def _lvm_device_name(vg: str, lv: str) -> str:
    """
    Converts VG and LV names into the standard /dev/mapper path, escaping single hyphens with double hyphens.
    """
    vg_escaped = vg.replace("-", "--")
    lv_escaped = lv.replace("-", "--")
    return f"/dev/mapper/{vg_escaped}-{lv_escaped}"


def _encrypted_device_name(partition_name: str) -> str:
    """
    Returns the path to the encrypted device relative to a partition.
    """
    return "/dev/mapper/" + _CRYPT_DEVICE_PREFIX + partition_name


# ----------------------
# Types
# ----------------------


class _MountEntry(TypedDict):
    """
    Dict containing the filesystem path mount point along with the device and filesystem target.
    """

    path: str
    src: str
    fstype: str


class _PartitionEntry(TypedDict):
    """
    Dict containing all the values related to partition and used as the source of truth for subsequent plans
    such as `_MountPlan` or `EncryptionPlan`.
    """

    name: str
    number: int
    device: str
    active_device: str
    mount_path: str
    part_start: int
    part_end: int
    label: str
    flags: NotRequired[list]


class _LogicalVolumeEntry(TypedDict):
    """
    Dict containing values for the Ansible's module `community.general.lvol`.
    """

    lv: str
    size: int


class _EncryptedDeviceEntry(TypedDict):
    """
    Dict containing the values for the Ansible's module `community.crypto.luks_device`.
    """

    device: str
    name: str


_PartitionSizeMap = dict[str, int]
"""Map containing partition name as key and size as value."""

_LogicalVolumePlan = list[_LogicalVolumeEntry]
"""List of LVM Logical Volumes metadata."""

_MountPlan = list[_MountEntry]
"""List of mount entries."""

_PartitionPlan = list[_PartitionEntry]
"""List of partitions that will be created."""

_EncryptionPlan = list[_EncryptedDeviceEntry]
"""List of partitions that will be encrypted."""


# ----------------------
# Main module logic
# ----------------------


class Plan:
    """
    Translates a validated `contract.Config` object into a dictionary of variables
    that the Ansible runner can consume natively.
    """

    def __init__(self, config: Config) -> None:
        """
        Initialize calculations and build variables for Ansible runner.
        """
        self._config = config

        # Base disk-related flags and variables
        self._disk = self._config.disks.disk
        self._is_lvm = self._config.disks.layout == "lvm"
        self._is_encrypt = self._config.disks.encryption.enabled
        self._is_boot_efi = is_boot_efi()
        self._lvm_config = self._config.disks.lvm_config
        self._disk_total_usable_bytes = disk_usable_space(self._disk, self._is_encrypt, self._is_lvm)

        # Key-value maps containing partition name as key and size as value
        self._partition_size_map = self._calculate_partition_size()
        self._partition_size_map_lvm = self._calculate_lvm_partition_size()

        # Partitioning data used as source of truth for downstream plan generators (`_MountPlan`, `_EncryptionPlan`)
        self._partition_plan = self._build_partition_plan()
        self._partition_plan_map = {p["name"]: p for p in self._partition_plan}

        # Main output payload consumed by `runner`
        self.ansible_vars = self._build_plan()

    def _calculate_partition_size(self) -> _PartitionSizeMap:
        """
        Parses user input sizes (i.e "50 MiB") into exact MiB integers, dinamically resolving "100%FREE".
        Returns a key-value map where key is the partition name and value is the size.
        """
        partition_size_map = {}
        allocated_bytes = 0
        dynamic_partition = None

        partitions_raw = self._config.disks.partitions.model_dump(exclude_none=True)

        """
        NOTE: model_dump() preserves the field declaration order from `contract.PartitionsConfig`.
        The partition layout therefore follows the order defined in the Pydantic model.
        """

        # Calculate fixed sizes
        for part_name, size_str in partitions_raw.items():
            if size_str == REST_OF_DISK_KEYWORD:
                dynamic_partition = part_name
                continue

            size_bytes = convert_size_to_bytes(size_str)

            allocated_bytes += size_bytes
            partition_size_map[part_name] = _bytes_to_mib(size_bytes)

        # Allocate remaining space to the 100%FREE partition (if any)
        if dynamic_partition:
            remaining_bytes = self._disk_total_usable_bytes - allocated_bytes
            partition_size_map[dynamic_partition] = _bytes_to_mib(remaining_bytes)

        return partition_size_map

    def _calculate_lvm_partition_size(self) -> _PartitionSizeMap:
        """
        Combines all `_LVM_PARTITIONS` into one single partition that will be used to create LVM Physical Volume.
        Returns a key-value map where key is the partition name and value is the size.
        """
        partition_size_map_lvm = self._partition_size_map.copy()
        partition_size_map_lvm[_LVM_PARTITION_NAME] = _PESIZE_MIB

        # Ensure that the PV has enough space for LUKS container + VG
        if self._is_encrypt:
            partition_size_map_lvm[_LVM_PARTITION_NAME] += _LUKS_CONTAINER_SIZE_MIB

        for partition in _LVM_PARTITIONS:
            if partition in self._partition_size_map:
                del partition_size_map_lvm[partition]
                partition_size_map_lvm[_LVM_PARTITION_NAME] += self._partition_size_map.get(partition, 0)

        return partition_size_map_lvm

    def _get_active_device(self, part_name: str, part_number: int) -> str:
        """
        Returns the final block device path for a given partition.
        Resolves to the LUKS mapper device if encryption is enabled for this partition,
        otherwise returns the raw partition device path.
        """
        raw_device = _partition_device_name(self._disk, part_number)

        if self._is_encrypt and part_name not in ("boot", "swap"):
            return _encrypted_device_name(part_name)

        return raw_device

    def _build_partition_plan(self) -> _PartitionPlan:
        """
        Builds a sequential list of dictionaries ready to be consumed by Ansible's `parted` module.
        """
        partition_plan = []
        current_start_mib = _FIRST_PARTITION_START_MIB
        current_part_number = 1
        selected_partitions = self._partition_size_map_lvm if self._is_lvm else self._partition_size_map
        label = "gpt" if self._is_boot_efi else "msdos"  # Parted partition scheme

        for part_name, part_size in selected_partitions.items():
            partition: _PartitionEntry = {
                "number": current_part_number,
                "device": _partition_device_name(self._disk, current_part_number),
                "active_device": self._get_active_device(part_name, current_part_number),
                "mount_path": _mount_path(part_name),
                "part_start": current_start_mib,
                "part_end": current_start_mib + part_size,
                "label": label,
                "name": part_name,
                "flags": [],
            }

            if part_name == "boot":
                partition["flags"] = ["esp"] if self._is_boot_efi else ["boot"]

            elif part_name == _LVM_PARTITION_NAME:
                partition["flags"] = ["lvm"]

            partition_plan.append(partition)
            current_part_number += 1
            current_start_mib += part_size

        return partition_plan

    def _build_encryption_plan(self) -> _EncryptionPlan:
        """
        Build the list of disk partitions that will be encrypted using LUKS.
        Neither /boot or swap will be encrypted.
        """
        encryption_plan: _EncryptionPlan = [
            {
                "device": p["device"],
                "name": _CRYPT_DEVICE_PREFIX + p["name"],
            }
            for p in self._partition_plan_map.values()
            if p["name"] not in ("boot", "swap")
        ]

        return encryption_plan

    def _build_mount_plan_standard(self) -> _MountPlan:
        """
        Build mount entries for a standard (non-LVM) layout.
        Swap is excluded, it is activated separately, not mounted.
        """
        mount_plan: _MountPlan = [
            {
                "path": p["mount_path"],
                "src": p["active_device"],
                "fstype": _BOOT_FSTYPE if p["name"] == "boot" else _STANDARD_FSTYPE,
            }
            for p in self._partition_plan_map.values()
            if p["name"] != "swap"
        ]

        return mount_plan

    def _build_mount_plan_lvm(self) -> _MountPlan:
        """
        Build mount entries for an LVM layout.

        Sources differ by partition type:
        - LVM members (root, var, home): virtual devices under /dev/mapper, sourced
            from `_partitions_size_map` - which still holds the original names, unlike
            `_partition_plan_map` where they collapsed into a single `lvm_pv` entry.
        - boot: a real partition; device path derived from its number in
            `_partition_plan_map`.
        """
        vg = self._lvm_config.vg_name

        # Build `mount_plan` using list comprehension
        mount_plan: _MountPlan = [
            {
                "path": _mount_path(partition),
                "src": _lvm_device_name(vg, self._lvm_config.get_lv_name(partition)),
                "fstype": _STANDARD_FSTYPE,
            }
            for partition in _LVM_PARTITIONS
            if partition in self._partition_size_map
        ]

        # Get boot data from `_partition_plan_map`
        if boot_part := self._partition_plan_map.get("boot", {}):
            boot_entry: _MountEntry = {
                "path": boot_part["mount_path"],
                "src": boot_part["active_device"],
                "fstype": _BOOT_FSTYPE,
            }
            mount_plan.append(boot_entry)

        return mount_plan

    def _build_mount_plan(self) -> _MountPlan:
        """
        Builds the partition device and destination paths for each mountpoint.

        Dispatches to layout-specific helpers since the two layouts source their
        device information differently.
        """
        if self._is_lvm:
            return self._build_mount_plan_lvm()

        return self._build_mount_plan_standard()

    def _build_lvm_lv_plan(self) -> _LogicalVolumePlan:
        """
        Calculate the logical volumes size ensuring they're multiple of `_PESIZE_MIB` (rounded down if necessary).
        """
        lvm_plan = []

        for partition in _LVM_PARTITIONS:
            size = self._partition_size_map.get(partition)
            if size:
                lvm_plan.append({"lv": self._lvm_config.get_lv_name(partition), "size": _align_down(size, _PESIZE_MIB)})

        return lvm_plan

    def _build_plan(self) -> dict[str, Any]:
        """
        Builds the final dictionary to pass to ansible-runner.
        """
        plan = self._config.model_dump(exclude_none=True)
        plan["parted_unit"] = _PARTED_UNIT
        plan["system_root"] = _SYSTEM_ROOT
        plan["lvm_enabled"] = self._is_lvm

        # Dynamic calculations
        plan["partition_plan"] = self._partition_plan
        plan["mount_plan"] = self._build_mount_plan()

        swap_part = self._partition_plan_map.get("swap")
        plan["swap_device"] = swap_part["device"] if swap_part else None

        # Crypt specific variables
        if self._is_encrypt:
            plan["encryption_luks_passphrase_env_var"] = self._config.disks.encryption.passphrase_env_var
            plan["encryption_plan"] = self._build_encryption_plan()

        # LVM-specific variables
        if self._is_lvm:
            lvm_part = self._partition_plan_map[_LVM_PARTITION_NAME]
            plan["lvm_pesize"] = _PESIZE_MIB
            plan["lvm_volumes_plan"] = self._build_lvm_lv_plan()
            plan["lvm_pv_device"] = lvm_part["active_device"]

        return plan
