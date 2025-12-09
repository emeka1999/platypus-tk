#!/usr/bin/env python3
"""
Platypus BMC Management CLI

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
"""

import argparse
import asyncio
import os
import sys
from typing import Callable

# Local modules (must be in the same directory or on PYTHONPATH)
import bmc
import network
import utils


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

    await bmc.bmc_update(
        bmc_user=args.user,
        bmc_pass=args.password,
        bmc_ip=args.bmc_ip,
        fw_content=fw_bytes,
        callback_progress=progress,
        callback_output=output
    )

async def cmd_flash_emmc(args):
    output = _log_output(not args.quiet)
    progress = _log_progress(not args.quiet)

    dd_value = 1 if args.bmc_type.lower() == "mos-bmc" else 0
    await bmc.flash_emmc(
        bmc_ip=args.bmc_ip,
        directory=args.directory,
        my_ip=args.my_ip,
        dd_value=dd_value,
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

    await bmc.flasher(
        flash_file=args.fip,
        my_ip=args.my_ip,
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

    await bmc.flash_eeprom(
        flash_file=args.fru,
        my_ip=args.my_ip,
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
    if result:
        print(result)


# ---------- Parser ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="platypus",
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
    s.set_defaults(func=cmd_login)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
