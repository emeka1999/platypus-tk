#!/usr/bin/env python3
"""
Platypus BMC Management CLI

Commands:
  update-fw       Redfish firmware update (HTTP push)
  bios-update     Redfish BIOS/BMC firmware update with task monitoring
  flash-emmc      Flash eMMC restore image via serial
  flash-fip       Flash U-Boot (FIP) over serial
  flash-eeprom    Flash EEPROM (FRU) over serial
  power-on        Power on host via serial (obmcutil poweron)
  reboot-bmc      Reboot the BMC via serial
  factory-reset   Factory reset the BMC via serial
  reset-uboot     Reboot BMC and interrupt U-Boot autoboot
  set-ip          Set BMC IP over serial
  grab-ip         Grab current BMC IP via serial
  login           Send login sequence over serial
"""

import argparse
import asyncio
import os
import sys

import bmc
import network
import utils


# ---------------------------------------------------------------------------
# Callback factories
# ---------------------------------------------------------------------------

def _output_cb(quiet: bool):
    def _cb(msg: str):
        if not quiet:
            print(msg, flush=True)
    return _cb

def _progress_cb(quiet: bool):
    def _cb(value: float):
        if not quiet:
            pct = max(0, min(100, int(round(value * 100))))
            print(f"[progress] {pct}%", file=sys.stderr, flush=True)
    return _cb


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_update_fw(args):
    try:
        with open(args.image, "rb") as f:
            fw_bytes = f.read()
    except OSError as e:
        sys.exit(f"Error reading firmware image: {e}")

    await bmc.bmc_update(
        bmc_user=args.user,
        bmc_pass=args.password,
        bmc_ip=args.bmc_ip,
        fw_content=fw_bytes,
        callback_progress=_progress_cb(args.quiet),
        callback_output=_output_cb(args.quiet),
    )


async def cmd_bios_update(args):
    try:
        with open(args.image, "rb") as f:
            fw_bytes = f.read()
    except OSError as e:
        sys.exit(f"Error reading firmware image: {e}")

    await bmc.bios_update(
        bmc_user=args.user,
        bmc_pass=args.password,
        bmc_ip=args.bmc_ip,
        fw_content=fw_bytes,
        callback_progress=_progress_cb(args.quiet),
        callback_output=_output_cb(args.quiet),
        is_bmc=args.is_bmc,
    )


async def cmd_flash_emmc(args):
    dd_value = 1 if args.bmc_type.lower() == "mos-bmc" else 0
    await bmc.flash_emmc(
        bmc_ip=args.bmc_ip,
        directory=args.directory,
        my_ip=args.my_ip,
        dd_value=dd_value,
        callback_progress=_progress_cb(args.quiet),
        callback_output=_output_cb(args.quiet),
        serial_device=args.serial,
    )


async def cmd_flash_fip(args):
    if not os.path.isfile(args.fip):
        sys.exit(f"FIP file not found: {args.fip}")

    await bmc.flasher(
        flash_file=args.fip,
        my_ip=args.my_ip,
        callback_progress=_progress_cb(args.quiet),
        callback_output=_output_cb(args.quiet),
        serial_device=args.serial,
    )


async def cmd_flash_eeprom(args):
    if not os.path.isfile(args.fru):
        sys.exit(f"FRU binary not found: {args.fru}")

    await bmc.flash_eeprom(
        flash_file=args.fru,
        my_ip=args.my_ip,
        callback_progress=_progress_cb(args.quiet),
        callback_output=_output_cb(args.quiet),
        serial_device=args.serial,
    )


async def cmd_power_on(args):
    await bmc.power_host(_output_cb(args.quiet), args.serial)


async def cmd_reboot_bmc(args):
    await bmc.reboot_bmc(_output_cb(args.quiet), args.serial)


async def cmd_factory_reset(args):
    await bmc.bmc_factory_reset(_output_cb(args.quiet), args.serial)


async def cmd_reset_uboot(args):
    await bmc.reset_to_uboot(_output_cb(args.quiet), args.serial)


async def cmd_set_ip(args):
    await network.set_ip(
        args.bmc_ip,
        _progress_cb(args.quiet),
        _output_cb(args.quiet),
        args.serial,
    )


async def cmd_grab_ip(args):
    ip = await network.grab_ip(_output_cb(args.quiet), args.serial)
    if ip:
        print(ip)


