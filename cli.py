#!/usr/bin/env python3
"""
Platypus BMC Management CLI

<<<<<<< HEAD
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
=======
Commands map directly to your existing async helpers:
- bmc_update()                -> update-fw
- flash_emmc()                -> flash-emmc
- flasher() (FIP/U-Boot)      -> flash-fip
- flash_eeprom()              -> flash-eeprom
- power_host()                -> power-on
- reboot_bmc()                -> reboot-bmc
- bmc_factory_reset()         -> factory-reset
- set_ip(), grab_ip()         -> set-ip, grab-ip
- login()                     -> login
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)
"""

import argparse
import asyncio
import os
import sys
<<<<<<< HEAD

=======
from typing import Callable

# Local modules (must be in the same directory or on PYTHONPATH)
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)
import bmc
import network
import utils


<<<<<<< HEAD
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
=======
# ---------- Console callbacks ----------

def _log_output(verbose: bool = True) -> Callable[[str], None]:
    def _inner(msg: str):
        if verbose:
            print(msg, flush=True)
    return _inner

def _log_progress(verbose: bool = True) -> Callable[[float], None]:
    def _inner(value: float):
        if verbose:
            pct = max(0, min(100, int(round(value * 100))))
            # Print progress to stderr so stdout can be piped if desired
            print(f"[progress] {pct}%", file=sys.stderr, flush=True)
    return _inner


# ---------- Command handlers ----------

async def cmd_update_fw(args):
    output = _log_output(not args.quiet)
    progress = _log_progress(not args.quiet)

    # Read firmware image as bytes
    try:
        with open(args.image, "rb") as f:
            fw_bytes = f.read()
    except Exception as e:
        print(f"Error reading firmware image: {e}", file=sys.stderr)
        sys.exit(2)
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)

    await bmc.bmc_update(
        bmc_user=args.user,
        bmc_pass=args.password,
        bmc_ip=args.bmc_ip,
        fw_content=fw_bytes,
<<<<<<< HEAD
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
=======
        callback_progress=progress,
        callback_output=output
    )

async def cmd_flash_emmc(args):
    output = _log_output(not args.quiet)
    progress = _log_progress(not args.quiet)

>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)
    dd_value = 1 if args.bmc_type.lower() == "mos-bmc" else 0
    await bmc.flash_emmc(
        bmc_ip=args.bmc_ip,
        directory=args.directory,
        my_ip=args.my_ip,
        dd_value=dd_value,
<<<<<<< HEAD
        callback_progress=_progress_cb(args.quiet),
        callback_output=_output_cb(args.quiet),
        serial_device=args.serial,
    )


async def cmd_flash_fip(args):
    if not os.path.isfile(args.fip):
        sys.exit(f"FIP file not found: {args.fip}")
=======
        callback_progress=progress,
        callback_output=output,
        serial_device=args.serial
    )

async def cmd_flash_fip(args):
    output = _log_output(not args.quiet)
    progress = _log_progress(not args.quiet)

    if not os.path.isfile(args.fip):
        print(f"FIP file not found: {args.fip}", file=sys.stderr)
        sys.exit(2)
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)

    await bmc.flasher(
        flash_file=args.fip,
        my_ip=args.my_ip,
<<<<<<< HEAD
        callback_progress=_progress_cb(args.quiet),
        callback_output=_output_cb(args.quiet),
        serial_device=args.serial,
    )


async def cmd_flash_eeprom(args):
    if not os.path.isfile(args.fru):
        sys.exit(f"FRU binary not found: {args.fru}")
=======
        callback_progress=progress,
        callback_output=output,
        serial_device=args.serial
    )

async def cmd_flash_eeprom(args):
    output = _log_output(not args.quiet)
    progress = _log_progress(not args.quiet)

    if not os.path.isfile(args.fru):
        print(f"FRU binary not found: {args.fru}", file=sys.stderr)
        sys.exit(2)
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)

    await bmc.flash_eeprom(
        flash_file=args.fru,
        my_ip=args.my_ip,
<<<<<<< HEAD
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
=======
        callback_progress=progress,
        callback_output=output,
        serial_device=args.serial
    )

async def cmd_power_on(args):
    output = _log_output(not args.quiet)
    await bmc.power_host(output, args.serial)

async def cmd_reboot_bmc(args):
    output = _log_output(not args.quiet)
    await bmc.reboot_bmc(output, args.serial)

async def cmd_factory_reset(args):
    output = _log_output(not args.quiet)
    await bmc.bmc_factory_reset(output, args.serial)

async def cmd_set_ip(args):
    output = _log_output(not args.quiet)
    progress = _log_progress(not args.quiet)
    await network.set_ip(args.bmc_ip, progress, output, args.serial)

