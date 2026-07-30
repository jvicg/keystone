#!/usr/bin/env python3

"""
Module responsible of parsing the user-input into serialized data that Ansible can understand.

It does expose `Plan` object that will be consumed by Ansible through the `runner`.
"""

# TODO: Avoid importing `convert_size_to_bytes` from `contract`. Maybe move those functions to `helpers` module

from typing import Any, NotRequired, TypedDict

from .contract import REST_OF_DISK_KEYWORD, Config, convert_size_to_bytes
from .probe import disk_usable_space, is_boot_efi

_FIRST_PARTITION_START_MIB = 1

_LVM_PARTITION_NAME = "lvm_pv"
_LVM_PARTITIONS = ("root", "var", "home")
"""List of partitions that will live inside of the LVM's VG (if layout is lvm)."""
_PESIZE = 4
"""Size of the LVM physical extend (in MiB)."""

_PARTED_UNIT = "MiB"
_SYSTEM_ROOT = "/mnt"
_STANDARD_FSTYPE = "ext4"
_BOOT_FSTYPE = "vfat"


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


def _mount_path(partition: str) -> str:
    """
    Returns the filesystem path of a given partition.
    """
    if partition == "root":
        return _SYSTEM_ROOT

    return _SYSTEM_ROOT + "/" + partition


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


_PartitionsSizeMap = dict[str, int]
"""Map containing partitio name as a key and size as a value."""


class _MountEntry(TypedDict):
    """
    Dict containing the filesystem path mountpoint along with the target device.
    """

    path: str
    src: str
    fstype: str


class _PartitionEntry(TypedDict):
    """
    Dict containing all the values expected by `parted` module when creating a new partition.
    """

    name: str
    number: int
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


_LogicalVolumePlan = list[_LogicalVolumeEntry]
"""List of LVM Logical Volumes metadata."""

_MountPlan = list[_MountEntry]
"""List of mount entries."""

_PartitionPlan = list[_PartitionEntry]
"""List of partitions that will be created."""


