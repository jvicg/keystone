#!/usr/bin/env python3

"""
Pydantic model defining the installation config contract.

This module exposes different models responsible of validating the user input obtained via TUI or via `config.yml`.
"""

# TODO: Raise custom exception on _convert_size_to_bytes()
# TODO: Add support for multiple PVs in LVM
# TODO: Implement support for files in `PackagesConfig.extra` to bulk load packages
# TODO: Validate wifi.ssid using a pattern instead of a raw str
# TODO: Type size arguments in converting functions (i.e `convert_size_to_bytes`)
#       so the get Size types instead of raw strings

from __future__ import annotations

import re
from collections.abc import Callable
from os import getenv
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import AfterValidator, BaseModel, Field, ValidationInfo, model_validator

from .exceptions import ConfigFileNotFound, InvalidYAMLConfigFile, UnreadableConfigFile
from .probe import (
    disk_exists,
    disk_usable_space,
    keymap_exists,
    locale_exists,
    timezone_exists,
)

# ----------------------
# Constants
# ----------------------

_UNIT_MULTIPLIERS_MAP = {"MIB": 1024**2, "GIB": 1024**3}
"""Map with the number of bytes of different bytes units such as MiB or GiB."""

# Partition minimum sizes
_MIN_BOOT_SIZE_BYTES = 512 * _UNIT_MULTIPLIERS_MAP["MIB"]  # Min. /boot partition size - 512 MiB
_MIN_ROOT_SIZE_BYTES = 10 * _UNIT_MULTIPLIERS_MAP["GIB"]  # Min. / partition size - 10 GiB
_MIN_SWAP_SIZE_BYTES = 512 * _UNIT_MULTIPLIERS_MAP["MIB"]  # Min. swap partition size - 512 MiB
_MIN_HOME_SIZE_BYTES = 1 * _UNIT_MULTIPLIERS_MAP["GIB"]  # Min. /home partition size - 1 GiB
_MIN_VAR_SIZE_BYTES = 2 * _UNIT_MULTIPLIERS_MAP["GIB"]  # Min. /var partition size - GiB

_MIN_SIZE_MAP = {
    "root": _MIN_ROOT_SIZE_BYTES,
    "boot": _MIN_BOOT_SIZE_BYTES,
    "swap": _MIN_SWAP_SIZE_BYTES,
    "home": _MIN_HOME_SIZE_BYTES,
    "var": _MIN_VAR_SIZE_BYTES,
}
"""Map used to dinamically calculate the minimum size of the partition that chose `REST_OF_DISK_KEYWORD`."""

# Disk defaults
_DEFAULT_PARTITION_LAYOUT: Literal["standard", "lvm"] = "standard"
_DEFAULT_ENCRYPTION_ENABLED = False

# LVM default config
_DEFAULT_LVM_VG = "vg0"
_DEFAULT_LVM_LV_ROOT = "lv_root"
_DEFAULT_LVM_LV_HOME = "lv_home"
_DEFAULT_LVM_LV_VAR = "lv_var"

# System defaults
_DEFAULT_LOCALE = "en_US.UTF-8"
_DEFAULT_KEYMAP = "us"

# Security defaults
_DEFAULT_SSH_PORT = 22
_DEFAULT_SSH_ENABLED = False
_DEFAULT_LUKS_PASSPHRASE_ENV_VAR = "KEYSTONE_LUKS_PASSPHRASE"
_DEFAULT_PSK_PASSPHRASE_ENV_VAR = "KEYSTONE_WIFI_PSK"

# Packages defaults
_DEFAULT_VIDEO_SERVER: Literal["wayland", "xorg", "none"] = "wayland"
_DEFAULT_DESKTOP_ENV: Literal["gnome", "kde", "hyprland", "none"] = "none"

# Network defaults
_DEFAULT_HOSTNAME = "keystone"
_DEFAULT_IF_NAMES = False
_DEFAULT_WIFI_ENABLED = False

