#!/usr/bin/env sh
# Script to create the virtual environment, clone the repo and install all the dependencies

TMP_DIR="/tmp"
TMP_DIR_SIZE="1G"
INSTALLATION_DIR="${TMP_DIR}/arch-install"
VENV_DIR="${INSTALLATION_DIR}/.venv"
REQUIREMENTS_FILE="${INSTALLATION_DIR}/requirements.txt"
ANSIBLE_DEPENDENCIES="community.general ansible.posix"
REPO_URL="https://github.com/jvicg/arch-install/archive/refs/heads/main.tar.gz"
REPO_TARBALL="${TMP_DIR}/repo.tar.gz"
TEST_URL="github.com"
MIN_GB_RAM=2

# Error codes
EXIT_SUCCESS=0
ERR_USER_INTERRUPT=1
ERR_NO_NETWORK=2
ERR_LOW_RAM=3
ERR_ALREADY_INSTALLED=4

trap ctrl_c SIGINT SIGTERM  

ctrl_c() {
    # Exit the script and rollback the installation if user interrupts the execution
    printf "warning: The execution was interrupted. Cleaning up...\n"
    rollback "${ERR_USER_INTERRUPT}"
}

rollback() {
    # Rollback installation process
    if [ -d "${INSTALLATION_DIR}" ]; then
        rm -rf "${INSTALLATION_DIR}"
    fi

    exit "$1"
}

ex() {
    # Function to run a command and exit the script if errors occur (used to execute commands that can potentially fail)
    "$@" &  
    PROCESS_PID="$!"   
    wait "$PROCESS_PID"

    # Check for errors on command exit code
    if [ "$?" -ne 0 ]; then
        printf "fatal: Errors ocurred while running the command: '$*'. Terminating...\n"
        rollback "$?"
    fi

    unset PROCESS_PID
}

check_ram() {
    # Check if the system has at least 2GB of RAM
    local ram=$(free -m | awk '/^Mem:/ {print int($2 / 1024)}')
    if [ ${ram} -lt ${MIN_GB_RAM} ]; then
        printf "fatal: The system must have at least ${MIN_GB_RAM}GB of RAM. The current system has ${ram}GB of RAM.\n"
        exit "${ERR_LOW_RAM}"
    fi
}

check_network() {
    # Check if the system has network connection
    if ! ping -c2 ${TEST_URL} &>/dev/null; then
        printf "fatal: No network connection. Please check that you have access to the Internet.\n"
        exit "${ERR_NO_NETWORK}"
    fi
}

check_if_installed() {
    trap - SIGINT SIGTERM

    # Check if script was already executed
    if [ -d "${INSTALLATION_DIR}" ]; then
        while true; do
            read -r -p "warning: The script was already executed. Do you want to reinstall? [y/N]: " choice
            choice=${choice:-N}  # Default value is No

            case "${choice}" in
                [yY]) 
                    printf "info: Reinstalling...\n"
                    rm -rf "${INSTALLATION_DIR}" 
                    break  
                    ;;
                [nN]) 
                    printf "warning: Process aborted.\n"
                    exit ${ERR_ALREADY_INSTALLED}
                    ;;
                *) 
                    printf "warning: Invalid input. Please enter 'y' for Yes or 'n' for No.\n"
                    ;;
            esac
        done
    fi

    trap ctrl_c SIGINT SIGTERM 
}

main() {

    printf "info: Installation initialized. Loading prechecks...\n"

    # Prechecks 
    check_network
    check_if_installed
    check_ram 

    printf "info: Prechecks sucessfully passed. Proceeding with the installation...\n"

    # Extends the size of tmpfs to ensure it has enough space to clone the repository and install Ansible
    ex mount -o remount,size=${TMP_DIR_SIZE} ${TMP_DIR} 
    cd ${TMP_DIR}  # Change to tmp directory to avoid issues if the script is runned on ${INSTALLATION_DIR}

    # Get the repository without using git
    mkdir -p ${INSTALLATION_DIR}
    ex curl -fsSL -o ${REPO_TARBALL} ${REPO_URL}
    tar -xf ${REPO_TARBALL} -C ${INSTALLATION_DIR} --strip-components=1 && rm ${REPO_TARBALL}

    # Create virtual environment
    ex python3 -m venv ${VENV_DIR}
    source ${VENV_DIR}/bin/activate
    
    # Install the dependencies
    ex pip install -r ${REQUIREMENTS_FILE}
    ex ansible-galaxy collection install ${ANSIBLE_DEPENDENCIES}

    printf "success: Installation successfully completed.\n"
    exit ${EXIT_SUCCESS}
}

main
