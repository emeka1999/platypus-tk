# Platypus

Platypus is a Python/CustomTkinter desktop application for managing BMC (Baseboard Management Controller) and server hardware. It talks to hardware over the Redfish API, serial/UART connections, and SSH, and wraps common bring-up and maintenance workflows — firmware flashing, BIOS/BMC updates, network configuration, and console access — in a single GUI, with a parallel CLI for scripted/headless use.

**Current version:** 6.1.2

## Features

- **GUI application** (`platypus.py`) — the main CustomTkinter window for day-to-day BMC/server operations.
- **BMC/BIOS firmware updates over Redfish** (`bmc.py`) — `bmc_update()` and `bios_update()` push firmware images to the BMC via the Redfish API.
- **Serial-based flashing and recovery** (`bmc.py`) — eMMC restore image flashing (`flash_emmc`, `flash_emmc2`), U-Boot/FIP flashing (`flasher`), EEPROM/FRU flashing (`flash_eeprom`), U-Boot reset flows (`reset_to_uboot`, `reset_uboot`), factory reset (`bmc_factory_reset`), power control and reboot over serial (`power_host`, `reboot_bmc`).
- **Network configuration over serial** (`network.py`) — read (`grab_ip`) and set (`set_ip`) the BMC's IP address, plus a small local HTTP server (`start_server` / `stop_server`) for serving images to the BMC during flashing.
- **Multi-unit flashing** (`extra.py`) — `MultiUnitFlashWindow` drives the flashing workflow across several units in parallel, with per-unit configuration (`UnitConfig`) and validation.
- **SNUC DMI & FRU tool** (`snuc_flasher.py`) — a standalone Tk window for writing DMI/SMBIOS and FRU data (system name, SKU, serial, manufacturer, etc.) to a unit, including preset device profiles.
- **Shared utilities** (`utils.py`) — managed serial connection lifecycle (`ManagedSerialConnection`, `create_serial_connection`, `register_serial_connection`, `cleanup_all_serial_connections`), serial command/response handling (`read_serial_data`), and Redfish-based system info lookups (`bmc_info`).
- **Command-line interface** (`cli.py`) — exposes the same async operations as subcommands for scripting: `update-fw`, `flash-emmc`, `flash-fip`, `flash-eeprom`, `power-on`, `reboot-bmc`, `factory-reset`, `set-ip`, `grab-ip`, `login`.

## Project layout

| File | Purpose |
|---|---|
| `platypus.py` | Main GUI application (CustomTkinter window, event handling, orchestration). |
| `bmc.py` | Redfish firmware update logic and serial-based flashing/reset/power operations. |
| `network.py` | Serial IP get/set and a lightweight local HTTP server for serving flash images. |
| `extra.py` | Multi-unit flashing window and per-unit configuration/validation. |
| `snuc_flasher.py` | Standalone DMI/FRU (SNUC) writer tool with device presets. |
| `utils.py` | Serial connection management and Redfish system-info helpers shared across modules. |
| `cli.py` | Argparse-based CLI wrapping the async BMC/network operations for headless use. |
| `requirements.txt` | Python dependencies. |

## Requirements

- Python 3
- Dependencies listed in `requirements.txt`:
  - `customtkinter` — GUI framework
  - `pyserial` — serial/UART communication
  - `redfish`, `urllib3` — Redfish API access
  - `psutil` — system/process management
  - `netifaces` — network interface detection (optional, recommended)

Install with:

```bash
pip install -r requirements.txt
```

> **Note:** `pyinstaller`-based packaging requires the `pyserial` package specifically — uninstall any `serial`/`pyserial` conflicts and reinstall `pyserial` cleanly before building.

## Usage

### GUI

```bash
python platypus.py
```

### CLI

The CLI mirrors the GUI's underlying operations as subcommands:

```bash
python cli.py update-fw --bmc-ip 192.168.0.10 -u root -p <password> -i firmware.bin
python cli.py flash-emmc --bmc-ip 192.168.0.10 --directory ./images --my-ip <host-ip> --serial /dev/ttyUSB0
python cli.py flash-fip --fip fip-snuc-*.bin --my-ip <host-ip> --serial /dev/ttyUSB0
python cli.py flash-eeprom --fru fru.bin --my-ip <host-ip> --serial /dev/ttyUSB0
python cli.py power-on --serial /dev/ttyUSB0
python cli.py reboot-bmc --serial /dev/ttyUSB0
python cli.py factory-reset --serial /dev/ttyUSB0
python cli.py set-ip --bmc-ip 192.168.0.10 --serial /dev/ttyUSB0
python cli.py grab-ip --serial /dev/ttyUSB0
python cli.py login -u root -p <password> --serial /dev/ttyUSB0
```

Use `-q`/`--quiet` to suppress non-essential logging.

### SNUC DMI & FRU tool

```bash
python snuc_flasher.py
```

## Development notes

- Built and tested primarily on Linux; several code paths (e.g. `pwd`, X11/XWayland auth discovery for root access) are POSIX-specific.
- Serial connections should go through `utils.create_serial_connection` / `ManagedSerialConnection` so they're tracked and cleaned up centrally via `cleanup_all_serial_connections`.
- Redfish sessions are explicitly logged out after firmware operations; be careful that a secondary failure in a `finally`-block logout doesn't mask the original error.

## License

No license file is currently included in this repository. Add one if you intend to distribute this project.