REST_OF_DISK_KEYWORD = "100%FREE"
"""Special value used by the user to choose all the remaining space in a partition."""

# ----------------------
# Patterns
# ----------------------

_PASSWORD_HASH_PATTERN = re.compile(r"^\$6\$[^$]+\$.+$")
"""
SHA-512 crypt hash, e.g. output of `mkpasswd -m sha-512` or Ansible's password_hash() filter. Format: $6$<salt>$<hash>
"""

_HOSTNAME_PATTERN = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$")
"""RFC 1123 hostname: alphanumeric and hyphens, no leading/trailing hyphen per label"""

_DOMAIN_PATTERN = _HOSTNAME_PATTERN
"""Domain pattern: alphanumeric and hyphens, no leading/trailing hyphen per label"""

_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
"""POSIX portable username: lowercase start, alphanumeric/underscore/hyphen, <=32 chars"""

_SSH_PUBKEY_PATTERN = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521) "
    r"[A-Za-z0-9+/]+=*(\s+.*)?$"
)
"""Minimal shape check for an SSH public key line (not a full parser)"""

_PACKAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")
"""Legal Arch package name characters (pacman's own naming rules)"""

_HTTPS_URL_PATTERN = re.compile(r"^https://[^\s]+$")
"""URL pattern."""

_SIZE_PATTERN = re.compile(r"^(?P<size>\d+)\s+(?P<unit>MiB|GiB)$", re.IGNORECASE)
"""Size pattern. The size and the unit will be captured independently in `size` and `unit` named-groups. E.g: 512 MiB"""

_LVM_NAME_PATTERN = re.compile(r"^(?!(?:\.|\.\.|snapshot|pvmove)$)[a-zA-Z0-9_.+][a-zA-Z0-9_\-.+]*$")
"""
LVM name pattern. Enforces lvm(8) rules: allowed characters (a-z, A-Z, 0-9, +, _, ., -),
cannot begin with a hyphen, and cannot be a reserved name (., .., snapshot, pvmove).
"""


# ----------------------
# Helper functions
# ----------------------


def convert_size_to_bytes(size: str | None) -> int:
    """
    Convert an optional string with pattern "XX MiB|GiB" into bytes.

    Args:
        size (str | None): A string matching the pattern `_SIZE_PATTERN` or None.

    Returns:
        int: Returns an integer with the size in bytes or 0 if size is None.
    """
    if size is None:
        return 0

    m = _SIZE_PATTERN.match(size)

    # The size pattern is already validated by `PartitionConfig` model,
    # just double check for cases when it's used outside Config
    if m is None:
        raise ValueError(f"'{size}' does not match the expected size format")

    size_int = int(m["size"])
    unit_upper = m["unit"].upper()

    return size_int * _UNIT_MULTIPLIERS_MAP[unit_upper]


def _format_bytes(size_bytes: int) -> str:
    """
    Format a byte count into a human-readable MiB/GiB string for messages.

    Args:
        size_bytes (int): Integer with a bytes size.

    Returns:
        str: A string like "512 MiB" or "40 GiB".
    """
    gib = _UNIT_MULTIPLIERS_MAP["GIB"]
    mib = _UNIT_MULTIPLIERS_MAP["MIB"]

    if size_bytes >= gib and size_bytes % gib == 0:
        return f"{size_bytes // gib} GiB"

    return f"{size_bytes // mib} MiB"


# ----------------------
# Validation functions
# ----------------------


def _pattern_validator(pattern: re.Pattern[str], err_msg: str) -> Callable[[str], str]:
    """
    Build a validator function that enforces a regex pattern.

    Args:
        pattern: Compiled regex the value must fully match.
        err_msg: Error message raised when the value does not match. May include
            a `{value}` placeholder, filled in with the offending value.

    Returns:
        A callable suitable for `Annotated[str, AfterValidator(...)]`.
    """

    def _validate(value: str) -> str:
        if not pattern.match(value):
            raise ValueError(err_msg.format(value=value))
        return value

    return _validate


