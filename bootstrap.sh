#!/usr/bin/env bash
# Script to expand the filesystem, create the virtual environment,
# clone the repo and install all the dependencies

set -uo pipefail

# Paths
TMP_DIR="/tmp"
INSTALL_DIR="${TMP_DIR}/keystone"
VENV_DIR="${TMP_DIR}/keystone-venv"
REPO_TARBALL="${TMP_DIR}/keystone.tar.gz"
KEYSTONE_LINK="/usr/local/bin/keystone"
ANSIBLE_PLAYBOOK_LINK="/usr/local/bin/ansible-playbook"

# Directory pip installs from. Set by `fetch_source`, and NEVER deleted: when
# KEYSTONE_SOURCE points at a local checkout this is the user's own repo.
SOURCE_DIR=""

# Package source. Can be overridden for local development to avoid downloads
# (e.g., `KEYSTONE_SOURCE=/mnt/keystone ./boostrap.sh` when sharing the repo via virtfs).
KEYSTONE_SOURCE="${KEYSTONE_SOURCE:-https://github.com/jvicg/keystone/archive/refs/heads/main.tar.gz}"

# Collections dependencies
ANSIBLE_COLLECTIONS=(community.general community.crypto ansible.posix)

# URL used to ensure Internet connection
TEST_URL="https://github.com"

# Fraction of total RAM will be used to expand tmpfs, since there's no
# enough space in the ISO fs to store all the project
MIN_RAM_MB=3800
TMPFS_MIN_MB=1024
TMPFS_RAM_DIVISOR=8  # 1/8 parts of the total RAM to be used for the FS

# Error codes
ERR_USER_INTERRUPT=1
ERR_NO_NETWORK=2
ERR_LOW_RAM=3
ERR_ALREADY_INSTALLED=4
ERR_NOT_ROOT=5
ERR_COMMAND_FAILED=6

trap ctrl_c SIGINT SIGTERM

# shellcheck disable=SC2329
ctrl_c() {
    echo "warning: The execution was interrupted. Cleaning up..."
    rollback "${ERR_USER_INTERRUPT}"
}

rollback() {
    # Rollback installation process
    [[ -d "${INSTALL_DIR}" ]] && rm -rf "${INSTALL_DIR}"
    [[ -d "${VENV_DIR}" ]] && rm -rf "${VENV_DIR}"
    [[ -f "${REPO_TARBALL}" ]] && rm -f "${REPO_TARBALL}"
    [[ -L "${KEYSTONE_LINK}" ]] && rm -f "${KEYSTONE_LINK}"
    [[ -L "${ANSIBLE_PLAYBOOK_LINK}" ]] && rm -f "${ANSIBLE_PLAYBOOK_LINK}"

    exit "$1"
}

ex() {
    # Run a command, rolling back if it fails.
    local rc=0

    "$@" || rc="$?"

    if [ "${rc}" -ne 0 ]; then
        echo "fatal: Errors ocurred while running the command: '$*' (exit code: ${rc}) Terminating..."
        rollback "${ERR_COMMAND_FAILED}"
    fi
}

check_root() {
    # Partitioning, mounting and remounting tmpfs all need root.
    if [ "$(id -u)" -ne 0 ]; then
        echo "fatal: This script must be run as root."
        exit "${ERR_NOT_ROOT}"
    fi
}

check_network() {
    if ! curl -fsS --max-time 10 -o /dev/null "${TEST_URL}"; then
        echo "fatal: No network connection. Please check that you have access to the Internet."
        exit "${ERR_NO_NETWORK}"
    fi
}

check_ram() {
    local ram_mb
    ram_mb="$(free -m | awk '/^Mem:/ {print $2}')"

    if [ "${ram_mb}" -lt "${MIN_RAM_MB}" ]; then
        echo "fatal: The system must have at least 4GB of RAM. The current system reports ${ram_mb}MB."
        exit "${ERR_LOW_RAM}"
    fi
}

