#!/usr/bin/env bash
# Script to run a headless virtual machine to do the tests

# Paths
TEST_ENV_DIR="${PWD}/.test_environment"
VDA="${TEST_ENV_DIR}/arch.qcow2"
ISO="${TEST_ENV_DIR}/arch.iso"
UEFI_FIRMWARE_FILE="/usr/share/ovmf/x64/OVMF.4m.fd"
BIOS_FIRMWARE_FILE="/usr/share/qemu/bios-256k.bin"

# Machine specs
CORES=4
MEM="8G"
BOOT_TYPE="c" 
VDA_SIZE="40G"
FIRMWARE_FILE="${BIOS_FIRMWARE_FILE}"  # BIOS is set as the default boot mode

usage() {
  printf "usage: %s [-r] [-b boot_type] [-e|-m] [-h]\n" "$0"
  printf "\t-r\t\t Delete the virtual disk and recreate it.\n"
  printf "\t-b boot_type\t Set boot type ('d' for CD, 'c' for disk) Default is 'd'.\n"
  printf "\t-e\t\t Set the boot mode to UEFI.\n"
  printf "\t-m\t\t Set the boot mode to BIOS.\n"
  printf "\t-h\t\t Display this help message.\n"
  exit 0
}

validate_boot_type() {
  if [[ ! "$1" =~ ^(d|c)$ ]]; then
    printf "error: invalid boot type '%s'. valid options are 'd' (cdrom) or 'c' (disk).\n" "$1"
    exit 1
  fi
}

reset_vm() {
    if pid=$(pidof qemu-system-x86_64); then
        kill "${pid}" >/dev/null 2>&1
        sleep 1
    fi
    [ -f "${VDA}" ] && rm -f "${VDA}"
}

main() {
    # Process args
    while getopts "rmeb:h" opt; do
        case "${opt}" in
            r)
                reset_vm
                ;;
            b)
                BOOT_TYPE="${OPTARG}"
                validate_boot_type "${BOOT_TYPE}"
                ;;
            e)
                FIRMWARE_FILE="${UEFI_FIRMWARE_FILE}"
                ;;
            m)
                FIRMWARE_FILE="${BIOS_FIRMWARE_FILE}"
                ;;
            h)
                usage
                ;;
            *)
                usage
                ;;
        esac
    done

    # Create disk if doesn't exist
    [ ! -f "${VDA}" ] && qemu-img create -f qcow2 -o compat=1.1 "${VDA}" "${VDA_SIZE}" >/dev/null

    # Run the virtual machine
    qemu-system-x86_64 \
        -m "${MEM}" -smp "${CORES}" -enable-kvm -cpu host -boot order="${BOOT_TYPE}" \
        -cdrom "${ISO}" -drive file="${VDA}",if=virtio,format=qcow2 \
        -netdev user,id=net1,hostfwd=tcp::2222-:22 \
        -device virtio-net-pci,netdev=net1 \
        -netdev bridge,br=virbr0,id=net0 \
        -device virtio-net-pci,netdev=net0 \
        -bios "${FIRMWARE_FILE}" \
        -spice port=5900,addr=127.0.0.1,disable-ticketing=on -device qxl & disown

    QEMU_PID=$!

    printf "info: virtual machine is running with process id: '%s'\n" "${QEMU_PID}"
}

main "$@"