def _size_validator(pattern: re.Pattern[str], min_size_bytes: int, err_msg: str) -> Callable[[str], str]:
    """
    Build a validator function that enforces a pattern and a minimum size.

    Args:
        pattern: Compiled regex the value must fully match.
        min_size_bytes: The absolute minimum size in bytes allowed for this field.
        err_msg: Error message raised when the value does not match the pattern.
            May include a `{value}` placeholder, filled in with the offending value.

    Returns:
        A callable suitable for `Annotated[str, AfterValidator(...)]`.
    """

    def _validate(value: str) -> str:
        # Avoid validation for the literal string `REST_OF_DISK_KEYWORD`
        # The validation will be performed in `DiskConfig.validate_sematic` after the remaining space is calculated
        if value == REST_OF_DISK_KEYWORD:
            return value

        m = pattern.match(value)
        if m is None:
            raise ValueError(err_msg.format(value=value))

        # Parsed without extra validation - the pattern already guarantees expected input
        size_bytes = convert_size_to_bytes(value)

        if size_bytes < min_size_bytes:
            raise ValueError(f"'{value}' is below the minimum size of {_format_bytes(min_size_bytes)} for this field")

        return value

    return _validate


def _is_schema_only(info: ValidationInfo) -> bool:
    """
    Return whether semantic (live-system) validation should be skipped.

    Context is optional. `Config.model_validate(data)` with no context at all means full validation.
    To skip semantic checks: `Config.model_validate(data, context={"schema_only": True})`

    Args:
        info: The ValidationInfo passed into a field_validator/model_validator.

    Returns:
        True if semantic checks should be skipped, False otherwise.
    """
    if info.context is None:
        return False
    return bool(info.context.get("schema_only", False))


# ----------------------
# Pattern validate types
# ----------------------


def _build_size_validator(min_size_bytes: int) -> AfterValidator:
    """
    Builds the Pydantic AfterValidator logic, parameterized by its minimum size to avoid code repetition in Size types.
    """
    return AfterValidator(
        _size_validator(
            pattern=_SIZE_PATTERN,
            min_size_bytes=min_size_bytes,
            err_msg="'{value}' is not a valid size. Use pattern: '<size> MiB|GiB'. E.g: 150 GiB",
        )
    )


SizeBoot = Annotated[str, _build_size_validator(min_size_bytes=_MIN_BOOT_SIZE_BYTES)]
SizeRoot = Annotated[str, _build_size_validator(min_size_bytes=_MIN_ROOT_SIZE_BYTES)]
SizeSwap = Annotated[str, _build_size_validator(min_size_bytes=_MIN_SWAP_SIZE_BYTES)]
SizeHome = Annotated[str, _build_size_validator(min_size_bytes=_MIN_HOME_SIZE_BYTES)]
SizeVar = Annotated[str, _build_size_validator(min_size_bytes=_MIN_VAR_SIZE_BYTES)]

LVMName = Annotated[
    str,
    AfterValidator(
        _pattern_validator(
            pattern=_LVM_NAME_PATTERN,
            err_msg="'{value}' is not a valid LVM name. Must contain only letters, numbers, "
            "underscores, periods, or plus signs, cannot start with a hyphen, "
            "and cannot be a reserved name (such as '.', '..', 'snapshot', or 'pvmove').",
        )
    ),
]

Hostname = Annotated[
    str,
    AfterValidator(
        _pattern_validator(
            pattern=_HOSTNAME_PATTERN,
            err_msg="'{value}' is not a valid hostname (RFC 1123: alphanumeric and hyphens "
            "only, no leading/trailing hyphen per label)",
        )
    ),
]

Domain = Annotated[
    str,
    AfterValidator(_pattern_validator(pattern=_DOMAIN_PATTERN, err_msg="'{value}' is not a valid domain")),
]