class Plan:
    """
    Translates a validated `contract.Config` object into a dictionary of variables
    that the Ansible runner can consume natively.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._disk = self._config.disks.disk
        self._is_lvm = self._config.disks.layout == "lvm"
        self._lvm_config = self._config.disks.lvm_config
        self._is_boot_efi = is_boot_efi()
        self._available_disk_bytes = disk_usable_space(self._disk)
        self._partitions_size_map = self._calculate_partition_sizes()
        self._partitions_size_map_lvm = self._calculate_lvm_partitions_size()
        self.ansible_vars = self._build_plan()

    def _calculate_partition_sizes(self) -> _PartitionsSizeMap:
        """
        Parses user input sizes (i.e "50 MiB") into exact MiB integers, dinamically resolving "100%FREE".
        """
        partition_sizes = {}
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
            partition_sizes[part_name] = _bytes_to_mib(size_bytes)

        # Allocate remaining space to the 100%FREE partition (if any)
        if dynamic_partition:
            remaining_bytes = self._available_disk_bytes - allocated_bytes
            partition_sizes[dynamic_partition] = _bytes_to_mib(remaining_bytes)

        return partition_sizes

    def _calculate_lvm_partitions_size(self) -> _PartitionsSizeMap:
        """
        Combines all `_LVM_PARTITION_SIZES` into one single partition that will be used by LVM.
        """
        partition_sizes_lvm = self._partitions_size_map.copy()
        partition_sizes_lvm[_LVM_PARTITION_NAME] = 0

        for partition in _LVM_PARTITIONS:
            if partition in self._partitions_size_map:
                del partition_sizes_lvm[partition]
                partition_sizes_lvm[_LVM_PARTITION_NAME] += self._partitions_size_map.get(partition, 0)

        return partition_sizes_lvm

    def _get_device(self, partition_plan: _PartitionPlan, partition_name: str) -> str | None:
        """
        Get the disk device associated with `partition_name` from a `_PartitionPlan` object.
        """
        for partition in partition_plan:
            if partition.get("name") == partition_name:
                return _partition_device_name(self._disk, partition.get("number"))
        return None

    def _get_lvm_pv_device(self, partition_plan: _PartitionPlan) -> str | None:
        """
        Get the Physical Volume device from a `_PartitionPlan` object.
        """
        return self._get_device(partition_plan, _LVM_PARTITION_NAME)

    def _get_swap_device(self, partition_plan: _PartitionPlan) -> str | None:
        """
        Get the `swap` device from a `_PartitionPlan` object.
        """
        return self._get_device(partition_plan, "swap")

    def _build_partitions_plan(self) -> _PartitionPlan:
        """
        Builds a sequential list of dictionaries ready to be consumed by Ansible's `parted` module.
        """
        partitions_plan = []
        current_start_mib = _FIRST_PARTITION_START_MIB
        current_part_number = 1
        selected_partitions = self._partitions_size_map_lvm if self._is_lvm else self._partitions_size_map
        label = "gpt" if self._is_boot_efi else "msdos"  # Parted partition scheme

        for part_name, part_size in selected_partitions.items():
            partition: _PartitionEntry = {
                "number": current_part_number,
                "part_start": current_start_mib,
                "part_end": current_start_mib + part_size,
                "label": label,
                "name": part_name,
                "flags": [],
            }

            if part_name == "boot":
                partition["flags"] = ["esp"] if self._is_boot_efi else ["boot"]

            if part_name == _LVM_PARTITION_NAME:
                partition["flags"] = ["lvm"]

            partitions_plan.append(partition)
            current_part_number += 1
            current_start_mib += part_size

        return partitions_plan

    def _build_mount_plan_standard(self, partition_plan: _PartitionPlan) -> _MountPlan:
        """
        Build mount entries for a standard (non-LVM) layout.

        Device paths are derived from the partition number in `partitions_plan`.
        Swap is excluded, it is activated separately, not mounted.
        """
        mount_plan = []

        for partition in partition_plan:
            name = partition["name"]

            if name == "swap":
                continue

            fstype = _BOOT_FSTYPE if name == "boot" else _STANDARD_FSTYPE
            mount_plan.append(
                {
                    "path": _mount_path(name),
                    "src": _partition_device_name(self._disk, partition["number"]),
                    "fstype": fstype,
                }
            )

        return mount_plan

    def _build_mount_plan_lvm(self, partition_plan: _PartitionPlan) -> _MountPlan:
        """
        Build mount entries for an LVM layout.

        Sources differ by partition type:
        - LVM members (root, var, home): virtual devices under /dev/mapper, sourced
            from `_partitions_size_map` - which still holds the original names, unlike
            `partition_plan` where they collapsed into a single `lvm_pv` entry.
        - boot: a real partition; device path derived from its number in
            `partition_plan`.
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
            if partition in self._partitions_size_map
        ]

        # Look for boot partition number in `partitions_plan`.
        for partition in partition_plan:
            if partition["name"] == "boot":
                mount_plan.append(
                    {
                        "path": _mount_path("boot"),
                        "src": _partition_device_name(self._disk, partition["number"]),
                        "fstype": _BOOT_FSTYPE,
                    }
                )
                break

        return mount_plan

    def _build_mount_plan(self, partition_plan: _PartitionPlan) -> _MountPlan:
        """
        Builds the partition device and destination paths for each mountpoint.

        Dispatches to layout-specific helpers since the two layouts source their
        device information differently.
        """
        if self._is_lvm:
            return self._build_mount_plan_lvm(partition_plan)

        return self._build_mount_plan_standard(partition_plan)

    def _build_lvm_lv_plan(self) -> _LogicalVolumePlan:
        """
        Calculate the logical volumes size ensuring that they're multiple of `_PESIZE`.
        and reserving space for LVM metadata.
        """
        lvm_plan = []

        for partition in _LVM_PARTITIONS:
            size = self._partitions_size_map.get(partition)
            if size:
                lvm_plan.append({"lv": self._lvm_config.get_lv_name(partition), "size": _align_down(size, _PESIZE)})

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
        partition_plan = self._build_partitions_plan()
        plan["partition_plan"] = partition_plan
        plan["mount_plan"] = self._build_mount_plan(partition_plan)
        plan["swap_device"] = self._get_swap_device(partition_plan)

        if self._is_lvm:
            plan["lvm_pesize"] = _PESIZE
            plan["lvm_volumes_plan"] = self._build_lvm_lv_plan()
            plan["lvm_pv_device"] = self._get_lvm_pv_device(partition_plan)

        return plan
