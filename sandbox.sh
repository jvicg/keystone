#!/usr/bin/env bash
# Script to run a headless virtual machine for testing purposes

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
  echo -e "usage: ""$0"" [-r] [-b boot_type] [-e|-m] [-h]"
  echo -e "\t-r\t\t Delete the virtual disk and recreate it."
  echo -e "\t-b boot_type\t Set boot type ('d' for CD, 'c' for disk) Default is 'd'."
  echo -e "\t-e\t\t Set the boot mode to UEFI."
  echo -e "\t-m\t\t Set the boot mode to BIOS."
  echo -e "\t-h\t\t Display this help message."
  exit 0
}

validate_boot_type() {
  if [[ ! "$1" =~ ^(d|c)$ ]]; then
    echo "error: invalid boot type '$1'. valid options are 'd' (cdrom) or 'c' (disk)."
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
        -virtfs local,path="${PWD}",mount_tag=keystone,security_model=none,readonly=on \
        -netdev user,id=net1,hostfwd=tcp::2222-:22 \
        -device virtio-net-pci,netdev=net1 \
        -netdev bridge,br=virbr0,id=net0 \
        -device virtio-net-pci,netdev=net0 \
        -bios "${FIRMWARE_FILE}" \
        -spice port=5900,addr=127.0.0.1,disable-ticketing=on -device qxl & disown

    QEMU_PID=$!

    echo "info: virtual machine is running with process id: '${QEMU_PID}'"
}

main "$@"