Username = Annotated[
    str,
    AfterValidator(
        _pattern_validator(
            pattern=_USERNAME_PATTERN,
            err_msg="'{value}' is not a valid username "
            "(lowercase start, alphanumeric/underscore/hyphen, max 32 characters)",
        )
    ),
]

PasswordHash = Annotated[
    str,
    AfterValidator(
        _pattern_validator(
            pattern=_PASSWORD_HASH_PATTERN,
            err_msg="system.password must be a SHA-512 crypt hash (format: $6$<salt>$<hash>), "
            "never a plaintext password. Generate one with `mkpasswd -m sha-512`",
        )
    ),
]

SSHPublicKey = Annotated[
    str,
    AfterValidator(
        _pattern_validator(
            pattern=_SSH_PUBKEY_PATTERN,
            err_msg="security.ssh.ssh_key must be a public key line (e.g. 'ssh-ed25519 "
            "AAAA... user@host'), never a private key",
        )
    ),
]

PackageName = Annotated[
    str,
    AfterValidator(_pattern_validator(pattern=_PACKAGE_NAME_PATTERN, err_msg="'{value}' is not a valid package name")),
]

HttpsUrl = Annotated[
    str,
    AfterValidator(
        _pattern_validator(
            pattern=_HTTPS_URL_PATTERN,
            err_msg="must be an https:// URL (git@ / SSH clone is not supported, to avoid "
            "requiring SSH key provisioning on the live ISO)",
        )
    ),
]

# ----------------------
# Models
# ----------------------


class EncryptionConfig(BaseModel):
    """
    LUKS full-disk encryption settings.
    """

    enabled: bool = _DEFAULT_ENCRYPTION_ENABLED
    passphrase_env_var: str = _DEFAULT_LUKS_PASSPHRASE_ENV_VAR

    @model_validator(mode="after")
    def check_envvar_exists(self) -> EncryptionConfig:
        """
        Ensure that environmental variable is set by user if `enabled` is True.

        Raises:
            ValueError: If wifi.enabled is True but no `passphrase_env_var` set.
        """
        if self.enabled and not getenv(self.passphrase_env_var):
            raise ValueError(
                f"You must set the environmental variable '{self.passphrase_env_var}' to enable encryption. "
                f"E.g: `{self.passphrase_env_var}=SOME_PASSPHRASE keystone install`."
            )
        return self


class LVMConfig(BaseModel):
    """
    LVM configuration.
    """

    vg_name: LVMName = _DEFAULT_LVM_VG
    lv_root_name: LVMName = _DEFAULT_LVM_LV_ROOT
    lv_home_name: LVMName = _DEFAULT_LVM_LV_HOME
    lv_var_name: LVMName = _DEFAULT_LVM_LV_VAR

    def get_lv_name(self, partition: str) -> str:
        """Return the configured LV name for a given partition."""
        mapping = {
            "root": self.lv_root_name,
            "var": self.lv_var_name,
            "home": self.lv_home_name,
        }
        if partition not in mapping:
            raise KeyError(f"'{partition}' has no configured LV name")
        return mapping[partition]


class PartitionsConfig(BaseModel):
    """
    Partition size settings.
    """

    boot: SizeBoot
    swap: SizeSwap | None = None
    root: SizeRoot
    var: SizeVar | None = None
    home: SizeHome | None = None


