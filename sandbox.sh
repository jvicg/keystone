#!/usr/bin/env bash
# Script to run a headless virtual machine for testing purposes

# Paths
TEST_ENV_DIR="${PWD}/.test_environment"
VDA="${TEST_ENV_DIR}/arch.qcow2"
ISO="${TEST_ENV_DIR}/arch.iso"

# Firmware settings
UEFI_FIRMWARE_CODE="/usr/share/ovmf/x64/OVMF_CODE.4m.fd"
UEFI_FIRMWARE_VARS_TEMPLATE="/usr/share/ovmf/x64/OVMF_VARS.4m.fd"
UEFI_FIRMWARE_VARS="${TEST_ENV_DIR}/OVMF_VARS.fd"
BIOS_FIRMWARE_FILE="/usr/share/qemu/bios-256k.bin"

# Machine specs
CORES=4
MEM="8G"
BOOT_TYPE="c"
DEFAULT_VDA_SIZE="40G"
VDA_SIZE="${DEFAULT_VDA_SIZE}"
BOOT_MODE="bios"  # BIOS is the default boot mode

usage() {
  echo -e "usage: $0 [-r] [-b boot_type] [-s size] [-e|-m] [-h]"
  echo -e "\t-r\t\t Delete the virtual disk and recreate it."
  echo -e "\t-b BOOT_TYPE\t Set boot type ('d' for CD, 'c' for disk). Default is disk (c)."
  echo -e "\t-s DISK_SIZE\t Set the virtual disk size (e.g. 60G). Default is ${DEFAULT_VDA_SIZE}."
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

validate_disk_size() {
  # Accepts formats like 40, 500M, 60G, 2T (case-insensitive for the unit)
  if [[ ! "$1" =~ ^[1-9][0-9]*[kKmMgGtT]?$ ]]; then
    echo "error: invalid disk size '$1'. format should be a number followed by an optional unit (e.g. 40G, 500M)."
    exit 1
  fi
}

reset_vm() {
  if pid=$(pidof qemu-system-x86_64); then
    kill -TERM "${pid}" >/dev/null 2>&1
    wait "${pid}" 2>/dev/null
  fi

  rm -f "${VDA}"
  rm -f "${UEFI_FIRMWARE_VARS}"
}

main() {
  while getopts "rmeb:s:h" opt; do
    case "${opt}" in
      r)
        reset_vm
        ;;
      b)
        BOOT_TYPE="${OPTARG}"
        validate_boot_type "${BOOT_TYPE}"
        ;;
      s)
        VDA_SIZE="${OPTARG}"
        validate_disk_size "${VDA_SIZE}"
        ;;
      e)
        BOOT_MODE="uefi"
        ;;
      m)
        BOOT_MODE="bios"
        ;;
      h)
        usage
        ;;
      *)
        usage
        ;;
    esac
  done

  # Create disk if it doesn't exist
  if [[ ! -f "${VDA}" ]]; then
    qemu-img create -f qcow2 -o compat=1.1 "${VDA}" "${VDA_SIZE}" >/dev/null
  fi

  # Create persistent UEFI NVRAM if it doesn't exist
  if [[ "${BOOT_MODE}" == "uefi" && ! -f "${UEFI_FIRMWARE_VARS}" ]]; then
    cp "${UEFI_FIRMWARE_VARS_TEMPLATE}" "${UEFI_FIRMWARE_VARS}"
  fi

  # shellcheck disable=SC2054
  QEMU_ARGS=(
    -m "${MEM}"
    -smp "${CORES}"
    -enable-kvm
    -cpu host
    -boot order="${BOOT_TYPE}"
    -cdrom "${ISO}"
    -drive file="${VDA}",if=virtio,format=qcow2
    -virtfs local,path="${PWD}",mount_tag=keystone,security_model=none,readonly=on
    -netdev user,id=net1,hostfwd=tcp::2222-:22
    -device virtio-net-pci,netdev=net1
    -netdev bridge,br=virbr0,id=net0
    -device virtio-net-pci,netdev=net0
    -spice port=5900,addr=127.0.0.1,disable-ticketing=on
    -device qxl
  )

  # shellcheck disable=SC2054
  if [[ "${BOOT_MODE}" == "uefi" ]]; then
    QEMU_ARGS+=(
      -drive if=pflash,format=raw,readonly=on,file="${UEFI_FIRMWARE_CODE}"
      -drive if=pflash,format=raw,file="${UEFI_FIRMWARE_VARS}"
    )
  else
    QEMU_ARGS+=(
      -bios "${BIOS_FIRMWARE_FILE}"
    )
  fi

  qemu-system-x86_64 "${QEMU_ARGS[@]}" & disown

  QEMU_PID=$!

  echo "info: virtual machine is running with process id: '${QEMU_PID}'"
}

main "$@"
