#!/usr/bin/env python3

"""
Custom exception classes used across the project.
"""


# TODO: Dinamically calculate the ERR_KEYBOARD_INTERRUPTED by calculating the exit signal + 127

ERROR_UNEXPECTED = -1
ERROR_VALIDATION_ERROR = 30
ERR_KEYBOARD_INTERRUPTED = 130


class _BaseException(Exception):
    """
    Private base class to build all application-specific exceptions.

    Provides a standard message and exit code for controlled termination
    of the CLI program when an expected error occurs.

    All subclasses should define its own `exit_code`.
    """

    exit_code = 99

    def __init__(self, msg: str) -> None:
        _exception_name = type(self).__name__

        self.msg = f"{_exception_name}: {msg}"
        super().__init__(self.msg)

    def __repr__(self) -> str:
        """
        String representation of the exception.
        """
        return f"[{self.__class__.__name__}] {self.msg}"


class KeystoneException(_BaseException):
    """
    Base class to build all handled exceptions in the application.

    This class inherits all behavior from `_BaseException`, but exists only
    to provide a clean and conventional name to catch in application code.
    """

    pass


class ConfigFileNotFound(KeystoneException):
    """Raised when the config file provided by user does not exist."""

    exit_code = 10


class UnreadableConfigFile(KeystoneException):
    """Raised when the config file provided by user cannot be readen."""

    exit_code = 11


class InvalidYAMLConfigFile(KeystoneException):
    """Raised when the config file provided does not support YAML parsing."""

    exit_code = 12


class InstallationFailed(KeystoneException):
    """Raised when Ansible returns a non-successful status."""

    exit_code = 20


class NotArchISOEnvironment(KeystoneException):
    """Raised when running the install script from outside an Arch ISO."""

    exit_code = 21