class DiskConfig(BaseModel):
    """
    Disk selection and partition layout.
    """

    disk: str
    layout: Literal["standard", "lvm"] = _DEFAULT_PARTITION_LAYOUT
    partitions: PartitionsConfig
    lvm_config: LVMConfig = Field(default_factory=LVMConfig)
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)

    @model_validator(mode="after")
    def validate_selected_sizes(self, info: ValidationInfo) -> DiskConfig:
        """
        Check the disk exists and has room for the requested partitions.

        Skipped entirely when schema_only=True (see `_is_schema_only` docstring).

        Raises:
            ValueError: If the disk doesn't exist, or the requested partitions exceed the disk's available space.
        """
        if _is_schema_only(info):
            return self

        if not disk_exists(self.disk):
            raise ValueError(f"Disk '{self.disk}' was not found on this machine")

        is_lvm = self.layout == "lvm"
        requested_bytes = 0
        available_bytes = disk_usable_space(self.disk, is_encrypted=self.encryption.enabled, is_lvm=is_lvm)

        partition_dict = self.partitions.model_dump(exclude_none=True)
        dynamic_partition = ""

        # Iterate over all the partitions to calculate all the bytes
        for partition, size in partition_dict.items():
            if size == REST_OF_DISK_KEYWORD:
                # Ensure only one partition choose `REST_OF_DISK_KEYWORD` value
                if dynamic_partition:
                    raise ValueError(f"Only one partition can use the value '{REST_OF_DISK_KEYWORD}'")
                else:
                    dynamic_partition = partition
                    continue

            requested_bytes += convert_size_to_bytes(size)

        if requested_bytes > available_bytes:
            raise ValueError(
                f"Requested partitions total {_format_bytes(requested_bytes)} but "
                f"'{self.disk}' only has {_format_bytes(available_bytes)} available"
            )

        # Ensure that remaining space is enough for the partition which choose it (if any)
        if dynamic_partition:
            remaining_space = available_bytes - requested_bytes
            min_partition_size = _MIN_SIZE_MAP[dynamic_partition]

            if remaining_space < min_partition_size:
                raise ValueError(
                    f"Minimum size for partition '{dynamic_partition}' "
                    f"is {_format_bytes(min_partition_size)} "
                    f"and there's only {_format_bytes(remaining_space)} left in '{self.disk}'"
                )

        return self


class WifiConfig(BaseModel):
    """
    Wi-Fi network credentials.
    """

    enabled: bool = _DEFAULT_WIFI_ENABLED
    ssid: str | None = None
    psk_env_var: str = _DEFAULT_PSK_PASSPHRASE_ENV_VAR

    @model_validator(mode="after")
    def check_enabled_requires_ssid(self) -> WifiConfig:
        """
        Ensure that `ssid` has a value if `enabled` is True.

        Raises:
            ValueError: If wifi.enabled is True but no ssid is set.
        """
        if self.enabled and not self.ssid:
            raise ValueError("You must set an SSID to enable WiFi")
        return self

    @model_validator(mode="after")
    def check_envvar_exists(self) -> WifiConfig:
        """
        Ensure that environmental variable is set by user if `enabled` is True.

        Raises:
            ValueError: If wifi.enabled is True but no `psk_env_var` set.
        """
        if self.enabled and not getenv(self.psk_env_var):
            raise ValueError(
                f"You must set the environmental variable '{self.psk_env_var}' to enable WiFi. "
                f"E.g: `{self.psk_env_var}=SOME_PASSWORD keystone install`."
            )
        return self


class NetworkConfig(BaseModel):
    """
    Hostname, domain, interface naming, and optional Wi-Fi.
    """

    hostname: Hostname = _DEFAULT_HOSTNAME
    domain: Domain | None = None
    default_if_names: bool = _DEFAULT_IF_NAMES
    wifi: WifiConfig = Field(default_factory=WifiConfig)


class SystemConfig(BaseModel):
    """
    User account, password, and locale settings.
    """

    user: Username
    password: PasswordHash
    locale: str = _DEFAULT_LOCALE
    time_region: str
    keymap: str = _DEFAULT_KEYMAP

    @model_validator(mode="after")
    def validate_semantic(self, info: ValidationInfo) -> SystemConfig:
        """
        Check `locale`, `time_region`, and `keymap` are all available on this system.

        Skipped entirely when schema_only=True (see `_is_schema_only` docstring).

        Raises:
            ValueError: If any of locale, time_region, or keymap is not available.
        """
        if _is_schema_only(info):
            return self

        if not locale_exists(self.locale):
            raise ValueError(f"Locale '{self.locale}' is not available on this system")
        if not timezone_exists(self.time_region):
            raise ValueError(f"Time region '{self.time_region}' does not exist")
        if not keymap_exists(self.keymap):
            raise ValueError(f"Keymap '{self.keymap}' is not available")

        return self