async def cmd_grab_ip(args):
    output = _log_output(not args.quiet)
    ip = await network.grab_ip(output, args.serial)
    if ip:
        # print only the IP so the command is scriptable
        print(ip)

async def cmd_login(args):
    output = _log_output(not args.quiet)
    result = await utils.login(args.user, args.password, args.serial, output)
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)
    if result:
        print(result)


<<<<<<< HEAD
# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
=======
# ---------- Parser ----------
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="platypus",
<<<<<<< HEAD
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
=======
        description="Platypus BMC Management CLI"
    )
    p.add_argument("-q", "--quiet", action="store_true", help="suppress logs (only essential outputs)")

    sub = p.add_subparsers(dest="command", required=True)

    # update-fw
    s = sub.add_parser("update-fw", help="Redfish firmware update (HTTP push)")
    s.add_argument("--bmc-ip", required=True, help="BMC IP (e.g., 192.168.0.10)")
    s.add_argument("-u", "--user", required=True, help="BMC username")
    s.add_argument("-p", "--password", required=True, help="BMC password")
    s.add_argument("-i", "--image", required=True, help="Firmware image file to upload")
    s.set_defaults(func=cmd_update_fw)

    # flash-emmc
    s = sub.add_parser("flash-emmc", help="Flash eMMC restore image via serial")
    s.add_argument("--bmc-ip", required=True, help="Target BMC IP to set/use during flow")
    s.add_argument("--directory", required=True, help="Directory serving restore images (contains .wic.xz and .bmap)")
    s.add_argument("--my-ip", required=True, help="Your host IP the BMC will reach")
    s.add_argument("--serial", required=True, help="Serial device (e.g., /dev/ttyUSB0)")
    s.add_argument("--bmc-type", choices=["mos-bmc", "nanobmc"], default="mos-bmc",
                   help="Image flavor used by the flow (default: mos-bmc)")
    s.set_defaults(func=cmd_flash_emmc)

    # flash-fip
    s = sub.add_parser("flash-fip", help="Flash U-Boot (FIP) over serial using curl + dd")
    s.add_argument("--fip", required=True, help="Path to fip-snuc-*.bin")
    s.add_argument("--my-ip", required=True, help="Your host IP the BMC will curl from")
    s.add_argument("--serial", required=True, help="Serial device (e.g., /dev/ttyUSB0)")
    s.set_defaults(func=cmd_flash_fip)

    # flash-eeprom
    s = sub.add_parser("flash-eeprom", help="Flash EEPROM (FRU) over serial")
    s.add_argument("--fru", required=True, help="Path to fru.bin")
    s.add_argument("--my-ip", required=True, help="Your host IP the BMC will curl from")
    s.add_argument("--serial", required=True, help="Serial device (e.g., /dev/ttyUSB0)")
    s.set_defaults(func=cmd_flash_eeprom)

    # power-on
    s = sub.add_parser("power-on", help="Power on host through BMC serial (obmcutil poweron)")
    s.add_argument("--serial", required=True, help="Serial device")
    s.set_defaults(func=cmd_power_on)

    # reboot-bmc
    s = sub.add_parser("reboot-bmc", help="Reboot the BMC via serial")
    s.add_argument("--serial", required=True, help="Serial device")
    s.set_defaults(func=cmd_reboot_bmc)

    # factory-reset
    s = sub.add_parser("factory-reset", help="Factory reset the BMC via serial")
    s.add_argument("--serial", required=True, help="Serial device")
    s.set_defaults(func=cmd_factory_reset)

    # set-ip
    s = sub.add_parser("set-ip", help="Set BMC IP over serial (ifconfig eth0 up <IP>)")
    s.add_argument("--bmc-ip", required=True, help="IP to assign to eth0")
    s.add_argument("--serial", required=True, help="Serial device")
    s.set_defaults(func=cmd_set_ip)

    # grab-ip
    s = sub.add_parser("grab-ip", help="Grab current BMC IP via serial")
    s.add_argument("--serial", required=True, help="Serial device")
    s.set_defaults(func=cmd_grab_ip)

    # login
    s = sub.add_parser("login", help="Send login sequence over serial")
    s.add_argument("-u", "--user", required=True, help="BMC username")
    s.add_argument("-p", "--password", required=True, help="BMC password")
    s.add_argument("--serial", required=True, help="Serial device")
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)
    s.set_defaults(func=cmd_login)

    return p


<<<<<<< HEAD
# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

=======
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)
def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
<<<<<<< HEAD
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
=======


if __name__ == "__main__":
    main()
>>>>>>> 60be66a (https://github.com/emeka1999/platypus-tk)