check_if_installed() {
    trap - SIGINT SIGTERM

    if [[ -d "${INSTALL_DIR}" ]] || [[ -L "${KEYSTONE_LINK}" ]]; then
        while true; do
            read -r -p "warning: The script was already executed. Do you want to reinstall? [y/N]: " choice
            choice=${choice:-N}
            case "${choice}" in
                [yY])
                    echo "info: Reinstalling..."
                    rm -rf "${INSTALL_DIR}" "${VENV_DIR}" "${KEYSTONE_LINK}" "${ANSIBLE_PLAYBOOK_LINK}"
                    break
                    ;;
                [nN])
                    echo "warning: Process aborted."
                    exit "${ERR_ALREADY_INSTALLED}"
                    ;;
                *)
                    echo "warning: Invalid input. Please enter 'y' for Yes or 'n' for No."
                    ;;
            esac
        done
    fi

    trap ctrl_c SIGINT SIGTERM
}

expand_tmpfs() {
    # Scale with available RAM to expand filesystem rather than a flat size
    local ram_mb tmpfs_mb
    ram_mb="$(free -m | awk '/^Mem:/ {print $2}')"
    tmpfs_mb=$(( ram_mb / TMPFS_RAM_DIVISOR ))

    if [ "${tmpfs_mb}" -lt "${TMPFS_MIN_MB}" ]; then
        tmpfs_mb="${TMPFS_MIN_MB}"
    fi

    echo "info: Expanding ${TMP_DIR} to ${tmpfs_mb}MB..."
    ex mount -o "remount,size=${tmpfs_mb}M" "${TMP_DIR}"
}

fetch_source() {
    # Check if `KEYSTONE_SOURCE` is passed to the script to skip downloading remote repo
    if [[ -d "${KEYSTONE_SOURCE}" ]]; then
        echo "info: Using local source at '${KEYSTONE_SOURCE}'."
        SOURCE_DIR="${KEYSTONE_SOURCE}"
        return
    fi

    echo "info: Downloading keystone from '${KEYSTONE_SOURCE}'..."
    mkdir -p "${INSTALL_DIR}"
    ex curl -fsSL -o "${REPO_TARBALL}" "${KEYSTONE_SOURCE}"
    ex tar -xf "${REPO_TARBALL}" -C "${INSTALL_DIR}" --strip-components=1
    rm -f "${REPO_TARBALL}"

    SOURCE_DIR="${INSTALL_DIR}"
}

install_keystone() {
    echo "info: Creating virtual environment..."
    ex python3 -m venv "${VENV_DIR}"

    # shellcheck disable=SC1091
    . "${VENV_DIR}/bin/activate"

    echo "info: Installing keystone and its dependencies..."
    ex pip install --quiet --upgrade pip
    ex pip install --quiet "${SOURCE_DIR}"

    # Link venv binaries to /usr/local/bin
    ex ln -sf "${VENV_DIR}/bin/keystone" "${KEYSTONE_LINK}"
    ex ln -sf "${VENV_DIR}/bin/ansible-playbook" "${ANSIBLE_PLAYBOOK_LINK}"

    echo "info: Installing Ansible collections..."
    ex ansible-galaxy collection install "${ANSIBLE_COLLECTIONS[@]}"
}

main() {
    echo "info: Installation initialized. Loading prechecks..."

    # Run pre-checks
    check_root
    check_network
    check_ram
    check_if_installed

    echo "info: Prechecks sucessfully passed. Proceeding with the installation..."

    # Expand tmpfs, fetch project and install it
    expand_tmpfs
    cd "${TMP_DIR}" || exit "${ERR_COMMAND_FAILED}"
    fetch_source
    install_keystone

    cat <<EOF

success: Installation completed. Available commands:

  keystone tui                  Interactive wizard
  keystone validate <file>      Check a config file without installing
  keystone install <file>       Run the installation

You can run any command with the --help flag for more information.

EOF
}

main "$@"