async def cmd_login(args):
    result = await utils.login(args.user, args.password, args.serial, _output_cb(args.quiet))
    if result:
        print(result)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="platypus",
        description="Platypus BMC Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress logs (only print essential output)")

    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    # ------------------------------------------------------------------
    # update-fw
    # ------------------------------------------------------------------
    s = sub.add_parser("update-fw", help="Redfish firmware update (HTTP push)")
    s.add_argument("--bmc-ip", required=True, metavar="IP", help="BMC IP address")
    s.add_argument("-u", "--user", required=True, help="BMC username")
    s.add_argument("-p", "--password", required=True, help="BMC password")
    s.add_argument("-i", "--image", required=True, metavar="FILE", help="Firmware image file")
    s.set_defaults(func=cmd_update_fw)

    # ------------------------------------------------------------------
    # bios-update
    # ------------------------------------------------------------------
    s = sub.add_parser("bios-update", help="Redfish BIOS or BMC firmware update with task monitoring")
    s.add_argument("--bmc-ip", required=True, metavar="IP", help="BMC IP address")
    s.add_argument("-u", "--user", required=True, help="BMC username")
    s.add_argument("-p", "--password", required=True, help="BMC password")
    s.add_argument("-i", "--image", required=True, metavar="FILE", help="Firmware image file")
    s.add_argument("--is-bmc", action="store_true",
                   help="Set this flag when flashing BMC firmware (handles reboot/connection loss gracefully)")
    s.set_defaults(func=cmd_bios_update)

    # ------------------------------------------------------------------
    # flash-emmc
    # ------------------------------------------------------------------
    s = sub.add_parser("flash-emmc", help="Flash eMMC restore image via serial")
    s.add_argument("--bmc-ip", required=True, metavar="IP", help="BMC IP to set during the flash flow")
    s.add_argument("--directory", required=True, metavar="DIR",
                   help="Directory containing .wic.xz and .bmap restore images")
    s.add_argument("--my-ip", required=True, metavar="IP", help="Host IP the BMC will connect back to")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device (e.g. /dev/ttyUSB0)")
    s.add_argument("--bmc-type", choices=["mos-bmc", "nanobmc"], default="mos-bmc",
                   help="Image flavour (default: mos-bmc)")
    s.set_defaults(func=cmd_flash_emmc)

    # ------------------------------------------------------------------
    # flash-fip
    # ------------------------------------------------------------------
    s = sub.add_parser("flash-fip", help="Flash U-Boot (FIP) over serial using curl + dd")
    s.add_argument("--fip", required=True, metavar="FILE", help="Path to fip-snuc-*.bin")
    s.add_argument("--my-ip", required=True, metavar="IP", help="Host IP the BMC will curl from")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_flash_fip)

    # ------------------------------------------------------------------
    # flash-eeprom
    # ------------------------------------------------------------------
    s = sub.add_parser("flash-eeprom", help="Flash EEPROM (FRU) over serial")
    s.add_argument("--fru", required=True, metavar="FILE", help="Path to fru.bin")
    s.add_argument("--my-ip", required=True, metavar="IP", help="Host IP the BMC will curl from")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_flash_eeprom)

    # ------------------------------------------------------------------
    # power-on
    # ------------------------------------------------------------------
    s = sub.add_parser("power-on", help="Power on host via BMC serial (obmcutil poweron)")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_power_on)

    # ------------------------------------------------------------------
    # reboot-bmc
    # ------------------------------------------------------------------
    s = sub.add_parser("reboot-bmc", help="Reboot the BMC via serial")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_reboot_bmc)

    # ------------------------------------------------------------------
    # factory-reset
    # ------------------------------------------------------------------
    s = sub.add_parser("factory-reset", help="Factory reset the BMC via serial")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_factory_reset)

    # ------------------------------------------------------------------
    # reset-uboot
    # ------------------------------------------------------------------
    s = sub.add_parser("reset-uboot", help="Reboot BMC and interrupt U-Boot autoboot sequence")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_reset_uboot)

    # ------------------------------------------------------------------
    # set-ip
    # ------------------------------------------------------------------
    s = sub.add_parser("set-ip", help="Set BMC IP over serial (ifconfig eth0 up <IP>)")
    s.add_argument("--bmc-ip", required=True, metavar="IP", help="IP address to assign to eth0")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_set_ip)

    # ------------------------------------------------------------------
    # grab-ip
    # ------------------------------------------------------------------
    s = sub.add_parser("grab-ip", help="Grab the current BMC IP via serial")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_grab_ip)

    # ------------------------------------------------------------------
    # login
    # ------------------------------------------------------------------
    s = sub.add_parser("login", help="Send login credentials over serial")
    s.add_argument("-u", "--user", required=True, help="BMC username")
    s.add_argument("-p", "--password", required=True, help="BMC password")
    s.add_argument("--serial", required=True, metavar="DEV", help="Serial device")
    s.set_defaults(func=cmd_login)

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()