class SSHConfig(BaseModel):
    """
    SSH access settings.
    """

    enabled: bool = _DEFAULT_SSH_ENABLED
    ssh_key: SSHPublicKey | None = None
    ssh_port: int = Field(default=_DEFAULT_SSH_PORT, ge=1, le=65535)

    @model_validator(mode="after")
    def check_enabled_requires_key(self) -> SSHConfig:
        """
        Ensure SSH is not enabled without a key to authorize.

        Raises:
            ValueError: If ssh.enabled is True but no ssh_key is set.
        """
        if self.enabled and not self.ssh_key:
            raise ValueError(
                "security.ssh.enabled is true but no ssh_key was provided - "
                "SSH would be enabled with no way to authenticate"
            )
        return self


class SecurityConfig(BaseModel):
    """SSH, firewall, and root login settings. Defaults are secure-by-default."""

    ssh: SSHConfig = Field(default_factory=SSHConfig)
    firewall: bool = True
    disable_root_login: bool = True


class DriversOverride(BaseModel):
    """
    Manual driver override.

    Hardware (GPU/audio) drivers are autodetected, but maybe overrriden by user.
    """

    video: list[PackageName] | None = None
    audio: list[PackageName] | None = None


class PackagesConfig(BaseModel):
    """
    Driver overrides, display server, desktop environment, and extra packages.
    """

    drivers: DriversOverride = Field(default_factory=DriversOverride)
    video_server: Literal["wayland", "xorg", "none"] = _DEFAULT_VIDEO_SERVER
    desktop_env: Literal["gnome", "kde", "hyprland", "none"] = _DEFAULT_DESKTOP_ENV
    extra: list[PackageName] = Field(default_factory=list)


class ExtrasConfig(BaseModel):
    """
    Optional post-install extras.
    """

    dotfiles_repo: HttpsUrl | None = None
    post_install_script: str | None = None


class Config(BaseModel):
    """
    Root config model that matches all the values of `config.yml` file.
    """

    disks: DiskConfig
    network: NetworkConfig
    system: SystemConfig
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    packages: PackagesConfig = Field(default_factory=PackagesConfig)
    extras: ExtrasConfig = Field(default_factory=ExtrasConfig)


# ----------------------
# Config file handling functions
# ----------------------


def load_config_file(config_file: Path, schema_only: bool = False) -> Config:
    """
    Read and parse a YAML config file.

    Args:
        config_file (Path): A Path object containing the config file path.
        schema_only (bool): Skip semantic validation.

    Returns:
        Config: The parsed YAML contents.

    Raises:
        ConfileFileNotFound: If the file does not exist.
        UnreadableConfigFile: If the file exists but cannot be read, e.g. a permissions problem or an I/O error.
        InvalidYAMLConfigFile: If the file is not parseable as YAML, or parses to something other than a mapping.
        ValidationError: If the file cannot be parsed into a `Config` object.
    """
    try:
        raw = config_file.read_text()
        data = yaml.safe_load(raw)

    except FileNotFoundError as e:
        raise ConfigFileNotFound(f"The file '{config_file}' does not exist.") from e

    except OSError as e:
        raise UnreadableConfigFile(f"Could not read '{config_file}:' {e}") from e

    except yaml.YAMLError as e:
        raise InvalidYAMLConfigFile(f"File '{config_file}' is not valid YAML: {e}") from e

    if data is None:
        raise InvalidYAMLConfigFile(f"File '{config_file}' is empty")

    if not isinstance(data, dict):
        raise InvalidYAMLConfigFile(f"File '{config_file}' must contain a YAML mapping, found {type(data).__name__}")

    return Config.model_validate(data, context={"schema_only": schema_only})
