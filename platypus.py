import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

# Work around a real bug in some installed CustomTkinter versions:
# CTkScrollbar._on_motion() reads self._motion_center_offset, but
# __init__() never initializes it - only the click handlers
# (_clicked/_clicked_scrollbar) ever set it. If a drag/motion event
# reaches the scrollbar before one of those handlers has fired first
# (easy to trigger depending on exactly where the initial click lands),
# it raises AttributeError and crashes the app. This affects every
# CTkScrollableFrame in the app (Inventory, Sensors, etc.), since they
# each create their own CTkScrollbar internally - patched here, once, at
# import time, rather than requiring everyone to pin a fixed CTk version.
if not hasattr(ctk.CTkScrollbar, "_platypus_motion_offset_patch"):
    _original_ctk_scrollbar_init = ctk.CTkScrollbar.__init__

    def _patched_ctk_scrollbar_init(self, *args, **kwargs):
        _original_ctk_scrollbar_init(self, *args, **kwargs)
        self._motion_center_offset = 0

    ctk.CTkScrollbar.__init__ = _patched_ctk_scrollbar_init
    ctk.CTkScrollbar._platypus_motion_offset_patch = True

import asyncio, glob, bmc, json, os, time, psutil, threading, subprocess, shutil, webbrowser
import serial
from utils import *
from network import *
from embedded_console import EmbeddedConsole
from inventory_panel import InventoryPanel
from virtual_media_panel import VirtualMediaPanel
from sensors_panel import SensorsPanel
from functools import partial
from threading import Thread
import tempfile
import atexit
from http.server import SimpleHTTPRequestHandler, HTTPServer
import functools # <-- Import functools
import subprocess
import os
import threading
import glob
from tkinter import messagebox
from tkinter import filedialog
import pwd

def _find_x_auth_file():
    """Scan running X/XWayland processes for an explicit '-auth <file>'
    argument. Different desktop environments put this file in different
    places -- GNOME/mutter uses a randomly-named file under
    XDG_RUNTIME_DIR, KDE varies, some setups still use ~/.Xauthority --
    so reading it straight from the running server's own command line
    works regardless of which convention is in play, instead of guessing
    a single hardcoded path.
    """
    try:
        for proc in psutil.process_iter(['name', 'cmdline']):
            name = (proc.info.get('name') or '').lower()
            if name not in ('xwayland', 'xorg', 'x'):
                continue
            cmdline = proc.info.get('cmdline') or []
            for i, arg in enumerate(cmdline):
                if arg == '-auth' and i + 1 < len(cmdline):
                    candidate = cmdline[i + 1]
                    if os.path.isfile(candidate):
                        return candidate
    except Exception:
        pass
    return None


def _grant_root_x_access(sudo_user, display):
    """Best-effort: ask the owning user's own session to explicitly allow
    root to connect, via xhost. This is what actually fixes things on
    compositors that run XWayland with no -auth file at all -- niri (via
    xwayland-satellite), sway, and other wlroots-based setups -- where
    access is otherwise restricted purely by UID and there's no cookie
    file to point XAUTHORITY at in the first place. xhost has to be run
    as the already-authorized user, not as root, so this shells out via
    'sudo -u' back to the original user.
    """
    try:
        result = subprocess.run(
            ['sudo', '-u', sudo_user, 'env', f'DISPLAY={display}',
             'xhost', '+si:localuser:root'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _ensure_x11_access_for_root():
    """Make the GUI able to open when this app is run as root via sudo,
    regardless of desktop environment or compositor. Tk itself is an
    X11-only toolkit -- under Wayland it can only work through XWayland --
    and root has no automatic permission to connect to another user's
    display. Three approaches are layered since no single one covers
    every setup:

      1. Point XAUTHORITY at the invoking user's own ~/.Xauthority, if it
         exists (classic X11, and some XWayland setups).
      2. If that file doesn't exist, look at the actual running
         X/XWayland process for an explicit -auth <file> argument and use
         that instead (covers GNOME/mutter, KDE/kwin, and similar where
         the cookie file lives somewhere other than ~/.Xauthority).
      3. Regardless of whether either of the above found anything, also
         try 'xhost +si:localuser:root' as the invoking user -- this is
         the one that actually works on compositors that run XWayland
         with no auth file at all (niri, sway, other wlroots-based
         setups).

    Every step is best-effort and silently continues on failure. If
    nothing here works, CTk() will still raise TclError, and __init__
    catches that with an actionable message instead of a raw traceback.
    """
    if os.geteuid() != 0 or 'SUDO_USER' not in os.environ:
        return

    sudo_user = os.environ['SUDO_USER']
    display = os.environ.get('DISPLAY', ':0')

    if not os.environ.get('XAUTHORITY'):
        try:
            user_home = pwd.getpwnam(sudo_user).pw_dir
            candidate = os.path.join(user_home, '.Xauthority')
            if os.path.isfile(candidate):
                os.environ['XAUTHORITY'] = candidate
            else:
                found = _find_x_auth_file()
                if found:
                    os.environ['XAUTHORITY'] = found
        except KeyError:
            pass

    _grant_root_x_access(sudo_user, display)


_ensure_x11_access_for_root()

try:
    from extra import create_multi_unit_window
    MULTI_UNIT_AVAILABLE = True
except ImportError:
    MULTI_UNIT_AVAILABLE = False
    print("Multi-unit functionality not available (extra.py not found)")
VERSION = "6.1.2"  
# --- Embedded DMI Scripts ---

# FRU_flash_v2.sh content
FRU_FLASH_SCRIPT_CONTENT = r"""
#!/usr/bin/env bash
#
# flash_fru.sh — Generate and flash FRU data to an I2C EEPROM (24C02 @ 0x50)
#
# Usage:
#   sudo ./flash_fru.sh --sku <SKU> --asmid <ASMID> [--mfg "SimplyNuc"] [--i2c-bus 1] [--dry-run]
#
# Example:
#   sudo ./flash_fru.sh --sku S1M0-F01-ABCD --asmid IP0DC4250840001 --mfg "SimplyNuc"
#
# Notes:
# - Requires root (writes to /sys and /sys/bus/i2c)
# - Requires: frugy, uuidgen, md5sum, dd, grep, sed, rev, cut
# - Default I2C bus is 1; override with --i2c-bus N
# - Uses 24c02 at 0x50;

set -Eeuo pipefail

# ----------------------- config / globals -----------------------
I2C_BUS=1                 # default; can be overridden via --i2c-bus
I2C_ADDR_HEX=0x50         # 24c02 at 0x50
EE_TYPE="24c02"
TMP_DIR="/tmp"
YML_NAME="$TMP_DIR/fru.yml"
BIN_NAME="$TMP_DIR/fru.bin"
SUM_NAME="$TMP_DIR/FRUMD5"
DRY_RUN=0

# ----------------------- logging helpers ------------------------
echo_BMC() {
  echo "[BMC] $*" >&2
}

die() {
  echo_BMC "ERROR: $*"
  exit 1
}

cleanup() {
  # Keep artifacts by default (useful for audit); uncomment to remove
  # rm -f "$YML_NAME" "$BIN_NAME" "$SUM_NAME" || true
  :
}
trap cleanup EXIT

# ----------------------- usage ---------------------------------
usage() {
  cat <<EOF
Usage:
  sudo $0 --sku <SKU> --asmid <ASMID> [--mfg "SimplyNuc"] [--i2c-bus 1] [--dry-run]

Required:
  --sku <SKU>       Device SKU (e.g., S1M0-F01-XXXX, S0M1-XXXX, V3B-XXXX, R8B-XXXX)
  --asmid <ASMID>   Assembly ID containing IP/AK serial (e.g., IP0DC4250840001)

Optional:
  --mfg <NAME>      Manufacturer string (default: "SimplyNuc")
  --i2c-bus <N>     I2C bus number to use (default: 1)
  --dry-run         Generate files, skip writing to EEPROM
  -h | --help       Show this help

This script:
  1) Ensures /sys i2c node exists for ${EE_TYPE} at ${I2C_ADDR_HEX} on i2c-\$bus
  2) Generates FRU YAML and binary via 'frugy'
  3) Writes FRU binary to the EEPROM at /sys/bus/i2c/devices/i2c-\$bus/\$bus-0050/eeprom
EOF
}

# ----------------------- arg parsing ----------------------------
if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

SKU=""
ASMID=""
MFG="SimplyNuc"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sku)        SKU="${2:-}"; shift 2 ;;
    --asmid)      ASMID="${2:-}"; shift 2 ;;
    --mfg)        MFG="${2:-}"; shift 2 ;;
    --i2c-bus)    I2C_BUS="${2:-}"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "Unknown argument: $1 (use --help)" ;;
  esac
done

I2C_BUS=1

[[ -n "$SKU"   ]] || { usage; die "Missing --sku"; }
[[ -n "$ASMID" ]] || { usage; die "Missing --asmid"; }

# ----------------------- preflight checks -----------------------
if [[ $EUID -ne 0 ]]; then
  die "Must be run as root (needs access to /sys). Try: sudo $0 ..."
fi

require_bin() {
  command -v "$1" >/dev/null 2>&1 || die "Required tool '$1' not found in PATH"
}
require_bin frugy
require_bin uuidgen
require_bin md5sum
require_bin dd
require_bin grep
require_bin sed
require_bin rev
require_bin cut

# ----------------------- resolve device path --------------------
I2C_DEV_DIR="/sys/bus/i2c/devices/i2c-${I2C_BUS}"
NEW_DEV_PATH="${I2C_DEV_DIR}/new_device"
EE_NODE="/sys/bus/i2c/devices/i2c-${I2C_BUS}/${I2C_BUS}-0050"

EEPROM_FILE="${EE_NODE}/eeprom"

[[ -d "$I2C_DEV_DIR" ]] || die "I2C bus $I2C_BUS not present at $I2C_DEV_DIR"

# Add device if not present
if [[ ! -e "$EEPROM_FILE" ]]; then
  echo_BMC "Adding I2C device ${EE_TYPE} at ${I2C_ADDR_HEX} on i2c-${I2C_BUS}"
  echo "${EE_TYPE} ${I2C_ADDR_HEX}" > "$NEW_DEV_PATH" || die "Failed to create I2C device node"
else
  echo_BMC "EEPROM node already present at $EEPROM_FILE"
fi

# Verify EEPROM path
[[ -e "$EEPROM_FILE" ]] || die "EEPROM file not found at $EEPROM_FILE"

# ----------------------- derive fields --------------------------
echo_BMC "Input SKU: $SKU"
echo_BMC "Input ASMid: $ASMID"
echo_BMC "Manufacturer: $MFG"

# Map SKU -> product name
device="$(echo -n "$SKU" | grep -ioE 'S1M0|S0M1|S.M.|V3B|R8B|EE[0-9]{4}|ME[0-9]{4}' | head -n1 || true)"
pname=""
case "$device" in
  S1M0|EE2000|ME2000)   pname="EE-2000" ;;
  S0M1|EE2100|ME2100)   pname="EE-2100" ;;
  EE2200|ME2200)        pname="EE-2200" ;;
  EE2300|ME2300)        pname="EE-2300" ;;
  V3B|EE3000|ME3000)    pname="EE-3000" ;;
  EE3100|ME3100)        pname="EE-3100" ;;
  R8B|EE3200|ME3200)    pname="EE-3200" ;;
  *)    ;;
esac
[[ -n "$pname" ]] || die "Unsupported or unrecognized SKU device code in '$SKU'"

# Serial from ASMid (IPnnn… or AKnnn…); keep last 15 chars of the match
serial="$ASMID"
if [[ -z "$serial" ]]; then
  serial="$ASMID"
fi
[[ -n "$serial" ]] || die "Could not extract serial (IPxxxx or AKxxxx) from ASMid '$ASMID'"
serial="$(echo -n "$serial" | rev | cut -c1-15 | rev)"

date_now="$(date +'%Y-%m-%dT%H:%M:%S')"
uuid_val="$(uuidgen)"

# Fan suffix if SKU contains F01
fan_suffix=""
if [[ "$SKU" == *"F01"* ]]; then
  fan_suffix="-FAN"
  echo_BMC "SKU includes fans (F01 detected)"
fi

echo_BMC "Resolved product name: ${pname}${fan_suffix}"
echo_BMC "Derived serial: $serial"
echo_BMC "Timestamp: $date_now"
echo_BMC "UUID: $uuid_val"

# ----------------------- build YAML -----------------------------
echo_BMC "Generating FRU YAML at $YML_NAME"
{
  printf "BoardInfo:\n"
  printf "  manufacturer: \"%s\"\n"                "$MFG"
  printf "  product_name: \"%s%s\"\n"              "$pname" "$fan_suffix"
  printf "  serial_number: \"%s\"\n"               "$serial"
  printf "  part_number: \"SN%s\"\n"               "$serial"
  printf "  mfg_date_time: \"%s\"\n"               "$date_now"

  printf "ProductInfo:\n"
  printf "  manufacturer: \"%s\"\n"                "$MFG"
  printf "  product_name: \"%s%s\"\n"              "$pname" "$fan_suffix"
  printf "  part_number: \"SN%s\"\n"               "$serial"
  printf "  serial_number: \"%s\"\n"               "$serial"

  printf "MultirecordArea:\n"
  printf "%s\n" "- type: MgmtAccessRecord"
  printf "  id: \"%s\"\n"                          "sys_unique_id"
  printf "  blob: \"%s\"\n"                        "$uuid_val"
} > "$YML_NAME"

echo_BMC "FRU YAML contents:"
cat "$YML_NAME"

# ----------------------- generate BIN ---------------------------
echo_BMC "Generating FRU binary at $BIN_NAME"
frugy "$YML_NAME" -o "$BIN_NAME" -e 256 || die "frugy failed"

[[ -e "$BIN_NAME" ]] || die "FRU binary not created: $BIN_NAME"

md5sum "$BIN_NAME" > "$SUM_NAME"
echo_BMC "MD5 of FRU binary:"

# ----------------------- flash to EEPROM ------------------------
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo_BMC "[DRY-RUN] Skipping write to $EEPROM_FILE"
  exit 0
fi

echo_BMC "Flashing FRU to EEPROM at $EEPROM_FILE"
# Use status=progress for visibility; notrunc to avoid truncation issues on some sysfs eeprom drivers
dd if="$BIN_NAME" of="$EEPROM_FILE" bs=1 count=256

echo_BMC "FRU binary flashed successfully."

echo_BMC "Reading FRU from EEPROM"
dd if="$EEPROM_FILE" of="$BIN_NAME" bs=1 count=256

expectedFRUmd5=$(awk '{print $1}' "$SUM_NAME")
actualFRUmd5=$(md5sum "$BIN_NAME" | awk '{print $1}')

if [ "$actualFRUmd5" != "$expectedFRUmd5" ]; then
    die "FRU binaries are not the same..."
fi

echo_BMC "Done, checksums were validated successfully."
"""
stop_event = threading.Event()
# --- Global HTTP Server Variable ---
http_server = None
server_lock = threading.Lock()
temp_dir = tempfile.gettempdir()

# --- DMI Flasher Utility Functions ---

def read_serial_data_sync(ser, command, timeout=10, output_callback=None, eol=b'\n'):
    """
    Synchronous serial data reading function.
    Sends a command, then reads until a prompt or timeout.
    """
    try:
        ser.write(command)
        if output_callback:
            # Try to show the command, handling potential bytes/str issues
            try:
                cmd_str = command.decode('utf-8').strip()
                if cmd_str:
                    output_callback(f"# {cmd_str}")
            except UnicodeDecodeError:
                output_callback(f"# [sent {len(command)} bytes]")
        
        ser.flush()
        
        line_buffer = b""
        full_response = b""
        start_time = time.time()
        
        prompt_markers = [b'root@', b'# ', b'> '] # Common prompts
        
        while time.time() - start_time < timeout:
            if ser.in_waiting > 0:
                new_byte = ser.read(1)
                if not new_byte:
                    continue
                
                line_buffer += new_byte
                full_response += new_byte
                
                # Check for end-of-line
                if new_byte == eol:
                    if output_callback:
                        try:
                            output_callback(line_buffer.decode('utf-8').strip())
                        except UnicodeDecodeError:
                            output_callback(f"[raw bytes: {line_buffer.hex()}]")
                    line_buffer = b"" # Reset line buffer
                
                # Check for prompt
                if any(marker in full_response for marker in prompt_markers):
                    if output_callback and line_buffer:
                        try:
                            output_callback(line_buffer.decode('utf-8').strip())
                        except UnicodeDecodeError:
                             output_callback(f"[raw bytes: {line_buffer.hex()}]")
                    return full_response.decode('utf-8', errors='ignore')

            else:
                time.sleep(0.05)
        
        # Timeout occurred
        if output_callback:
            output_callback(f"[Timeout after {timeout}s]")
            if line_buffer:
                try:
                    output_callback(line_buffer.decode('utf-8').strip())
                except UnicodeDecodeError:
                     output_callback(f"[raw bytes: {line_buffer.hex()}]")
        return full_response.decode('utf-8', errors='ignore')

    except serial.SerialException as e:
        if output_callback:
            output_callback(f"Serial Error: {e}")
        return f"Serial Error: {e}"
    except Exception as e:
        if output_callback:
            output_callback(f"Error in read_serial_data: {e}")
        return f"Error: {e}"

def start_server_dmi(directory, port, callback_output):
    """Starts a simple HTTP server in a separate thread."""
    global http_server
    with server_lock:
        if http_server:
            callback_output("Server is already running.")
            return http_server
        
        try:
            # --- FIX ---
            # Create a request handler that is bound to the target directory
            # This avoids using os.chdir, which is thread-unsafe.
            Handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
            
            http_server = HTTPServer(('0.0.0.0', port), Handler)
            
            threading.Thread(target=http_server.serve_forever, daemon=True).start()
            callback_output(f"Serving files from {directory} on port {port}")
            # No os.chdir or os.chdir back is needed
            return http_server
        except Exception as e:
            callback_output(f"Failed to start server: {e}")
            http_server = None
            return None

def stop_server_dmi(callback_output):
    """Stops the global HTTP server."""
    global http_server
    with server_lock:
        if http_server:
            try:
                http_server.shutdown()
                http_server.server_close()
                callback_output("Server has been stopped.")
            except Exception as e:
                callback_output(f"Error stopping server: {e}")
            finally:
                http_server = None
        else:
            callback_output("Server is not running.")
            
@atexit.register
def cleanup_server_on_exit():
    """Ensure server is stopped when the application exits."""
    global http_server
    if http_server:
        print("Cleaning up HTTP server on exit...")
        http_server.shutdown()
        http_server.server_close()

async def transfer_and_run_script(
    serial_device,
    host_ip,
    script_content,
    script_name,
    script_args,
    callback_output,
    callback_progress
):
    """
    Transfers a script to the BMC via HTTP/curl and then executes it.
    """
    global temp_dir
    port = 8000 # Use a non-privileged port to avoid sudo
    httpd = None
    ser = None
    temp_script_path = ""

    try:
        callback_progress(0.1)
        callback_output(f"Preparing to transfer {script_name}...")

        # 1. Write the script content to a temporary file
        try:
            # We create the temp file in our serving directory
            temp_script_path = os.path.join(temp_dir, script_name)
            with open(temp_script_path, 'w') as f:
                f.write(script_content)
            callback_output(f"Temporary script created at {temp_script_path}")
        except Exception as e:
            callback_output(f"Failed to create temporary script file: {e}")
            return

        # 2. Start the HTTP server
        callback_progress(0.2)
        httpd = start_server_dmi(temp_dir, port, callback_output)
        if not httpd:
            callback_output("Failed to start local HTTP server. Aborting.")
            return

        # 3. Open Serial Connection
        callback_progress(0.3)
        try:
            ser = serial.Serial(serial_device, 115200, timeout=1)
            ser.dtr = True
            callback_output(f"Serial connection open on {serial_device}")
        except serial.SerialException as e:
            callback_output(f"Failed to open serial port {serial_device}: {e}")
            return
            
        # Ensure we are at a prompt
        await asyncio.to_thread(read_serial_data_sync, ser, b'\n', 1, callback_output)

        # 4. Transfer script to BMC using curl
        callback_progress(0.4)
        bmc_script_path = f"/tmp/{script_name}"
        url = f"http://{host_ip}:{port}/{script_name}"
        curl_command = f"curl -o {bmc_script_path} {url}\n".encode('utf-8')
        
        callback_output(f"Transferring script to BMC: {url} -> {bmc_script_path}")
        await asyncio.to_thread(read_serial_data_sync, ser, curl_command, 20, callback_output)
        callback_output("Transfer complete.")

        # 5. Make script executable
        callback_progress(0.6)
        chmod_command = f"chmod +x {bmc_script_path}\n".encode('utf-8')
        callback_output(f"Making script executable: chmod +x {bmc_script_path}")
        await asyncio.to_thread(read_serial_data_sync, ser, chmod_command, 5, callback_output)
        callback_output("Permissions set.")

        # 6. Execute the script
        callback_progress(0.8)
        exec_command_str = f"{bmc_script_path} {script_args}\n"
        exec_command = exec_command_str.encode('utf-8')
        callback_output(f"Executing script on BMC: {exec_command_str.strip()}")
        
        # Use a longer timeout for script execution
        await asyncio.to_thread(read_serial_data_sync, ser, exec_command, 60, callback_output)
        callback_output(f"Script {script_name} execution finished.")

        callback_progress(1.0)

    except Exception as e:
        callback_output(f"An error occurred: {e}")
        callback_progress(0)
    finally:
        # 7. Clean up
        if ser and ser.is_open:
            ser.close()
            callback_output("Serial connection closed.")
        
        if httpd:
            stop_server_dmi(callback_output)
            
        if temp_script_path and os.path.exists(temp_script_path):
            try:
                os.remove(temp_script_path)
                callback_output(f"Temporary script {temp_script_path} removed.")
            except Exception as e:
                callback_output(f"Warning: could not remove temp script: {e}")
        
        # Reset progress bar after a delay
        await asyncio.sleep(2)
        callback_progress(0)


# --- Main Application Class ---

class FileSelectionHelper:
    """Helper class to standardize and simplify file/directory selection dialogs"""

    @staticmethod
    def get_real_home():
        """Gets the actual user's home directory, even if running under sudo"""
        import pwd
        import os
        
        # Try to get the real user if the app was launched via sudo
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            try:
                return pwd.getpwnam(sudo_user).pw_dir
            except KeyError:
                pass
        
        # Standard home expansion
        home = os.path.expanduser("~")
        
        # If it resolved to root, try to guess the real user directory in /home
        if home == "/root" or not os.path.isdir(home):
            try:
                users = [d for d in os.listdir("/home") if os.path.isdir(os.path.join("/home", d))]
                if len(users) == 1:  # If there's only one user on the machine, it's a safe bet
                    return os.path.join("/home", users[0])
            except Exception:
                pass
            return "/home"
            
        return home

    @staticmethod
    def _default_dir(last_dir):
        """Return last_dir if valid, otherwise default to the real user's home"""
        # Ensure we don't accidentally default to the root directory
        if last_dir and os.path.isdir(last_dir) and last_dir != "/root":
            return last_dir
        return FileSelectionHelper.get_real_home()

    @staticmethod
    def select_file(parent, title, last_dir, file_filter=None):
        """File selection using tkinter dialog"""
        filetypes = []
        if file_filter:
            if '|' in file_filter:
                label = file_filter.split('|')[0].strip()
                pattern = file_filter.split('|')[1].strip()
                filetypes = [(label, pattern), ("All files", "*.*")]
            else:
                filetypes = [("Files", file_filter), ("All files", "*.*")]
        else:
            filetypes = [("All files", "*.*")]

        parent.update_idletasks()
        parent.lift()

        file_path = filedialog.askopenfilename(
            parent=parent,
            title=title,
            initialdir=FileSelectionHelper._default_dir(last_dir),
            filetypes=filetypes
        )
        return file_path or ""

    @staticmethod
    def select_directory(parent, title, last_dir):
        """Directory selection using tkinter dialog"""
        parent.update_idletasks()
        parent.lift()

        directory = filedialog.askdirectory(
            parent=parent,
            title=title,
            initialdir=FileSelectionHelper._default_dir(last_dir),
        )
        return directory or ""

    @staticmethod
    def _show_entry_dialog(parent, title, default_value, message, file_filter=None):
        """Fallback manual path entry dialog"""
        dialog = ctk.CTkToplevel(parent)
        dialog.title(title)
        dialog.geometry("600x200")
        dialog.attributes('-topmost', True)
        dialog.resizable(True, True)

        # Initialize to the real home if the default is empty or root
        if not default_value or default_value == "/root":
            default_value = FileSelectionHelper.get_real_home()

        path_var = ctk.StringVar(value=default_value)

        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        filter_info = f"{message}\n\nExpected file type: {file_filter}" if file_filter else message
        ctk.CTkLabel(main_frame, text=filter_info, wraplength=500).pack(pady=10)

        entry = ctk.CTkEntry(main_frame, textvariable=path_var, width=500, height=32)
        entry.pack(pady=10, fill="x")
        entry.focus_set()

        browse_frame = ctk.CTkFrame(main_frame)
        browse_frame.pack(fill="x", pady=5)

        def browse_for_path():
            parent.update_idletasks()
            parent.lift()
            initial = FileSelectionHelper._default_dir(path_var.get())
            if "directory" in message.lower():
                result = filedialog.askdirectory(title=f"Browse for {title}", initialdir=initial)
                if result:
                    path_var.set(result)
            else:
                result = filedialog.askopenfilename(title=f"Browse for {title}", initialdir=initial)
                if result:
                    path_var.set(result)

        def set_to_home():
            """Quick action to set the path to the real home directory"""
            path_var.set(FileSelectionHelper.get_real_home())

        ctk.CTkButton(browse_frame, text="Browse...", command=browse_for_path, width=100).pack(side="right", padx=(5, 0))
        # New quick-action Home button
        ctk.CTkButton(browse_frame, text="🏠 Home", command=set_to_home, width=100, fg_color="#444444", hover_color="#666666").pack(side="right")

        result_path = []

        def on_ok():
            path = path_var.get().strip()
            if path and (os.path.exists(path) or "Enter the full path" in message):
                result_path.append(path)
                dialog.destroy()
            else:
                error_label = ctk.CTkLabel(main_frame, text="⚠️ Path does not exist!", text_color="red")
                error_label.pack(pady=5)
                dialog.after(3000, error_label.destroy)

        def on_cancel():
            dialog.destroy()

        entry.bind('<Return>', lambda e: on_ok())

        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(fill="x", pady=10)
        ctk.CTkButton(button_frame, text="OK", command=on_ok, width=100).pack(side="left", padx=20)
        ctk.CTkButton(button_frame, text="Cancel", command=on_cancel, width=100).pack(side="right", padx=20)

        dialog.update_idletasks()
        width = dialog.winfo_width()
        height = dialog.winfo_height()
        x = (dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (dialog.winfo_screenheight() // 2) - (height // 2)
        dialog.geometry(f'{width}x{height}+{x}+{y}')

        dialog.grab_set()
        dialog.wait_window()

        return result_path[0] if result_path else ""

class FlashAllWindow(ctk.CTkToplevel):
    def __init__(self, parent, bmc_type, app_instance):
        super().__init__(parent)
        self.bmc_type = bmc_type
        self.parent = parent
        self.app_instance = app_instance  # Store the app instance
        self.title("Select Files for Flashing")
        self.geometry("650x560")
        
        # Set parent relationship but DON'T make it modal
        self.transient(parent)
        
        # Initialize variables
        self.firmware_folder = ctk.StringVar()
        self.fip_file = ctk.StringVar()
        self.eeprom_file = ctk.StringVar()
        self.flash_fru_var = ctk.BooleanVar() # For the checkbox
        self.auto_password_compliance_var = ctk.BooleanVar(value=False)
        self.compliance_default_password_var = ctk.StringVar(value="0penBmc123")
        
        # Load previously selected files from config
        self.load_previous_selections()
        
        # Create UI elements
        self._create_ui()
        
        # Position window relative to parent
        self.position_window()

    def load_previous_selections(self):
        """Load previously selected files from app config"""
        # Check if the app has these attributes
        if hasattr(self.app_instance, 'last_flash_all_folder'):
            self.firmware_folder.set(self.app_instance.last_flash_all_folder)
        
        if hasattr(self.app_instance, 'last_flash_all_fip'):
            self.fip_file.set(self.app_instance.last_flash_all_fip)
            
        if hasattr(self.app_instance, 'last_flash_all_eeprom'):
            self.eeprom_file.set(self.app_instance.last_flash_all_eeprom)
            
        # Load checkbox state
        self.flash_fru_var.set(getattr(self.app_instance, 'last_flash_all_do_fru', True))

    def save_selections_to_config(self):
        """Save current selections to app config"""
        if self.firmware_folder.get():
            self.app_instance.last_flash_all_folder = self.firmware_folder.get()
            
        if self.fip_file.get():
            self.app_instance.last_flash_all_fip = self.fip_file.get()
            
        if self.eeprom_file.get():
            self.app_instance.last_flash_all_eeprom = self.eeprom_file.get()
            
        # Save checkbox state
        self.app_instance.last_flash_all_do_fru = self.flash_fru_var.get()
            
        # Save config if the method exists
        if hasattr(self.app_instance, 'save_config'):
            self.app_instance.save_config()

    def position_window(self):
        """Position the window relative to parent"""
        # Update window info before getting sizes
        self.update_idletasks()
        
        # Get window size
        width = self.winfo_width()
        height = self.winfo_height()
        
        # Get parent position and size
        parent_x = self.parent.winfo_rootx()
        parent_y = self.parent.winfo_rooty()
        
        # Calculate position - center on parent
        x = parent_x + 50  # Offset slightly from parent window
        y = parent_y + 50
        
        # Set window position
        self.geometry(f'+{x}+{y}')
    
    def _create_ui(self):
        """Create all UI elements for the flash all window"""
        ctk.CTkLabel(self, text="Firmware Folder (eMMC):").pack(pady=5)
        ctk.CTkEntry(self, textvariable=self.firmware_folder, width=400).pack()
        ctk.CTkButton(self, text="Browse", command=self.select_firmware_folder).pack(pady=5)
        
        ctk.CTkLabel(self, text="FIP File (U-Boot):").pack(pady=5)
        ctk.CTkEntry(self, textvariable=self.fip_file, width=400).pack()
        ctk.CTkButton(self, text="Browse", command=self.select_fip_file).pack(pady=5)
        
        # --- EEPROM Section ---
        
        def toggle_eeprom_widgets():
            """Show or hide EEPROM widgets based on checkbox"""
            if self.flash_fru_var.get():
                self.eeprom_frame.pack(fill="x", pady=0, padx=10)
            else:
                self.eeprom_frame.pack_forget()

        if self.bmc_type != 1:
            ctk.CTkCheckBox(self, text="Flash FRU (EEPROM)?", 
                            variable=self.flash_fru_var, 
                            onvalue=True, offvalue=False,
                            command=toggle_eeprom_widgets).pack(pady=(10, 0))
            
            # Frame to hold the EEPROM file widgets
            self.eeprom_frame = ctk.CTkFrame(self, fg_color="transparent")
            
            ctk.CTkLabel(self.eeprom_frame, text="EEPROM File (FRU):").pack(pady=5)
            ctk.CTkEntry(self.eeprom_frame, textvariable=self.eeprom_file, width=400).pack()
            ctk.CTkButton(self.eeprom_frame, text="Browse", command=self.select_eeprom_file).pack(pady=5)
            
            # Set initial state from loaded config
            # (self.flash_fru_var was set in load_previous_selections)
            toggle_eeprom_widgets() # Show/hide based on loaded value

            # Auto Set Password Compliance - only meaningful (and only
            # shown) when FRU flash is checked: after the reboot, waits
            # 75s, runs the auto-set-password sequence, then 2.5s later
            # sets the IP, then 2.5s later flashes the EEPROM - all
            # handled by bmc.flash_emmc_fru_checked. If unchecked, Flash
            # All falls back to the manual "click Continue after you've
            # logged in yourself" popup.
            self.compliance_frame = ctk.CTkFrame(self, fg_color="transparent")

            ctk.CTkCheckBox(
                self.compliance_frame, text="Auto Set Password Compliance",
                variable=self.auto_password_compliance_var,
            ).pack(anchor="w", padx=5, pady=(5, 0))

            pw_row = ctk.CTkFrame(self.compliance_frame, fg_color="transparent")
            pw_row.pack(fill="x", padx=5, pady=(4, 0))
            ctk.CTkLabel(pw_row, text="Default password:").pack(side="left")
            ctk.CTkEntry(pw_row, textvariable=self.compliance_default_password_var, width=180).pack(side="left", padx=(6, 0))

            def toggle_compliance_frame():
                if self.flash_fru_var.get():
                    self.compliance_frame.pack(fill="x", padx=10, pady=(4, 0))
                else:
                    self.compliance_frame.pack_forget()

            def toggle_all():
                toggle_eeprom_widgets()
                toggle_compliance_frame()

            # Re-wire the FRU checkbox (already packed above) to also
            # toggle the compliance frame's visibility
            for child in self.winfo_children():
                if isinstance(child, ctk.CTkCheckBox) and child.cget("text") == "Flash FRU (EEPROM)?":
                    child.configure(command=toggle_all)
                    break
            toggle_compliance_frame()

        ctk.CTkButton(self, text="Start Flashing", command=self.start_flashing).pack(pady=20)
    
    def select_firmware_folder(self):
        """Select firmware folder for flashing eMMC"""
        # Start with last selected folder or fall back to general firmware dir
        last_dir = self.firmware_folder.get()
        if not last_dir:
            last_dir = app.last_firmware_dir if hasattr(app, 'last_firmware_dir') else os.path.expanduser("~")
        
        folder = FileSelectionHelper.select_directory(
            self, "Select Firmware Folder", last_dir
        )
        
        if folder:
            self.firmware_folder.set(folder)
            # Save to both specific and general folder paths
            self.app_instance.last_flash_all_folder = folder
            if hasattr(app, 'last_firmware_dir'):
                app.last_firmware_dir = os.path.dirname(folder) or folder
            # Save the configuration if method exists
            if hasattr(app, 'save_config'):
                app.save_config()
    
    def select_fip_file(self):
        """Select FIP file for flashing U-Boot with validation"""
        # Start with last selected FIP file directory or fall back to general FIP dir
        last_dir = os.path.dirname(self.fip_file.get()) if self.fip_file.get() else None
        if not last_dir:
            last_dir = app.last_fip_dir if hasattr(app, 'last_fip_dir') else os.path.expanduser("~")
        
        file_path = FileSelectionHelper.select_file(
            self, "Select FIP File", 
            last_dir,
            "FIP Binary files (fip-snuc-*.bin) | fip-snuc-*.bin"
        )
        
        if file_path:
            # Validate filename before accepting
            filename = os.path.basename(file_path)
            allowed_fip_files = {"fip-snuc-nanobmc.bin", "fip-snuc-mos-bmc.bin"}
            
            if filename not in allowed_fip_files:
                self.log_message(f" Invalid FIP file: '{filename}'")
                self.log_message(f"Allowed files: {', '.join(allowed_fip_files)}")
                
                from tkinter import messagebox
                messagebox.showerror(
                    "Invalid FIP File", 
                    f"Invalid FIP file selected: '{filename}'\n\n"
                    f"Only these files are allowed:\n"
                    f"• fip-snuc-nanobmc.bin\n"
                    f"• fip-snuc-mos-bmc.bin\n\n"
                    f"Please select the correct FIP file."
                )
                return  # Don't set the file path
            
            self.fip_file.set(file_path)
            # Save to both specific and general file paths
            self.app_instance.last_flash_all_fip = file_path
            if hasattr(app, 'last_fip_dir'):
                app.last_fip_dir = os.path.dirname(file_path)
            # Save the configuration if method exists
            if hasattr(app, 'save_config'):
                app.save_config()
            
            
    def select_eeprom_file(self):
        """Select EEPROM file for flashing FRU with validation"""
        # Start with last selected EEPROM file directory or fall back to general EEPROM dir
        last_dir = os.path.dirname(self.eeprom_file.get()) if self.eeprom_file.get() else None
        if not last_dir:
            last_dir = app.last_eeprom_dir if hasattr(app, 'last_eeprom_dir') else os.path.expanduser("~")
        
        file_path = FileSelectionHelper.select_file(
            self, "Select EEPROM (FRU) File", 
            last_dir,
            "FRU Binary files (fru.bin) | fru.bin"
        )
        
        if file_path:
            # Validate filename before accepting
            filename = os.path.basename(file_path)
            
            if filename != "fru.bin":
                self.log_message(f" Invalid EEPROM file: '{filename}'")
                self.log_message(f"Required file: 'fru.bin'")
                
                from tkinter import messagebox
                messagebox.showerror(
                    "Invalid EEPROM File", 
                    f"Invalid EEPROM file selected: '{filename}'\n\n"
                    f"Only 'fru.bin' files are allowed for EEPROM flashing.\n\n"
                    f"Please select the correct fru.bin file."
                )
                return  # Don't set the file path
            
            self.eeprom_file.set(file_path)
            # Save to both specific and general file paths
            self.app_instance.last_flash_all_eeprom = file_path
            if hasattr(app, 'last_eeprom_dir'):
                app.last_eeprom_dir = os.path.dirname(file_path)
            # Save the configuration if method exists
            if hasattr(app, 'save_config'):
                app.save_config()
            
            self.log_message(f"✓ Valid EEPROM file selected: {filename}")
    
    def select_eeprom_file(self):
        """Select EEPROM file for flashing FRU"""
        # Start with last selected EEPROM file directory or fall back to general EEPROM dir
        last_dir = os.path.dirname(self.eeprom_file.get()) if self.eeprom_file.get() else None
        if not last_dir:
            last_dir = app.last_eeprom_dir if hasattr(app, 'last_eeprom_dir') else os.path.expanduser("~")
        
        file_path = FileSelectionHelper.select_file(
            self, "Select EEPROM File", 
            last_dir,
            "Binary files (*.bin) | *.bin"
        )
        
        if file_path:
            self.eeprom_file.set(file_path)
            # Save to both specific and general file paths
            self.app_instance.last_flash_all_eeprom = file_path
            if hasattr(app, 'last_eeprom_dir'):
                app.last_eeprom_dir = os.path.dirname(file_path)
            # Save the configuration if method exists
            if hasattr(app, 'save_config'):
                app.save_config()
    
    def start_flashing(self):
        """Start the full flashing sequence"""
        
        # Base validation
        if not self.firmware_folder.get() or not self.fip_file.get():
            messagebox.showerror("Error", "Please select Firmware Folder and FIP File before proceeding.")
            return
        
        # Conditional validation for FRU
        do_flash_fru = self.flash_fru_var.get()
        if self.bmc_type != 1 and do_flash_fru and not self.eeprom_file.get():
            messagebox.showerror("Error", "Please select an EEPROM file (fru.bin) if 'Flash FRU' is checked.")
            return
        
        # Save the selections before starting the thread
        self.save_selections_to_config()

        do_auto_password_compliance = self.auto_password_compliance_var.get() and do_flash_fru
        default_password = self.compliance_default_password_var.get()
        
        # Pass do_flash_fru and compliance settings to the sequence
        threading.Thread(
            target=self.run_flash_sequence,
            args=(do_flash_fru, do_auto_password_compliance, default_password),
            daemon=True,
        ).start()
        self.destroy()  # Close the window when starting the flashing
    
    def run_flash_sequence(self, do_flash_fru, do_auto_password_compliance, default_password):
        """Execute the full flashing sequence by calling the main app's method"""
        firmware_folder = self.firmware_folder.get()
        fip_file = self.fip_file.get()
        eeprom_file = self.eeprom_file.get() if hasattr(self, 'eeprom_file') else None
        bmc_type = self.bmc_type
        
        # Call the main app's method to execute the sequence
        self.app_instance.execute_flash_all(
            firmware_folder,
            fip_file,
            eeprom_file,
            bmc_type,
            do_flash_fru,
            do_auto_password_compliance,
            default_password,
        )
     

class PlatypusApp:

    # Button theme presets. "Default" restores CTk's own built-in blue
    # (captured from the actual theme at startup - see _collect_themable_
    # buttons - rather than hardcoded here, so it always matches whatever
    # CTk's "blue" theme actually resolves to). SNUC Yellow/Blue use the
    # exact brand hex values from SNUC's own asset files (YILLO = #FEDD00,
    # --color-blue = #0075FB).
    BUTTON_THEMES = {
        "SNUC Yellow": {"fg_color": "#FEDD00", "hover_color": "#E0C300", "text_color": "#101820"},
        "SNUC Blue": {"fg_color": "#0075FB", "hover_color": "#005FD1", "text_color": "#FFFFFF"},
    }

    def __init__(self):
        """Initialize the application with auto-opening console"""
        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Create main window with specific class name
        try:
            self.root = ctk.CTk(className="PlatypusApp")  # Set class name during creation
        except tk.TclError as e:
            if os.geteuid() == 0 and 'SUDO_USER' in os.environ:
                print(
                    "\nCould not open a display while running as root "
                    f"(underlying error: {e}).\n"
                    "Automatic fixes were attempted (XAUTHORITY detection, "
                    "xhost) but didn't resolve it on this system.\n"
                    "As a manual workaround, run this once as your normal "
                    f"user ({os.environ['SUDO_USER']}) before using sudo:\n"
                    "    xhost +si:localuser:root\n"
                )
            raise
        self.root.title("Platypus BMC Management - 7.0")
        self.root.geometry("1600x850")  # fallback size if maximizing fails below

        # Open maximized rather than at the fixed size above. 'zoomed' is
        # the normal cross-platform way (Windows, most Linux window
        # managers); '-zoomed' is the X11-specific fallback some window
        # managers require instead. If both fail for some reason, fall
        # back to manually sizing the window to the full screen.
        try:
            self.root.state("zoomed")
        except Exception:
            try:
                self.root.attributes("-zoomed", True)
            except Exception:
                self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
        
        # Initialize variables
        self._init_variables()
        
        # Create configuration directory
        self.config_dir = os.path.expanduser("~/.local/platypus")
        os.makedirs(self.config_dir, exist_ok=True)
        self.CONFIG_FILE = os.path.join(self.config_dir, "platypus_config.json")
        self.SKU_CONFIG_FILE = os.path.join(self.config_dir, "dmi_skus.json")

        # Try to set icon (optional)
        try:
            icon_path = os.path.join(self.config_dir, "platypus_icon.png")
            if os.path.exists(icon_path):
                img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, img)
        except Exception:
            pass  # Continue without icon if there's an error

        # Load saved configuration
        self.load_or_create_skus() # Load SKUs before building UI
        self.load_config()

        # Create main container frame for the UI
        self.main_container = ctk.CTkFrame(self.root)
        self.main_container.pack(fill="both", expand=True, padx=10, pady=10)

        # Split into a left column (all existing controls) and a right
        # column holding the embedded Serial/SOL console panel on top and
        # a system inventory panel (BIOS/BMC version, NICs, drives) below.
        self.main_container.grid_rowconfigure(0, weight=5)
        self.main_container.grid_rowconfigure(1, weight=1, minsize=200)
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(1, weight=1, minsize=480)

        # Actually wire up the resize handler - it was defined but never
        # bound to anything, so the minimum-size enforcement and the
        # console/controls split never actually adapted as the window was
        # resized this whole time.
        self._resize_debounce_job = None
        self.root.bind("<Configure>", self.on_window_resize)

        self.controls_frame = ctk.CTkFrame(self.main_container)
        self.controls_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 5))

        self.console_panel = EmbeddedConsole(
            self.main_container,
            get_serial_device=self.serial_device.get,
            get_bmc_ip=self.bmc_ip.get,
            get_password=self.password.get,
            get_username=self.username.get,
            log=self.log_message,
        )
        self.console_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.inventory_panel = InventoryPanel(
            self.main_container,
            get_bmc_ip=self.bmc_ip.get,
            get_username=self.username.get,
            get_password=self.password.get,
            log=self.log_message,
            bmc_ip_var=self.bmc_ip,
            on_ready=lambda ready: self.console_panel.set_external_redfish_ready("inventory", ready),
        )
        self.inventory_panel.grid(row=1, column=1, sticky="nsew", padx=(5, 0), pady=(5, 0))
        
        # Create UI sections in the controls frame
        self.create_connection_and_log_row()
        
        # --- Create Main Tab View ---
        self.main_tab_view = ctk.CTkTabview(self.controls_frame)
        self.main_tab_view.pack(fill="x", expand=False, padx=0, pady=5)
        
        self.main_tab_view.add("BMC Flashing")
        self.main_tab_view.add("FRU Data Flasher")
        self.main_tab_view.add("Virtual Media")
        
        # Get tab frames
        self.bmc_flashing_tab = self.main_tab_view.tab("BMC Flashing")
        self.fru_data_flasher_tab = self.main_tab_view.tab("FRU Data Flasher")
        self.virtual_media_tab = self.main_tab_view.tab("Virtual Media")
        
        # Populate tabs
        self.create_main_flashing_tab(self.bmc_flashing_tab)
        self.create_dmi_flasher_tab(self.fru_data_flasher_tab)
        self.virtual_media_panel = VirtualMediaPanel(
            self.virtual_media_tab,
            get_bmc_ip=self.bmc_ip.get,
            get_username=self.username.get,
            get_password=self.password.get,
            log=self.log_message,
        )
        self.virtual_media_panel.pack(fill="both", expand=True)
        
        self.sensors_panel = SensorsPanel(
            self.controls_frame,
            get_bmc_ip=self.bmc_ip.get,
            get_username=self.username.get,
            get_password=self.password.get,
            log=self.log_message,
            bmc_ip_var=self.bmc_ip,
            on_ready=lambda ready: self.console_panel.set_external_redfish_ready("sensors", ready),
        )
        self.sensors_panel.pack(fill="x", pady=5)
        self.create_progress_section()
        
        # Do an initial refresh of networks
        self.update_ip_dropdown()

        self.active_serial_connections = []
        self.cleanup_timer = None
        
        # Schedule periodic cleanup
        self.schedule_cleanup()

        
        # Bind close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Schedule device refresh and auto-console opening after UI is fully loaded
        self.root.after(500, self.initialize_app)

    def cleanup_zombie_processes(self):
        """Clean up any zombie processes created by the application"""
        try:
            current_pid = os.getpid()
            
            for proc in psutil.process_iter(['pid', 'ppid', 'name', 'status']):
                try:
                    # Clean up child processes that are zombies
                    if (proc.info['ppid'] == current_pid and 
                        proc.info['status'] == psutil.STATUS_ZOMBIE):
                        self.log_message(f"Cleaning up zombie process: {proc.info['pid']}")
                        os.waitpid(proc.info['pid'], os.WNOHANG)
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass
                    
        except Exception as e:
            self.log_message(f"Error cleaning zombie processes: {e}")

    def cleanup_serial_connections(self):
        """Clean up any stale serial connections"""
        try:
            # Remove closed connections from tracking
            self.active_serial_connections = [
                conn for conn in self.active_serial_connections 
                if hasattr(conn, 'is_open') and conn.is_open
            ]
            
            # Log current active connections
            if self.active_serial_connections:
                self.log_message(f"Active serial connections: {len(self.active_serial_connections)}")
                
        except Exception as e:
            self.log_message(f"Error cleaning serial connections: {e}")

    def track_serial_connection(self, serial_conn):
        """Track a serial connection for cleanup"""
        if serial_conn not in self.active_serial_connections:
            self.active_serial_connections.append(serial_conn)
    



    def schedule_cleanup(self):
        """Schedule periodic resource cleanup"""
        self.cleanup_resources()
        # Schedule next cleanup in 5 minutes
        self.cleanup_timer = self.root.after(300000, self.schedule_cleanup)
    
    def cleanup_resources(self):
        """Clean up system resources periodically"""
        try:
            self.log_message("Performing periodic resource cleanup...")
            
            # Clean up zombie processes
            self.cleanup_zombie_processes()
            
            # Clean up orphaned serial connections
            self.cleanup_serial_connections()
            
            # Force garbage collection
            import gc
            gc.collect()
            
            self.log_message("Resource cleanup completed.")
            
        except Exception as e:
            self.log_message(f"Warning: Error during resource cleanup: {e}")
            
    def initialize_app(self):
        """Initialize the app with device refresh"""
        # First refresh devices
        self.log_message("Initializing application...")
        
        # Update device list (also auto-connects the Serial console if
        # exactly one ttyUSB device is found - see refresh_devices())
        devices = self.refresh_devices()
        
        if devices:
            self.log_message("Serial devices detected. Use 'Console' button to open when needed.")
        else:
            self.log_message("No serial devices found. Please connect a device and click 'Refresh'.")


    def on_window_resize(self, event):
        """Handle window resize events to maintain proper layout"""
        # Only process if it's the main window being resized
        if event.widget == self.root:
            # Maintain a reasonable minimum size (wider now that the
            # console panel needs real room to be usable). Cheap, so this
            # part runs immediately rather than being debounced below.
            if event.width < 1100:
                self.root.geometry(f"1100x{event.height}")
            if event.height < 600:
                self.root.geometry(f"{event.width}x600")

            # The column-relayout math itself is comparatively expensive,
            # and Tk fires a flood of <Configure> events (often dozens)
            # while a window is actively being dragged-resized - doing
            # this on every single one of them is what made resizing feel
            # laggy. Debounce it instead: only actually recompute once
            # resizing has paused briefly, not on every intermediate frame.
            if self._resize_debounce_job is not None:
                try:
                    self.root.after_cancel(self._resize_debounce_job)
                except Exception:
                    pass
            self._resize_debounce_job = self.root.after(120, self._apply_resize_layout)

    def _apply_resize_layout(self):
        self._resize_debounce_job = None
        # Adjust column minsizes to keep a roughly even, console-friendly
        # split as the window is resized. Column 0 = controls (left),
        # column 1 = the embedded Serial/SOL console (right).
        try:
            total_width = self.main_container.winfo_width()
            
            if total_width > 0:
                console_width = max(480, int(total_width * 0.45))
                controls_width = total_width - console_width
                
                self.main_container.columnconfigure(0, minsize=controls_width)
                self.main_container.columnconfigure(1, minsize=console_width)
        except (AttributeError, tk.TclError):
            # This can happen during initialization or teardown
            pass

    
    def _init_variables(self):
        """Initialize all application variables"""
        # Button theming - tracks which buttons use the plain default blue
        # color (not a deliberately-colored one like the red Stop/Power
        # Off buttons), so theme switching restyles exactly those. Copied
        # per-instance so filling in "Default" below doesn't mutate the
        # shared class-level dict.
        self._themed_buttons = []
        self._themed_radio_buttons = []
        self._themed_segmented_buttons = []
        self.BUTTON_THEMES = dict(PlatypusApp.BUTTON_THEMES)

        # Auto password setup after a fresh eMMC flash: OpenBMC enforces a
        # mandatory password change on first login with default creds (see
        # bmc.post_flash_password_setup) - this is optional automation for
        # that, off by default.
        self.auto_password_reset_enabled = ctk.BooleanVar(value=False)
        self.post_flash_default_password = ctk.StringVar(value="0penBmc123")

        # Connection settings
        self.username = ctk.StringVar()
        self.password = ctk.StringVar()
        self.bmc_ip = ctk.StringVar()
        self.your_ip = ctk.StringVar()
        self.serial_device = ctk.StringVar()
        self.bmc_type = ctk.IntVar(value=2)
        
        # Operation state
        self.lock_buttons = False
        self.operation_running = False
        self.abort_requested = False
        
        # Flash file
        self.flash_file = None
        
        # Directory history
        self.last_firmware_dir = os.path.expanduser("~")
        self.last_fip_dir = os.path.expanduser("~")
        self.last_eeprom_dir = os.path.expanduser("~")
        
        # Flash All specific paths
        self.last_flash_all_folder = ""
        self.last_flash_all_fip = ""
        self.last_flash_all_eeprom = ""
        self.last_flash_all_do_fru = True # For the checkbox
        
        # --- DMI Flasher Variables ---
        self.fru_sku = ctk.StringVar()
        self.fru_asmid = ctk.StringVar()
        self.fru_mfg = ctk.StringVar(value="Simply NUC")
        self.sku_list = [] # Will be populated by load_or_create_skus
        # Master Home Directory
        self.user_home_dir = ""

    def execute_flash_all(self, firmware_folder, fip_file, eeprom_file=None, bmc_type=2, do_flash_fru=True, do_auto_password_compliance=False, default_password="0penBmc123"):
            """
            Execute the complete flash all sequence using the provided files.
            This method should be called from the FlashAllWindow.
            """
            self.abort_requested = False
            self.log_message("=" * 50)
            self.log_message("FLASH ALL SEQUENCE STARTED")
            self.log_message("=" * 50)
            self.lock_buttons = True
            
            # Determine total steps based on BMC type and if FRU flash is requested
            total_steps = 5 if (bmc_type != 1 and eeprom_file and do_flash_fru) else 4
            current_step = 0
            
            # Step names for better logging
            step_names = {
                1: "Flash eMMC",
                2: "Login to BMC", 
                3: "Set BMC IP",
                4: "Flash U-Boot",
                5: "Flash EEPROM"
            }
            
            def update_overall_progress(step_progress, step_number, step_name):
                """Update overall progress based on current step and its progress"""
                # Each step gets equal weight in the overall progress
                step_weight = 1.0 / total_steps
                overall_progress = ((step_number - 1) * step_weight) + (step_progress * step_weight)
                self.update_progress(overall_progress)
                
                # Log detailed progress updates
                if step_progress == 0.0:
                    self.log_message(f"→ Starting Step {step_number}/{total_steps}: {step_name}")
                elif step_progress == 1.0:
                    overall_percent = int(overall_progress * 100)
                    self.log_message(f"✓ Completed Step {step_number}/{total_steps}: {step_name} (Overall: {overall_percent}%)")
                elif step_progress > 0:
                    step_percent = int(step_progress * 100)
                    overall_percent = int(overall_progress * 100)
                    if step_percent % 25 == 0 or step_percent in [10, 30, 50, 70, 90]:  # Log at key intervals
                        self.log_message(f"  Step {step_number}: {step_percent}% | Overall: {overall_percent}%")
            
            try:
                # Step 1: Flash eMMC 
                current_step = 1
                step_name = step_names[current_step]
                self.log_message(f"\n[STEP {current_step}/{total_steps}] {step_name.upper()}")
                self.log_message("-" * 30)
                
                def emmc_progress_callback(progress):
                    update_overall_progress(progress, current_step, step_name)
                    
                asyncio.run(bmc.flash_emmc2(
                    self.bmc_ip.get(), 
                    firmware_folder, 
                    self.your_ip.get(), 
                    self.bmc_type.get(), 
                    emmc_progress_callback,
                    self.log_message,
                    self.serial_device.get()
                ))
                self.log_message("Running FRU Flash")

                time.sleep(35)

                # Step 2: Flash U-Boot (FIP)
                current_step = 2
                step_name = step_names[current_step]
                self.log_message(f"\n[STEP {current_step}/{total_steps}] {step_name.upper()}")
                self.log_message("-" * 30)
                
                def fip_progress_callback(progress):
                    update_overall_progress(progress, current_step, step_name)
                    
                asyncio.run(bmc.flasher(
                    fip_file, 
                    self.your_ip.get(), 
                    fip_progress_callback,
                    self.log_message, 
                    self.serial_device.get()
                ))
                
                # Step 3: Flash EEPROM (if needed and requested)
                if bmc_type != 1 and eeprom_file and do_flash_fru:
                    
                    # --- REBOOT ---
                    self.log_message("Rebooting system before flashing EEPROM...")
                    try:
                        asyncio.run(bmc.reboot_bmc(
                            self.log_message,
                            self.serial_device.get()
                        ))
                    except Exception as reboot_err:
                        self.log_message(f"Warning: Reboot command failed: {reboot_err}")

                    current_step = 5
                    step_name = step_names[current_step]
                    self.log_message(f"\n[STEP {current_step}/{total_steps}] {step_name.upper()}")
                    self.log_message("-" * 30)

                    def eeprom_progress_callback(progress):
                        update_overall_progress(progress, current_step, step_name)

                    if do_auto_password_compliance:
                        # "Auto Set Password Compliance" is checked - one
                        # call handles the 75s boot wait, the auto-set-
                        # password sequence, setting the IP 2.5s later, and
                        # flashing the EEPROM 2.5s after that.
                        asyncio.run(bmc.flash_emmc_fru_checked(
                            self.username.get(),
                            self.password.get(),
                            default_password,
                            self.bmc_ip.get(),
                            eeprom_file,
                            self.your_ip.get(),
                            eeprom_progress_callback,
                            self.log_message,
                            self.serial_device.get(),
                        ))
                    else:
                        self.log_message("Waiting 75 seconds for system to boot...")
                        time.sleep(75)

                        # Pause and ask the user to handle login — either
                        # manually via the "Auto Set Password" button (for
                        # fresh firmware that enforces a mandatory password
                        # change on first login, which plain login() can't
                        # navigate), or just click Continue if the BMC is
                        # already logged in / doesn't need the change.
                        self.log_message("Waiting for user to complete login...")
                        self._flash_all_login_event = threading.Event()

                        def _show_login_popup():
                            win = ctk.CTkToplevel(self.root)
                            win.title("Flash All - Login Required")
                            win.geometry("480x200")
                            win.transient(self.root)
                            win.attributes("-topmost", True)

                            ctk.CTkLabel(
                                win,
                                text=(
                                    "The BMC has rebooted and should be at the login prompt.\n\n"
                                    "If this is fresh firmware, use the 'Auto Set Password'\n"
                                    "button in Flashing Operations to log in and set the\n"
                                    "password, then click Continue.\n\n"
                                    "If already logged in, just click Continue."
                                ),
                                justify="left",
                            ).pack(padx=20, pady=(20, 16))

                            def _continue():
                                win.destroy()
                                self._flash_all_login_event.set()

                            ctk.CTkButton(win, text="Continue", width=120, command=_continue).pack(pady=(0, 16))

                            win.protocol("WM_DELETE_WINDOW", _continue)

                        self.root.after(0, _show_login_popup)
                        self._flash_all_login_event.wait()
                        self.log_message("User confirmed login complete. Continuing...")

                        self.log_message("Waiting 5 seconds before setting IP...")
                        time.sleep(5)
                        
                        self.log_message("Setting BMC IP...")
                        asyncio.run(set_ip(
                            self.bmc_ip.get(), 
                            lambda p: None, # Dummy callback to prevent progress bar jumping
                            self.log_message, 
                            self.serial_device.get(),
                            self.username.get(),
                            self.password.get(),
                        ))

                        self.log_message("Waiting 2 seconds before initiating EEPROM flash...")
                        time.sleep(2)

                        asyncio.run(bmc.flash_eeprom(
                            eeprom_file, 
                            self.your_ip.get(), 
                            eeprom_progress_callback,
                            self.log_message, 
                            self.serial_device.get(),
                            self.username.get(),
                            self.password.get(),
                        ))

                    self.log_message("Rebooting system after EEPROM flash...")
                    try:
                        asyncio.run(bmc.reboot_bmc(
                            self.log_message,
                            self.serial_device.get()
                        ))
                    except Exception as reboot_err:
                        self.log_message(f"Warning: Reboot command failed: {reboot_err}")
                elif bmc_type != 1:
                    self.log_message(f"\n[STEP 5/{total_steps}] Skipping EEPROM Flash (as requested).") 
                    try:
                        asyncio.run(bmc.reboot_bmc(
                            self.log_message,
                            self.serial_device.get()
                        ))
                    except Exception as reboot_err:
                        self.log_message(f"Warning: Reboot command failed: {reboot_err}")
                
                # Complete - set progress to 100%
                self.update_progress(1.0)
                self.log_message("\n" + "=" * 50)
                self.log_message(" FLASH ALL SEQUENCE COMPLETED SUCCESSFULLY!") 
                self.log_message("=" * 50)
                
                # Reset progress after a brief delay
                def reset_progress():
                    import time
                    time.sleep(3)
                    self.update_progress(0)
                
                threading.Thread(target=reset_progress, daemon=True).start()
                
            except Exception as e:
                self.log_message(f"\n ERROR during Flash All sequence at Step {current_step}: {str(e)}")
                self.log_message("=" * 50)
                # Reset progress on error
                self.update_progress(0)
            finally:
                self.lock_buttons = False
        
    def _create_ui(self):
        """Create all UI sections with reduced vertical spacing"""
        self.create_connection_section()
        self.create_bmc_operations_section()
        self.create_flashing_operations_section()
        self.create_log_section()
        self.create_progress_section()

    def load_or_create_skus(self):
        """Loads SKUs from dmi_skus.json or creates it with defaults."""
        
        # Define the default list of SKUs
        default_skus = [
            "EE3000", "EE3100", "EE3200", "EE2000",
            "EE2100", "EE2200", "EE2300"
        ]
        
        try:
            if not os.path.exists(self.SKU_CONFIG_FILE):
                self.log_message(f"SKU config not found. Creating {self.SKU_CONFIG_FILE}...")
                with open(self.SKU_CONFIG_FILE, 'w') as f:
                    json.dump({"skus": default_skus}, f, indent=2)
                self.sku_list = default_skus
            else:
                with open(self.SKU_CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    loaded_skus = data.get("skus", default_skus)
                
                # --- MIGRATION LOGIC ---
                # Check if any loaded SKU contains a dash, indicating an old format
                if any('-' in sku for sku in loaded_skus):
                    self.log_message("Old SKU format detected. Migrating to new format...")
                    self.sku_list = default_skus
                    # Overwrite the old file with the new format
                    with open(self.SKU_CONFIG_FILE, 'w') as f:
                        json.dump({"skus": default_skus}, f, indent=2)
                    self.log_message(f"Updated {self.SKU_CONFIG_FILE} with new SKU list.")
                else:
                    # No migration needed, use the loaded list
                    self.sku_list = loaded_skus
                    
        except Exception as e:
            self.log_message(f"Error loading SKU config: {e}. Using defaults.")
            self.sku_list = default_skus
            
    def load_config(self):
        """Load saved configuration from file"""
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r') as config_file:
                    try:
                        config = json.load(config_file)
                        self.username.set(config.get("username", ""))
                        self.password.set(config.get("password", ""))
                        self.bmc_ip.set(config.get("bmc_ip", ""))
                        self.your_ip.set(config.get("your_ip", ""))
                        
                        # Load last directory locations
                        self.last_firmware_dir = config.get("last_firmware_dir", os.path.expanduser("~"))
                        self.last_fip_dir = config.get("last_fip_dir", os.path.expanduser("~"))
                        self.last_eeprom_dir = config.get("last_eeprom_dir", os.path.expanduser("~"))
                        self.user_home_dir = config.get("user_home_dir", "")
                        
                        # Load Flash All specific paths
                        self.last_flash_all_folder = config.get("last_flash_all_folder", "")
                        self.last_flash_all_fip = config.get("last_flash_all_fip", "")
                        self.last_flash_all_eeprom = config.get("last_flash_all_eeprom", "")
                        self.last_flash_all_do_fru = config.get("last_flash_all_do_fru", True)
                        
                        # --- Load DMI Flasher Config ---
                        self.fru_sku.set(config.get("last_sku", ""))
                        self.fru_asmid.set(config.get("last_asmid", ""))
                        self.fru_mfg.set(config.get("last_mfg", "Simply NUC"))
                        
                    except json.JSONDecodeError:
                        print(f"Warning: Config file {self.CONFIG_FILE} is not valid JSON. Using default values.")
                        self.save_config()
            else:
                print(f"Config file {self.CONFIG_FILE} not found. Creating with default values.")
                self.save_config()
        except Exception as e:
            print(f"Error loading configuration: {e}")
            # Continue with defaults - don't let this stop the application
            pass


    def force_close_port_80(self):
        """Close any other service listening on port 80 (see network.free_port)."""
        free_port(80, self.log_message)

    def save_config(self):
        """Save current configuration to file"""
        config = {
            "username": self.username.get(),
            "password": self.password.get(),
            "bmc_ip": self.bmc_ip.get(),
            "your_ip": self.your_ip.get(),
            
            # Save last directory locations
            "last_firmware_dir": self.last_firmware_dir,
            "last_fip_dir": self.last_fip_dir,
            "last_eeprom_dir": self.last_eeprom_dir,
            
            # Save Flash All specific paths
            "last_flash_all_folder": getattr(self, 'last_flash_all_folder', ""),
            "last_flash_all_fip": getattr(self, 'last_flash_all_fip', ""),
            "last_flash_all_eeprom": getattr(self, 'last_flash_all_eeprom', ""),
            "last_flash_all_do_fru": getattr(self, 'last_flash_all_do_fru', True),
            
            # --- Save DMI Flasher Config ---
            "last_sku": self.fru_sku.get(),
            "last_asmid": self.fru_asmid.get(),
            "last_mfg": self.fru_mfg.get(),

            # Save Master home
            "user_home_dir": getattr(self, 'user_home_dir', ""),
        }
        try:
            with open(self.CONFIG_FILE, 'w') as config_file:
                json.dump(config, config_file)
        except Exception as e:
            print(f"Error saving configuration: {e}")

    def on_close(self):
        """Handle application closing with better cleanup"""
        self.log_message("Application closing - performing cleanup...")
        
        # Cancel cleanup timer
        if self.cleanup_timer:
            self.root.after_cancel(self.cleanup_timer)

        # Disconnect the embedded console panel (serial or SOL SSH session)
        try:
            if hasattr(self, "console_panel"):
                self.console_panel.disconnect()
        except Exception:
            pass

        # Clean up all serial connections
        for conn in self.active_serial_connections:
            try:
                if hasattr(conn, 'close') and hasattr(conn, 'is_open') and conn.is_open:
                    conn.close()
            except:
                pass
        
        # Clean up processes
        self.cleanup_minicom_processes()
        self.force_close_port_80()
        self.cleanup_zombie_processes()
        
        # Stop DMI server if running
        stop_server_dmi(self.log_message)
        
        # Save configuration
        self.save_config()
        
        # Destroy the main window
        self.root.destroy()
        
        # Force exit if needed
        try:
            os._exit(0)
        except:
            pass

    def create_connection_and_log_row(self):
        """Connection Settings sits at a compact, content-sized width (see
        create_connection_section), which leaves empty space next to it in
        the left column - put the Log panel there instead of wasting it."""
        row = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        row.pack(fill="x", pady=5)

        self.create_connection_section(row)
        self.create_log_section(row)

    def create_connection_section(self, parent_frame):
        """Create the connection settings section with optimized spacing"""
        # No fill="x" here (unlike other sections): letting the section
        # size itself to its own content, rather than stretching to match
        # the full width of its row, is what keeps it narrow. Its
        # children still use fill="x" *relative to this frame*, so they
        # correctly fill whatever width the section ends up needing.
        section = ctk.CTkFrame(parent_frame)
        section.pack(side="left", anchor="n")
        
        ctk.CTkLabel(section, text="Connection Settings", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)

        # Button theme selector - restyles the app's plain blue buttons
        # (not the deliberately red/green special-purpose ones).
        theme_frame = ctk.CTkFrame(section, fg_color="transparent")
        theme_frame.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(theme_frame, text="Button Theme:").pack(side="left", padx=(0, 8))
        ctk.CTkSegmentedButton(
            theme_frame,
            values=["Default", "SNUC Yellow", "SNUC Blue"],
            command=self.apply_button_theme,
        ).pack(side="left")

        # Serial Device - made more compact
        device_frame = ctk.CTkFrame(section)
        device_frame.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(device_frame, text="Serial Device:").pack(side="left", padx=5)
        
        # Create the dropdown with an empty list initially
        self.serial_dropdown = ctk.CTkComboBox(device_frame, variable=self.serial_device, values=[], height=28)
        self.serial_dropdown.pack(side="left", expand=True, fill="x", padx=5)
        
        ctk.CTkButton(device_frame, text="Refresh", command=self.refresh_devices, width=80, height=28).pack(side="right", padx=5)

        # Credentials - made more compact using grid
        cred_frame = ctk.CTkFrame(section)
        cred_frame.pack(fill="x", padx=10, pady=2)
        
        ctk.CTkLabel(cred_frame, text="Username:").grid(row=0, column=0, padx=5, pady=2, sticky="e")
        ctk.CTkEntry(cred_frame, textvariable=self.username, height=28).grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        
        ctk.CTkLabel(cred_frame, text="Password:").grid(row=1, column=0, padx=5, pady=2, sticky="e")
        ctk.CTkEntry(cred_frame, textvariable=self.password, show='*', height=28).grid(row=1, column=1, padx=5, pady=2, sticky="ew")
        
        cred_frame.grid_columnconfigure(1, weight=1)

        # IP Settings - made more compact
        ip_frame = ctk.CTkFrame(section)
        ip_frame.pack(fill="x", padx=10, pady=2)
        
        ctk.CTkLabel(ip_frame, text="BMC IP:").grid(row=0, column=0, padx=5, pady=2, sticky="e")
        
        # Create a frame for BMC IP entry and grab button
        bmc_ip_frame = ctk.CTkFrame(ip_frame)
        bmc_ip_frame.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        
        ctk.CTkEntry(bmc_ip_frame, textvariable=self.bmc_ip, height=28).pack(side="left", expand=True, fill="x")
       
        
        ctk.CTkLabel(ip_frame, text="Host IP:").grid(row=1, column=0, padx=5, pady=2, sticky="e")
        
        # Create a frame for the Host IP dropdown and refresh button
        host_ip_frame = ctk.CTkFrame(ip_frame)
        host_ip_frame.grid(row=1, column=1, padx=5, pady=2, sticky="ew")
        
        # Create a single dropdown bound to your_ip
        self.ip_dropdown = ctk.CTkComboBox(host_ip_frame, variable=self.your_ip, height=28)
        self.ip_dropdown.pack(side="left", expand=True, fill="x")
        
        # Add a refresh button
        ctk.CTkButton(host_ip_frame, text="↻", command=self.update_ip_dropdown, width=28, height=28).pack(side="right", padx=5)
        
        ip_frame.grid_columnconfigure(1, weight=1)

        # BMC Type - made more compact
        type_frame = ctk.CTkFrame(section)
        type_frame.pack(fill="x", padx=10, pady=2)
        
        ctk.CTkLabel(type_frame, text="BMC Type:").pack(side="left", padx=5)
        ctk.CTkRadioButton(type_frame, text="MOS BMC", variable=self.bmc_type, value=1).pack(side="left", padx=10)
        ctk.CTkRadioButton(type_frame, text="Nano BMC", variable=self.bmc_type, value=2).pack(side="left")

    def create_main_flashing_tab(self, tab_frame):
        """Populates the main BMC Flashing tab with operations."""
        # We pass tab_frame to the original create methods
        self.create_bmc_operations_section(tab_frame)
        self.create_flashing_operations_section(tab_frame)

    def create_dmi_flasher_tab(self, tab_frame):
        """Creates the FRU Data Flasher tab."""
        # Directly populate the tab_frame, no sub-tabs needed
        self.create_fru_flash_sub_tab(tab_frame)

    def create_bmc_operations_section(self, parent_frame):
        """Create the BMC operations section with hyperlink to Web UI"""
        section = ctk.CTkFrame(parent_frame)
        section.pack(fill="x", pady=5)
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=10)
        
        ops = [
            ("Update BMC/BIOS", self.update_bios),
            ("Login to BMC", self.login_to_bmc),
            ("Set BMC IP", self.set_bmc_ip),
            ("Power ON Host", self.power_on_host),
            ("Reboot BMC", self.reboot_bmc),
            ("Factory Reset", self.factory_reset),
            ("Clear Event Log", self.clear_bmc_event_log),
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            button = ctk.CTkButton(op_frame, text=text, command=command, height=28)
            button.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

        # Web UI hyperlink - shows the actual BMC IP and updates live as it
        # changes, opens directly on click (in the desktop's default
        # browser, as the real logged-in user rather than root - see
        # launch_web_ui/_open_url_in_browser) instead of needing a
        # separate button.
        self.web_ui_link = ctk.CTkLabel(
            section, text="Web UI: (set BMC IP)", text_color="#4EA1F7",
            font=ctk.CTkFont(underline=True), cursor="hand2",
        )
        self.web_ui_link.pack(pady=(2, 8))
        self.web_ui_link.bind("<Button-1>", lambda e: self.launch_web_ui())
        self.bmc_ip.trace_add("write", self._update_web_ui_link)
        self._update_web_ui_link()

    def _update_web_ui_link(self, *args):
        bmc_ip = self.bmc_ip.get().strip()
        if hasattr(self, "web_ui_link"):
            self.web_ui_link.configure(
                text=f"Web UI: https://{bmc_ip}" if bmc_ip else "Web UI: (set BMC IP)"
            )

    def create_flashing_operations_section(self, parent_frame):
        """Create the flashing operations section with optimized spacing"""
        section = ctk.CTkFrame(parent_frame)
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="Flashing Operations", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=10)
        
        # Standard operations - UPDATED to include Multi-Unit Flash
        ops = [
            ("Flash FIP (U-Boot)", self.flash_u_boot),
            ("Flash eMMC", self.flash_emmc),
            ("Flash FRU (EEPROM)", self.flash_eeprom),
            ("Flash All", self.on_flash_all),
            ("Multi-Unit Flash", self.open_multi_unit_flash),  # NEW BUTTON
            ("Reboot to Bootloader", self.reboot_to_bootloader),
            ("Auto Set Password", self.auto_set_password),
            ("Set Home Directory", self.set_home_directory),
            ("Open External Console", self.open_minicom_console),
            ("Stop Operation", self.stop_operation),
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            button = ctk.CTkButton(op_frame, text=text, command=command, height=28)
            button.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
            
            # Special styling for Multi-Unit Flash button
            if text == "Multi-Unit Flash":
                button.configure(fg_color="#2B5CE6", hover_color="#1E3A8A", 
                            text_color="white", font=ctk.CTkFont(weight="bold"))
                # Deliberately colored (not the plain default blue), so the
                # theme collector wouldn't find it by color-matching alone -
                # keep an explicit reference so theming can include it anyway.
                self.multi_unit_flash_button = button
            elif text == "Stop Operation":
                self.stop_button = button
                button.configure(fg_color="#cc0000", hover_color="#aa0000")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

    def create_fru_flash_sub_tab(self, tab):
        """Create the UI for the FRU Flash sub-tab."""
        ctk.CTkLabel(tab, text="Enter FRU data to flash using FRU_flash_v2.sh", font=ctk.CTkFont(size=12)).pack(pady=5)

        info_frame = ctk.CTkFrame(tab)
        info_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(info_frame, text="SKU:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        # Replace CTkEntry with CTkComboBox
        self.sku_dropdown = ctk.CTkComboBox(info_frame, variable=self.fru_sku, values=self.sku_list, width=300)
        self.sku_dropdown.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        ctk.CTkLabel(info_frame, text="ASMID:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        ctk.CTkEntry(info_frame, textvariable=self.fru_asmid, width=300).grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        
        ctk.CTkLabel(info_frame, text="MFG:").grid(row=2, column=0, padx=5, pady=5, sticky="e")
        ctk.CTkEntry(info_frame, textvariable=self.fru_mfg, width=300).grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        
        info_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(tab, text="Flash FRU Data", command=self.flash_fru).pack(pady=10)

    def create_log_section(self, parent_frame):
        """Create the log section - sits beside Connection Settings,
        filling the space freed up by that section no longer stretching
        full-width."""
        section = ctk.CTkFrame(parent_frame)
        section.pack(side="left", fill="both", expand=True, padx=(10, 0))
        
        header = ctk.CTkFrame(section, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(5, 0))
        ctk.CTkLabel(header, text="Log", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(
            header, text="Delete All Logs", width=110, height=24, command=self.clear_log,
            fg_color="#a94442", hover_color="#c9302c",
        ).pack(side="right")
        
        self.log_box = ctk.CTkTextbox(section, height=260, state="disabled")
        self.log_box.pack(padx=10, pady=5, fill="x")

    def clear_log(self):
        """Wipe all messages from the log box."""
        if hasattr(self, 'log_box') and self.log_box:
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", tk.END)
            self.log_box.configure(state="disabled")

    def create_progress_section(self):
            """Create the progress section (just the progress bar now - the
            Console and Stop Operation buttons live in the BMC operations
            row above, inside the tabview)."""
            section = ctk.CTkFrame(self.controls_frame)
            section.pack(fill="x", pady=5)
            
            progress_frame = ctk.CTkFrame(section)
            progress_frame.pack(fill="x", padx=10, pady=5)
            
            self.progress = ctk.CTkProgressBar(progress_frame)
            self.progress.pack(side="left", expand=True, fill="x", padx=5)
            self.progress.set(0)
        

    def open_multi_unit_flash(self):
        """Open the multi-unit flash window (NanoBMC only)"""
        if not MULTI_UNIT_AVAILABLE:
            messagebox.showerror("Feature Not Available", 
                            "Multi-unit flashing is not available.\n\n"
                            "Please ensure 'extra.py' is in the same directory as main.py")
            return
        
        # Check if NanoBMC is selected
        if self.bmc_type.get() != 2:
            response = messagebox.askyesno("BMC Type", 
                                        "Multi-unit flashing is only available for NanoBMC devices.\n\n"
                                        "Would you like to switch to NanoBMC mode?")
            if response:
                self.bmc_type.set(2)
                self.log_message("Switched to NanoBMC mode for multi-unit flashing")
            else:
                return
        
        # Clean up any existing serial connections before opening multi-unit
        try:
            connections_cleaned = cleanup_all_serial_connections()
            if connections_cleaned > 0:
                self.log_message(f"Detached {connections_cleaned} serial connections for multi-unit mode")
            else:
                self.log_message("No active serial connections to detach")
        except Exception as e:
            self.log_message(f"Error detaching serial connections: {e}")
        
        try:
            # Create and show the multi-unit window
            multi_window = create_multi_unit_window(self.root, self)
            if multi_window:
                self.log_message("Multi-unit flash window opened")
                self.log_message("TIP: Make sure all devices are at U-Boot bootloader prompt before starting")
        except Exception as e:
            self.log_message(f"Error opening multi-unit flash window: {e}")
            messagebox.showerror("Error", f"Failed to open multi-unit flash window:\n{e}")


    def _collect_themable_buttons(self):
        """Find every CTkButton/CTkRadioButton/CTkSegmentedButton in the
        app that's still using the plain default blue theme color, so
        theme switching can restyle exactly those - not the deliberately-
        colored ones like the red Stop/Power Off buttons or the green
        Power On button, which carry specific meaning and shouldn't change
        with the button theme. CTkSegmentedButton matters here because
        CTkTabview's own tab-selector strip (the "navbar" - BMC Flashing /
        FRU Data Flasher / Virtual Media) is implemented internally as one
        - it's a completely different widget class from CTkButton, so it
        would otherwise be silently skipped entirely. The Multi-Unit Flash
        button is deliberately colored too (a distinct blue, not the plain
        default), so it wouldn't be found by color-matching alone - it's
        added explicitly via the reference kept when it was created.
        Collected once (lazily, on first use) after the whole UI has been
        built, and captures the real default colors first so "Default"
        can restore them exactly rather than guessing at hardcoded
        values."""
        if self._themed_buttons or self._themed_radio_buttons or self._themed_segmented_buttons:
            return  # already collected

        try:
            from customtkinter import ThemeManager
            btn_theme = ThemeManager.theme["CTkButton"]
            seg_theme = ThemeManager.theme["CTkSegmentedButton"]
            mode_idx = 1 if ctk.get_appearance_mode() == "Dark" else 0
            self._default_button_colors = {
                "fg_color": btn_theme["fg_color"][mode_idx],
                "hover_color": btn_theme["hover_color"][mode_idx],
                "text_color": btn_theme["text_color"][mode_idx],
            }
            self._default_segmented_colors = {
                "selected_color": seg_theme["selected_color"][mode_idx],
                "selected_hover_color": seg_theme["selected_hover_color"][mode_idx],
            }
        except Exception:
            # Fall back to CTk's known built-in "blue" theme values if the
            # theme manager's structure isn't what's expected.
            self._default_button_colors = {
                "fg_color": "#1F6AA5", "hover_color": "#144870", "text_color": "#DCE4EE",
            }
            self._default_segmented_colors = {
                "selected_color": "#1F6AA5", "selected_hover_color": "#144870",
            }

        self.BUTTON_THEMES["Default"] = self._default_button_colors

        def _walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkSegmentedButton):
                    try:
                        current_selected = child._apply_appearance_mode(child.cget("selected_color"))
                    except Exception:
                        current_selected = None
                    if current_selected == self._default_segmented_colors["selected_color"]:
                        self._themed_segmented_buttons.append(child)
                elif isinstance(child, (ctk.CTkButton, ctk.CTkRadioButton)):
                    try:
                        # cget("fg_color") returns the raw (light, dark)
                        # tuple/list as originally set, not the resolved
                        # color for the current appearance mode - comparing
                        # that directly against a resolved hex string would
                        # never match anything. _apply_appearance_mode()
                        # is the same resolution method CTk itself uses
                        # internally when actually drawing the widget.
                        current_fg = child._apply_appearance_mode(child.cget("fg_color"))
                    except Exception:
                        current_fg = None
                    if current_fg == self._default_button_colors["fg_color"]:
                        if isinstance(child, ctk.CTkButton):
                            self._themed_buttons.append(child)
                        else:
                            self._themed_radio_buttons.append(child)
                _walk(child)

        _walk(self.root)

        if getattr(self, "multi_unit_flash_button", None) is not None:
            if self.multi_unit_flash_button not in self._themed_buttons:
                self._themed_buttons.append(self.multi_unit_flash_button)

    def apply_button_theme(self, theme_name):
        """Restyle every plain default-blue button/radio button/segmented-
        button (including the CTkTabview navbar) in the app - plus the
        Multi-Unit Flash button - to the chosen theme."""
        self._collect_themable_buttons()
        theme = self.BUTTON_THEMES.get(theme_name)
        if theme is None:
            return
        for button in self._themed_buttons:
            try:
                button.configure(
                    fg_color=theme["fg_color"],
                    hover_color=theme["hover_color"],
                    text_color=theme["text_color"],
                )
            except Exception:
                pass
        for radio in self._themed_radio_buttons:
            try:
                # Radio button label text isn't part of the accent color,
                # so only the selection indicator's fg/hover change.
                radio.configure(fg_color=theme["fg_color"], hover_color=theme["hover_color"])
            except Exception:
                pass
        for seg in self._themed_segmented_buttons:
            try:
                # CTkSegmentedButton uses different color-key names than
                # CTkButton (selected_color/selected_hover_color rather
                # than fg_color/hover_color), but the same underlying hex
                # values - CTk's default theme uses the identical blue for
                # both - so the same theme dict applies directly here too.
                seg.configure(selected_color=theme["fg_color"], selected_hover_color=theme["hover_color"])
            except Exception:
                pass
        self.log_message(
            f"Applied '{theme_name}' button theme to {len(self._themed_buttons)} button(s), "
            f"{len(self._themed_radio_buttons)} radio button(s), and "
            f"{len(self._themed_segmented_buttons)} segmented control(s)."
        )

    def refresh_devices(self):
        """Find all available serial devices and update the dropdown"""
        # Find all serial devices
        devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        
        if not devices:
            self.log_message("No serial devices found.")
            self.serial_device.set("")
        else:
            # Update the dropdown values
            self.serial_dropdown.configure(values=devices)
            # Log the found devices
            self.log_message(f"Found {len(devices)} serial devices: {', '.join(devices)}")
            # Set the first device if not already set
            if not self.serial_device.get() and devices:
                self.serial_device.set(devices[0])
                self.log_message(f"Automatically selected device: {devices[0]}")

        # Auto-connect the embedded Serial console when there's exactly
        # one ttyUSB device - nothing to choose between, so no need for a
        # manual Connect click. Runs both at startup and on a manual
        # Refresh click (e.g. after plugging a device in), but never
        # disrupts a session that's already connected.
        ttyusb_devices = [d for d in devices if "ttyUSB" in d]
        if (len(ttyusb_devices) == 1 and hasattr(self, "console_panel")
                and self.console_panel.mode == self.console_panel.MODE_SERIAL
                and self.console_panel.backend is None):
            self.log_message(f"Exactly one serial device found ({ttyusb_devices[0]}) - auto-connecting console.")
            try:
                self.console_panel.connect()
            except Exception as e:
                self.log_message(f"Auto-connect failed: {e}")
        elif len(devices) > 2:
            # Ambiguous which one to use - open the dropdown right away
            # instead of making the user click it first to see the choices.
            try:
                self.serial_dropdown._open_dropdown_menu()
            except Exception:
                pass  # cosmetic convenience only - never block on it

        # Return the devices found (empty list if none) - truthy/falsy
        # compatible with callers that just check "were any found", and
        # also usable by callers that need the actual list/count.
        return devices

    def get_network_interfaces(self):
        """Get a comprehensive list of all network interfaces with valid IP addresses"""
        ips = []
        interface_info = {}
        
        try:
            # Method 1: Use 'ip addr show' command (most comprehensive)
            try:
                result = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    import re
                    current_interface = None
                    
                    for line in result.stdout.splitlines():
                        # Parse interface names
                        interface_match = re.match(r'^\d+:\s+([^:@]+)[@:]?\s', line)
                        if interface_match:
                            current_interface = interface_match.group(1)
                            continue
                        
                        # Parse IP addresses
                        ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)/\d+.*scope\s+global', line)
                        if ip_match and current_interface:
                            ip = ip_match.group(1)
                            if ip not in ips and ip != "127.0.0.1":
                                ips.append(ip)
                                interface_info[ip] = current_interface
                                self.log_message(f"Found IP: {ip} on interface {current_interface}")
                    
            except Exception as e:
                self.log_message(f"ip addr command failed: {e}")
            
            # Method 2: Use 'ifconfig' command as backup
            if not ips:
                try:
                    result = subprocess.run(['ifconfig'], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        import re
                        
                        # Split by interface blocks
                        interfaces = result.stdout.split('\n\n')
                        
                        for interface_block in interfaces:
                            if not interface_block.strip():
                                continue
                                
                            # Get interface name
                            interface_name_match = re.match(r'^([^:\s]+)', interface_block)
                            interface_name = interface_name_match.group(1) if interface_name_match else "unknown"
                            
                            # Find IP addresses
                            ip_matches = re.findall(r'inet\s+(\d+\.\d+\.\d+\.\d+)', interface_block)
                            
                            for ip in ip_matches:
                                if ip not in ips and ip != "127.0.0.1":
                                    ips.append(ip)
                                    interface_info[ip] = interface_name
                                    self.log_message(f"Found IP: {ip} on interface {interface_name}")
                                    
                except Exception as e:
                    self.log_message(f"ifconfig command failed: {e}")
            
            # Method 3: Use netifaces library if available
            try:
                import netifaces
                
                for interface in netifaces.interfaces():
                    try:
                        addrs = netifaces.ifaddresses(interface)
                        if netifaces.AF_INET in addrs:
                            for addr_info in addrs[netifaces.AF_INET]:
                                ip = addr_info.get('addr')
                                if ip and ip not in ips and ip != "127.0.0.1":
                                    ips.append(ip)
                                    interface_info[ip] = interface
                                    self.log_message(f"Found IP: {ip} on interface {interface}")
                    except Exception:
                        continue
                        
            except ImportError:
                pass  # netifaces not available
            except Exception as e:
                self.log_message(f"netifaces detection failed: {e}")
            
            # Method 4: Use psutil network interfaces
            try:
                import psutil
                
                net_if_addrs = psutil.net_if_addrs()
                for interface_name, addr_list in net_if_addrs.items():
                    for addr in addr_list:
                        if addr.family == 2:  # AF_INET (IPv4)
                            ip = addr.address
                            if ip and ip not in ips and ip != "127.0.0.1":
                                ips.append(ip)
                                interface_info[ip] = interface_name
                                self.log_message(f"Found IP: {ip} on interface {interface_name}")
                                
            except Exception as e:
                self.log_message(f"psutil network detection failed: {e}")
            
            # Method 5: Parse /proc/net/fib_trie (Linux specific)
            try:
                with open('/proc/net/fib_trie', 'r') as f:
                    content = f.read()
                    import re
                    
                    # Find local IPs
                    ip_matches = re.findall(r'/32 host LOCAL\n.*?(\d+\.\d+\.\d+\.\d+)', content, re.DOTALL)
                    
                    for ip in ip_matches:
                        if ip and ip not in ips and ip != "127.0.0.1":
                            ips.append(ip)
                            interface_info[ip] = "system"
                            self.log_message(f"Found IP: {ip} from /proc/net/fib_trie")
                            
            except Exception as e:
                pass  # /proc/net/fib_trie might not be available
            
            # Method 6: Socket-based detection (fallback)
            if not ips:
                try:
                    import socket
                    
                    # Get hostname IPs
                    hostname = socket.gethostname()
                    
                    # Get primary IP by connecting to external address
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                            s.connect(("8.8.8.8", 80))  # Google DNS
                            primary_ip = s.getsockname()[0]
                            if primary_ip not in ips and primary_ip != "127.0.0.1":
                                ips.append(primary_ip)
                                interface_info[primary_ip] = "primary"
                                self.log_message(f"Found primary IP: {primary_ip}")
                    except:
                        pass
                    
                    # Get all hostname IPs
                    try:
                        hostname_ips = socket.gethostbyname_ex(hostname)[2]
                        for ip in hostname_ips:
                            if ip not in ips and ip != "127.0.0.1":
                                ips.append(ip)
                                interface_info[ip] = "hostname"
                                self.log_message(f"Found hostname IP: {ip}")
                    except:
                        pass
                        
                except Exception as e:
                    self.log_message(f"Socket detection failed: {e}")
            
            # Remove any invalid IPs and sort
            valid_ips = []
            for ip in ips:
                # Validate IP format
                try:
                    parts = ip.split('.')
                    if len(parts) == 4 and all(0 <= int(part) <= 255 for part in parts):
                        valid_ips.append(ip)
                except:
                    continue
            
            # Sort IPs: put private network ranges first, then public
            def ip_sort_key(ip):
                parts = [int(x) for x in ip.split('.')]
                # Private ranges: 192.168.x.x, 10.x.x.x, 172.16-31.x.x
                if parts[0] == 192 and parts[1] == 168:
                    return (0, parts)  # 192.168.x.x first
                elif parts[0] == 10:
                    return (1, parts)  # 10.x.x.x second  
                elif parts[0] == 172 and 16 <= parts[1] <= 31:
                    return (2, parts)  # 172.16-31.x.x third
                else:
                    return (3, parts)  # Public IPs last
            
            valid_ips.sort(key=ip_sort_key)
            
            # Log summary
            if valid_ips:
                self.log_message(f"Total {len(valid_ips)} IP address(es) detected:")
                for ip in valid_ips:
                    interface = interface_info.get(ip, "unknown")
                    self.log_message(f"  - {ip} ({interface})")
            else:
                self.log_message("No valid IP addresses found")
                # Add localhost as absolute fallback
                valid_ips = ["127.0.0.1"]
                
        except Exception as e:
            self.log_message(f"Error detecting network interfaces: {e}")
            # Absolute fallback
            valid_ips = ["127.0.0.1"]
        
        return valid_ips

    def update_ip_dropdown(self):
        """Update the IP address dropdown with all available network interfaces"""
        try:
            if not hasattr(self, 'ip_dropdown'):
                return
                
            self.log_message("Refreshing network interface list...")
            
            # Get comprehensive list of network interfaces
            ips = self.get_network_interfaces()
            
            # If no interfaces found, show error and keep current
            if not ips:
                self.log_message(" No network interfaces found")
                return
                
            # Update the dropdown values with all detected IPs
            self.ip_dropdown.configure(values=ips)
            
            # Get current IP selection
            current_ip = self.your_ip.get()
            
            # Set the IP selection intelligently
            if current_ip and current_ip in ips:
                # Keep current selection if it's still valid
                self.ip_dropdown.set(current_ip)
                self.log_message(f"✓ Kept current selection: {current_ip}")
            else:
                # Auto-select the best IP (first in sorted list)
                if ips:
                    best_ip = ips[0]  # First IP after sorting (private networks first)
                    self.ip_dropdown.set(best_ip)
                    self.your_ip.set(best_ip)
                    self.log_message(f"✓ Auto-selected: {best_ip}")
            
            # Show summary in log
            self.log_message(f"Host IP dropdown updated with {len(ips)} interface(s)")
                    
        except Exception as e:
            self.log_message(f" Error updating network interfaces: {e}")
            # Don't crash - just keep whatever was there before

    def log_message(self, message):
        """Add a message to the log box"""
        if hasattr(self, 'log_box') and self.log_box:
            self.log_box.configure(state="normal")
            self.log_box.insert(tk.END, f"{message}\n")
            self.log_box.configure(state="disabled")
            self.log_box.see(tk.END)
        else:
            print(f"Log: {message}")

    def update_progress(self, value):
        """Update the progress bar value"""
        if hasattr(self, 'progress'):
            self.progress.set(value)

    def validate_button_click(self):
        """Check if buttons should be locked (operation in progress)"""
        # Exception: allow clicks while BIOS/BMC update is running
        if getattr(self, 'bios_update_running', False):
            return True

        if self.lock_buttons:
            self.log_message("Another operation is in progress. Please wait...")
            return False
        self.lock_buttons = True
        return True

    def open_minicom_console(self):
        """Open a minicom console for the selected serial device with better process management"""
        if not self.serial_device.get():
            self.log_message("No serial device selected. Please select a device.")
            return
        try:
            device = self.serial_device.get()
            self.log_message(f"Launching Minicom on {device}...")

            # Clean up any existing minicom processes first
            self.cleanup_minicom_processes()

            if not shutil.which("minicom"):
                self.log_message("minicom is not installed. Install it with: sudo apt install minicom")
                return

            process = launch_in_terminal(
                f"minicom -D {device}",
                title=f"Console - {device}",
                log=self.log_message,
            )
            if process is None:
                self.log_message(f"Error launching Minicom: no working terminal emulator found on this system.")

        except Exception as e:
            self.log_message(f"Error launching Minicom: {e}")

    def cleanup_minicom_processes(self):
        """Clean up any existing minicom processes"""
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                if proc.info['name'] == 'minicom':
                    try:
                        cmdline = ' '.join(proc.info['cmdline'] or [])
                        if self.serial_device.get() in cmdline:
                            self.log_message(f"Terminating existing minicom process: {proc.info['pid']}")
                            proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except Exception as e:
            self.log_message(f"Error cleaning minicom processes: {e}")

    def cleanup_server_processes(self):
        """Clean up any running server processes (TFTP, HTTP, etc.)"""
        self.log_message("Stopping any running servers...")
        try:
            # Look for TFTP server processes
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                # Check for TFTP or HTTP server processes that might have been started
                if proc.info['name'] in ['tftp', 'tftpd', 'in.tftpd', 'python', 'python3', 'http.server']:
                    try:
                        cmdline = ' '.join(proc.info['cmdline'] or [])
                        # If the command line contains indicators this was our server
                        if ('tftp' in cmdline.lower() and self.your_ip.get() in cmdline) or \
                        ('http.server' in cmdline.lower() and self.your_ip.get() in cmdline):
                            self.log_message(f"Terminating server process: {proc.info['pid']}")
                            proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
                        
            # Execute specific kill commands for any known server processes
            try:
                # Try to kill any TFTP/HTTP servers bound to our IP
                subprocess.run(f"pkill -f 'tftp.*{self.your_ip.get()}'", shell=True)
                subprocess.run(f"pkill -f 'http.server.*{self.your_ip.get()}'", shell=True)
            except Exception:
                pass
            
            self.log_message("Server cleanup completed.")
        except Exception as e:
            self.log_message(f"Warning: Error during server cleanup: {e}")
            
    def _run_operation(self, operation_func, required_fields=None, error_msg=None):
        """
        Generic method to run BMC operations with proper validation and threading
        
        Args:
            operation_func: Function to run as the operation
            required_fields: Dictionary of {field_name: field_value} to validate
            error_msg: Error message to display if validation fails
        """
        if not self.validate_button_click():
            return False
            
        # Validate required fields
        if required_fields:
            missing_fields = [name for name, value in required_fields.items() if not value]
            if missing_fields:
                self.log_message(error_msg or f"Missing required fields: {', '.join(missing_fields)}")
                self.lock_buttons = False
                return False
            
        self.abort_requested = False
        
        # Start thread for operation
        self.lock_buttons = True # Lock buttons here
        threading.Thread(target=self.run_async_operation, args=(operation_func,), daemon=True).start()
        return True
    
    def run_async_operation(self, operation_func):
        """Wrapper to run the async operation and unlock buttons"""
        try:
            asyncio.run(operation_func())
        except Exception as e:
            self.log_message(f"Fatal error in operation: {e}")
        finally:
            self.lock_buttons = False

    # --- DMI Flasher Operations ---

    def flash_fru(self):
        """Prepares and runs the FRU flashing operation."""
        required = {
            "Serial Device": self.serial_device.get(),
            "Host IP": self.your_ip.get(),
            "SKU": self.fru_sku.get(),
            "ASMID": self.fru_asmid.get(),
            "MFG": self.fru_mfg.get(),
        }
        if not self._run_operation(
            self.run_flash_fru,
            required_fields=required,
            error_msg="Please fill in all connection and FRU fields."
        ):
            return # Validation failed
            
    async def run_flash_fru(self):
        """The async task for flashing FRU."""
        self.log_message("--- Starting FRU Flash ---")
        sku = self.fru_sku.get()
        asmid = self.fru_asmid.get()
        mfg = self.fru_mfg.get()
        
        # Construct the arguments for the shell script
        # Quote the mfg string to handle spaces
        script_args = f'--sku {sku} --asmid {asmid} --mfg "{mfg}"'
        
        await transfer_and_run_script(
            serial_device=self.serial_device.get(),
            host_ip=self.your_ip.get(),
            script_content=FRU_FLASH_SCRIPT_CONTENT,
            script_name="FRU_flash_v2.sh",
            script_args=script_args,
            callback_output=self.log_message,
            callback_progress=self.update_progress
        )
        self.log_message("--- FRU Flash Finished ---")

    # BMC OPERATIONS
    

    def login_to_bmc(self):
        """Log in to BMC"""
        required = {
            "Username": self.username.get(),
            "Password": self.password.get(),
            "Serial Device": self.serial_device.get()
        }
        if self._run_operation(
            self.run_login_to_bmc,
            required_fields=required,
            error_msg="Error: Missing input(s). Please enter username, password, and select a device."
        ):
            self.log_message("Attempting to log in to BMC...")

    async def run_login_to_bmc(self):
        """Run BMC login operation"""
        try:
            response = await login(
                self.username.get(), 
                self.password.get(), 
                self.serial_device.get(),
                self.log_message
            )

            if response is None:
                self.log_message("Error: No response received during BMC login.")
                return

            # Check if login is successful
            if "login successful" in response.lower():
                self.log_message("BMC login successful. You can now perform other actions.")
            else:
                self.log_message("Login failed. Please check your credentials.")
        except Exception as e:
            self.log_message(f"Error during BMC login: {e}")
        finally:
            self.lock_buttons = False


    def _is_valid_ipv4(self, ip_str):
        """Reject launching the web UI for an obviously incomplete/invalid
        address (e.g. still mid-typing) rather than trying to open a
        broken URL."""
        import ipaddress
        try:
            ipaddress.IPv4Address(ip_str)
            return True
        except ValueError:
            return False

    def launch_web_ui(self):
        """Open the BMC's web UI (https://<bmc_ip>) directly in the
        desktop's default browser, as the real logged-in user rather than
        root - Platypus is commonly run under sudo, where root has no
        access to the desktop session, so _open_url_in_browser re-invokes
        the browser launch as the actual user via $SUDO_USER. Only falls
        back to clipboard+a dialog if that actually fails."""
        bmc_ip = self.bmc_ip.get().strip()
        if not bmc_ip:
            self.log_message("Set the BMC IP first.")
            messagebox.showerror("Launch Web UI", "Please enter a BMC IP first.")
            return
        if not self._is_valid_ipv4(bmc_ip):
            messagebox.showwarning("Invalid BMC IP", "Enter a valid BMC IPv4 address first.")
            return
        url = f"https://{bmc_ip}"

        if self._open_url_in_browser(url):
            self.log_message(f"[NETWORK] Opened BMC interface: {url}")
            return

        copied = False
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            copied = True
        except Exception:
            pass

        self.log_message(
            f"Could not open a browser automatically for {url}."
            + (" Copied to clipboard." if copied else "")
        )
        clip_note = "It's been copied to your clipboard - paste it into a browser.\n\n" if copied else ""
        messagebox.showinfo(
            "Launch Web UI",
            "Couldn't automatically open a browser. This commonly happens "
            "when Platypus is run with sudo, since the root user doesn't "
            "have access to your desktop session.\n\n"
            f"{clip_note}URL: {url}",
        )

    def clear_bmc_event_log(self):
        """Clear the BMC's Redfish event log(s) (LogService.ClearLog)."""
        bmc_ip = self.bmc_ip.get().strip()
        user = self.username.get().strip()
        password = self.password.get()
        if not bmc_ip or not user or not password:
            messagebox.showerror("Clear Event Log", "Please enter BMC IP, username, and password first.")
            return

        if not messagebox.askyesno(
            "Clear Event Log",
            f"This will permanently clear the event log(s) on {bmc_ip}. Continue?",
        ):
            return

        self.log_message(f"Clearing event log on {bmc_ip}...")

        def _worker():
            try:
                summary = bmc.clear_event_log(user, password, bmc_ip)
                self.root.after(0, lambda: self.log_message(summary))
            except Exception as e:
                # Capture into a plain variable before the lambda - see the
                # note in inventory_panel.py for why referencing `e`
                # directly inside a deferred lambda raises NameError.
                error_msg = str(e)
                self.root.after(0, lambda: self.log_message(f"Error clearing event log: {error_msg}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _find_user_session_env(self, target_uid):
        """Find the real DISPLAY/WAYLAND_DISPLAY/XAUTHORITY/DBUS session
        variables by reading the environment of a process that's already
        running correctly inside that user's desktop session - rather
        than guessing paths ourselves. This matters a lot on Wayland: the
        Xwayland auth cookie lives at a randomly-named path under
        /run/user/<uid>/ (e.g. .mutter-Xwaylandauth.XXXXXX), not the
        classic ~/.Xauthority, so there's no fixed path to guess at all."""
        env = {}
        wanted = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
        for environ_path in glob.glob("/proc/*/environ"):
            try:
                pid_str = environ_path.split("/")[2]
                if not pid_str.isdigit():
                    continue
                if os.stat(f"/proc/{pid_str}").st_uid != target_uid:
                    continue
                with open(environ_path, "rb") as f:
                    raw = f.read()
            except Exception:
                continue

            pairs = dict(
                item.split("=", 1) for item in raw.decode(errors="ignore").split("\0") if "=" in item
            )
            if "DISPLAY" not in pairs and "WAYLAND_DISPLAY" not in pairs:
                continue
            for key in wanted:
                if key in pairs and key not in env:
                    env[key] = pairs[key]
            if all(k in env for k in ("DISPLAY", "XAUTHORITY")) or "WAYLAND_DISPLAY" in env:
                break  # found a solid candidate, no need to keep scanning

        return env

    def _open_url_in_browser(self, url):
        """Try to open `url` in the desktop's default browser.

        Under sudo, this needs to actually launch as the real desktop
        user, not root - browsers like Firefox explicitly refuse to run
        as root inside a regular user's session (a deliberate safety
        check, not a missing-permission error) even when the display
        connection itself works fine. So rather than guessing a session
        path (the Wayland/Xwayland auth cookie in particular has no fixed
        location), this reads the target user's *actual* running session
        environment first (see _find_user_session_env) and launches with
        that."""
        sudo_user = os.environ.get("SUDO_USER")
        if hasattr(os, "geteuid") and os.geteuid() == 0 and sudo_user:
            try:
                target_uid = pwd.getpwnam(sudo_user).pw_uid
                session_env = self._find_user_session_env(target_uid)
                if session_env:
                    env = os.environ.copy()
                    env["HOME"] = pwd.getpwnam(sudo_user).pw_dir
                    env.update(session_env)
                    result = subprocess.run(
                        ["sudo", "-u", sudo_user, "-E", "xdg-open", url],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                    )
                    if result.returncode == 0:
                        return True
            except Exception:
                pass  # fall through to the plain attempt below

        try:
            if webbrowser.open_new_tab(url):
                return True
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def set_bmc_ip(self):
        """Set BMC IP address"""
        required = {"BMC IP": self.bmc_ip.get(), "Serial Device": self.serial_device.get()}
        if self._run_operation(
            self.run_set_bmc_ip,
            required_fields=required,
            error_msg="Please enter BMC IP and select a serial device"
        ):
            self.log_message(f"Setting BMC IP to {self.bmc_ip.get()}...")


    async def run_set_bmc_ip(self):
        """Run set BMC IP operation"""
        try:
            await set_ip(
                self.bmc_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get(),
                self.username.get(),
                self.password.get(),
            )
        except Exception as e:
            self.log_message(f"Error during IP setup: {e}")
        finally:
            self.lock_buttons = False

    def power_on_host(self):
        """Power on the host"""
        required = {"Serial Device": self.serial_device.get()}
        if self._run_operation(
            self.run_power_on_host,
            required_fields=required,
            error_msg="Please select a serial device"
        ):
            self.log_message("Sending power on command to host...")

    async def run_power_on_host(self):
        """Run power on host operation"""
        try:
            await bmc.power_host(
                self.log_message, 
                self.serial_device.get(),
                self.username.get(),
                self.password.get(),
            )
        except Exception as e:
            self.log_message(f"Error powering on host: {e}")
        finally:
            self.lock_buttons = False

    def reboot_bmc(self):
        """Reboot the BMC"""
        required = {"Serial Device": self.serial_device.get()}
        if self._run_operation(
            self.run_reboot_bmc,
            required_fields=required,
            error_msg="No serial device selected. Please select a device."
        ):
            self.log_message("Sending reboot command to BMC...")

    async def run_reboot_bmc(self):
        """Run reboot BMC operation"""
        try:
            await bmc.reboot_bmc(
                self.log_message, 
                self.serial_device.get()
            )
        except Exception as e:
            self.log_message(f"Error rebooting BMC: {e}")
        finally:
            self.lock_buttons = False

    def factory_reset(self):
        """Factory reset the BMC"""
        required = {"Serial Device": self.serial_device.get()}
        if self._run_operation(
            self.run_factory_reset,
            required_fields=required,
            error_msg="Please select a serial device"
        ):
            self.log_message("Sending factory reset command to BMC...")

    async def run_factory_reset(self):
        """Run factory reset operation"""
        try:
            await bmc.bmc_factory_reset(
                self.log_message, 
                self.serial_device.get()
            )
        except Exception as e:
            self.log_message(f"Error during factory reset: {e}")
        finally:
            self.lock_buttons = False
            
    # FLASHING OPERATIONS

    def flash_u_boot(self):
        """Flash the FIP (U-Boot)"""
        required = {
            "Host IP": self.your_ip.get(),
            "Serial Device": self.serial_device.get()
        }
        if self._run_operation(
            self.run_flash_u_boot,
            required_fields=required,
            error_msg="Please enter Host IP and select a serial device"
        ):
            self.log_message("Starting U-Boot flashing operation...")

    async def run_flash_u_boot(self):
            """Run flash U-Boot operation with strict filename validation"""
            try:
                # Select FIP file with specific filter
                file_path = FileSelectionHelper.select_file(
                    self.root,
                    "Select FIP File", 
                    self.last_fip_dir,
                    "FIP files (fip-snuc-*.bin) | fip-snuc-*.bin"
                )
                
                if not file_path:
                    self.log_message("No file selected. Flashing aborted.")
                    self.lock_buttons = False
                    return
                
                # Validate filename - STRICT validation for FIP files
                filename = os.path.basename(file_path)
                allowed_fip_files = {"fip-snuc-nanobmc.bin", "fip-snuc-mos-bmc.bin"}
                
                if filename not in allowed_fip_files:
                    self.log_message(f" ERROR: Invalid FIP file selected!")
                    self.log_message(f"Selected file: '{filename}'")
                    self.log_message(f"Allowed files: {', '.join(allowed_fip_files)}")
                    self.log_message(" FIP flashing ABORTED for safety!")
                    
                    # Show error dialog to user
                    from tkinter import messagebox
                    messagebox.showerror(
                        "Invalid FIP File", 
                        f"Invalid FIP file selected: '{filename}'\n\n"
                        f"Only these files are allowed:\n"
                        f"• fip-snuc-nanobmc.bin\n"
                        f"• fip-snuc-mos-bmc.bin\n\n"
                        f"Please select the correct FIP file and try again."
                    )
                    
                    self.lock_buttons = False
                    return
                    
                # Update last used directory
                self.last_fip_dir = os.path.dirname(file_path)
                self.save_config()
                
                self.flash_file = file_path
                self.log_message(f"✓ Valid FIP file selected: {filename}")
                self.log_message(f"File path: {file_path}")
                
                # Run the flashing process
                await bmc.flasher(
                    self.flash_file, 
                    self.your_ip.get(), 
                    self.update_progress, 
                    self.log_message, 
                    self.serial_device.get()
                )
            except Exception as e:
                self.log_message(f"Error during FIP flashing: {e}")
            finally:
                self.lock_buttons = False


    def flash_emmc(self):
        """Flash the eMMC"""
        # Check if already running
        if self.operation_running:
            self.log_message("An operation is already in progress. Please wait for it to complete.")
            return
            
        # Show bootloader warning popup
        if not messagebox.askyesno("Bootloader Warning", 
                                "WARNING: Make sure your system is at the U-Boot bootloader prompt before continuing.\n\n"
                                "Have you already rebooted to the bootloader?"):
            self.log_message("eMMC flashing cancelled - system not in bootloader.")
            return
            
        required = {
            "BMC Type": str(self.bmc_type.get()),
            "BMC IP": self.bmc_ip.get(),
            "Host IP": self.your_ip.get()
        }
        if self._run_operation(
            self.run_flash_emmc,
            required_fields=required,
            error_msg="Please enter all required fields: BMC Type, BMC IP, and Host IP"
        ):
            self.operation_running = True
            self.log_message("Starting eMMC flashing process...")

    async def run_flash_emmc(self):
        """Run flash eMMC operation"""
        try:
            # First, clean up any existing servers
            self.cleanup_server_processes()
            
            # Select firmware directory
            firmware_directory = FileSelectionHelper.select_directory(
                self.root,
                "Select Firmware Directory", 
                self.last_firmware_dir
            )
            
            if not firmware_directory:
                self.log_message("No directory selected. Cleaning up and aborting...")
                # Cleanup any running server processes
                self.cleanup_server_processes()
                self.lock_buttons = False
                self.operation_running = False
                return

            # Update last used directory
            self.last_firmware_dir = os.path.dirname(firmware_directory) or firmware_directory
            self.save_config()

            # Continue with flashing process - ADD serial_device parameter
            await bmc.flash_emmc(
                self.bmc_ip.get(),
                firmware_directory,
                self.your_ip.get(),
                self.bmc_type.get(),
                self.update_progress,
                self.log_message,
                self.serial_device.get()  # ADD THIS LINE
            )

            if self.auto_password_reset_enabled.get():
                await bmc.post_flash_password_setup(
                    self.serial_device.get(),
                    self.post_flash_default_password.get(),
                    self.password.get(),
                    self.log_message,
                )
        except Exception as e:
            self.log_message(f"Error during eMMC flashing: {e}")
            # Cleanup on error
            self.cleanup_server_processes()
        finally:
            self.lock_buttons = False
            self.operation_running = False

    def reset_bmc(self):
        """Reset the BMC"""
        if self._run_operation(
            self.run_reset_bmc,
            error_msg="Failed to start BMC reset operation"
        ):
            self.log_message("Sending reset command to BMC...")

    async def run_reset_bmc(self):
        """Run reset BMC operation"""
        try:
            await bmc.reset_uboot(self.log_message)
        except Exception as e:
            self.log_message(f"Error resetting BMC: {e}")
        finally:
            self.lock_buttons = False

    def flash_eeprom(self):
        """Flash the EEPROM (FRU)"""
        required = {
            "Host IP": self.your_ip.get(),
            "Serial Device": self.serial_device.get()
        }
        if self._run_operation(
            self.run_flash_eeprom,
            required_fields=required,
            error_msg="Please enter Host IP and select a serial device"
        ):
            self.log_message("Starting EEPROM flashing operation...")
    

    async def run_flash_eeprom(self):
        """Run flash EEPROM operation with strict filename validation"""
        try:
            # Select EEPROM file with specific filter
            file_path = FileSelectionHelper.select_file(
                self.root,
                "Select EEPROM (FRU) File", 
                self.last_eeprom_dir,
                "FRU files (fru.bin) | fru.bin"
            )
            
            if not file_path:
                self.log_message("No file selected for EEPROM flashing. Process aborted.")
                self.lock_buttons = False
                return
            
            # Validate filename - STRICT validation for EEPROM files
            filename = os.path.basename(file_path)
            
            if filename != "fru.bin":
                self.log_message(f" ERROR: Invalid EEPROM file selected!")
                self.log_message(f"Selected file: '{filename}'")
                self.log_message(f"Required file: 'fru.bin'")
                self.log_message(" EEPROM flashing ABORTED for safety!")
                
                # Show error dialog to user
                from tkinter import messagebox
                messagebox.showerror(
                    "Invalid EEPROM File", 
                    f"Invalid EEPROM file selected: '{filename}'\n\n"
                    f"Only 'fru.bin' files are allowed for EEPROM flashing.\n\n"
                    f"Please select the correct fru.bin file and try again."
                )
                
                self.lock_buttons = False
                return
                
            # Update last used directory
            self.last_eeprom_dir = os.path.dirname(file_path)
            self.save_config()
            
            self.flash_file = file_path
            self.log_message(f"✓ Valid EEPROM file selected: {filename}")
            self.log_message(f"File path: {file_path}")
            
            # Run the flashing process
            await bmc.flash_eeprom(
                self.flash_file, 
                self.your_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get(),
                self.username.get(),
                self.password.get(),
            )
        except Exception as e:
            self.log_message(f"Error during EEPROM flashing: {e}")
        finally:
            self.lock_buttons = False
            
    def on_flash_all(self):
        """Open the Flash All window"""
        if self.bmc_type.get() == 0:
            messagebox.showerror("Error", "Please select a BMC type before proceeding.")
            return
            
        # Show bootloader warning popup
        if not messagebox.askyesno("Bootloader Warning", 
                                "WARNING: The Flash All operation requires your system to be at the U-Boot bootloader prompt.\n\n"
                                "Have you already rebooted to the bootloader?\n\n"
                                "If not, please use the 'Reboot to Bootloader' button first."):
            self.log_message("Flash All operation cancelled - system not in bootloader.")
            return
            
        FlashAllWindow(self.root, self.bmc_type.get(), self)

    def update_bios(self):
        """Update BIOS firmware"""
        required = {
            "Username": self.username.get(),
            "Password": self.password.get(),
            "BMC IP": self.bmc_ip.get()
        }
        self._run_operation(
            self.run_update_bios,
            required_fields=required,
            error_msg="Please enter all required fields: Username, Password, BMC IP"
        )

    async def run_update_bios(self):
        """Run BIOS or BMC update operation with detection"""
        try:
            self.flash_file = FileSelectionHelper.select_file(
                self.root, 
                "Select Firmware File",
                self.last_firmware_dir,
                "Firmware Files (*.tar.gz) | *.tar.gz"
            )
            
            if not self.flash_file:
                self.log_message("Update cancelled: No file selected.")
                return

            # --- DETECTION LOGIC ---
            filename = self.flash_file.lower()
            is_bmc = "bmc" in filename or "fw" in filename
            # You can add more specific vendor keywords here (e.g., 'idrac', 'ilo', 'ast2500')
            
            update_type = "BMC" if is_bmc else "BIOS"
            warning_msg = (
                f"You have selected a {update_type} firmware.\n\n"
                f"The {update_type} will restart after the update. "
                "You will lose connection to the web interface for 3-5 minutes.\n\n"
                "Do you want to proceed?"
            ) if is_bmc else (
                "You have selected a BIOS firmware.\n\n"
                "This process takes up to 7 minutes and requires a system restart.\n\n"
                "Do NOT power off. Proceed?"
            )

            # Show the specific popup
            if not messagebox.askyesno(f"Confirm {update_type} Update", warning_msg):
                self.log_message(f"{update_type} update cancelled.")
                return
            # -----------------------

            with open(self.flash_file, 'rb') as fw_file:
                fw_content = fw_file.read()
                self.log_message(f"Starting {update_type} update process...")
                is_bmc_file = "bmc" in self.flash_file.lower()
                update_func = bmc.bmc_update if is_bmc_file else bmc.bios_update

                await update_func(
                    self.username.get(),
                    self.password.get(),
                    self.bmc_ip.get(),
                    fw_content,
                    self.update_progress,
                    self.log_message,
                )
        except Exception as e:
            self.log_message(f"Error during update: {e}")
        finally:
            self.bios_update_running = False
        

    def reboot_to_bootloader(self):
        """Reboot the OpenBMC to bootloader (U-Boot)"""
        required = {"Serial Device": self.serial_device.get()}
        if self._run_operation(
            self.run_reboot_to_bootloader,
            required_fields=required,
            error_msg="Please select a serial device before attempting to reboot to bootloader"
        ):
            self.log_message("Sending reboot to U-Boot command...")

    async def run_reboot_to_bootloader(self):
        """Run reboot to U-Boot bootloader operation for OpenBMC"""
        try:
            # Call the OpenBMC-specific reset to U-Boot function
            await bmc.reset_to_uboot(self.log_message, self.serial_device.get())
            
            # Inform user about U-Boot interaction
            self.log_message("System should now be at the U-Boot prompt")
            self.log_message("TIP: Use the Console button to interact with U-Boot if needed")
                
        except Exception as e:
            self.log_message(f"Error rebooting to bootloader: {e}")
        finally:
            self.lock_buttons = False

    def auto_set_password(self):
        """Manually run a login + password-change sequence on demand:
        logs in with the current Connection Settings username/password,
        then resets the BMC's password to the configured default password
        (see run_auto_set_password for why this direction, as opposed to
        the automatic Flash All + FRU / Flash eMMC flows)."""
        required = {"Serial Device": self.serial_device.get(), "Password": self.password.get()}
        if self._run_operation(
            self.run_auto_set_password,
            required_fields=required,
            error_msg="Please select a serial device and enter a password in Connection Settings first"
        ):
            self.log_message("Running login + password setup sequence...")

    async def run_auto_set_password(self):
        """Run the manual auto-set-password sequence.

        Note this runs in the OPPOSITE direction from the automatic
        Flash All + FRU / Flash eMMC flows: those log in with the default
        password and set it to Connection Settings' password (a freshly
        flashed BMC really does start on the factory default). This
        button instead logs in with Connection Settings' current
        username/password and sets it TO the configured default password
        - for resetting a BMC that's already on a known working password
        back to the standard default.
        """
        try:
            await bmc.post_flash_login_and_password_sequence(
                self.username.get(),
                self.password.get(),
                self.post_flash_default_password.get(),
                self.serial_device.get(),
                self.log_message,
            )
        except Exception as e:
            self.log_message(f"Error during password setup: {e}")
        finally:
            self.lock_buttons = False


    def connect_console(self):
        """Connect the embedded console to the selected serial device"""
        if not self.serial_device.get():
            self.log_message("No serial device selected. Please select a device.")
            return
            
        if self.embedded_console.connect(self.serial_device.get()):
            # Start processing the queue
            self.embedded_console.process_serial_queue()
            self.log_message(f"Console connected to {self.serial_device.get()}")

    def disconnect_console(self):
        """Disconnect the embedded console"""
        self.embedded_console.disconnect()
        self.log_message("Console disconnected")

    def clear_console(self):
        """Clear the embedded console"""
        self.embedded_console.clear()
        self.log_message("Console cleared")

        
    async def run_set_bmc_ip(self):
        """Run set BMC IP operation with Web UI hyperlink update"""
        try:
            await set_ip(
                self.bmc_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get(),
                self.username.get(),
                self.password.get(),
            )
            
            # Add notification about Web UI after IP is set
            self.log_message(f"IP set successfully to {self.bmc_ip.get()}")
            self.log_message("You can now access the BMC Web UI through your browser.")
            
        except Exception as e:
            self.log_message(f"Error during IP setup: {e}")
        finally:
            self.lock_buttons = False

    def set_home_directory(self):
        """Let the user explicitly set the base directory for all file dialogs."""
        # Start at the currently set home, or fallback to the OS real home
        start_dir = getattr(self, 'user_home_dir', "")
        if not start_dir or not os.path.exists(start_dir):
            start_dir = FileSelectionHelper.get_real_home()
            
        new_home = FileSelectionHelper.select_directory(
            self.root, 
            "Select Master Home Directory", 
            start_dir
        )
        
        if new_home:
            self.user_home_dir = new_home
            # Immediately override all individual trackers
            self.last_firmware_dir = new_home
            self.last_fip_dir = new_home
            self.last_eeprom_dir = new_home
            
            # Save the new configuration
            self.save_config()
            self.log_message(f"🏠 Master home directory updated to: {new_home}")

    def stop_operation(self):
            """Stops any running operations, cleans up, and restarts the application."""
            import sys
            
            self.log_message("\n STOP OPERATION REQUESTED! Cleaning up...")
            self.abort_requested = True  # Signal any loops to break
            
            # 1. Force close serial connections so the port is free for the next instance
            try:
                cleanup_all_serial_connections()
            except Exception:
                pass
                
            # 2. Gracefully stop the HTTP server so port 80 is freed up
            try:
                stop_server_dmi(self.log_message)
            except Exception:
                pass
                
            # 3. Save current configurations before rebooting
            try:
                self.save_config()
            except Exception:
                pass
                
            self.log_message("Restarting application in 2 seconds...")
            self.root.update()  # Force the UI to update so the user sees the message
            time.sleep(2)
            
            # 4. Restart the app by replacing the current process with a new one
            os.execv(sys.executable, [sys.executable] + sys.argv)
                

def main():
    """Main entry point for the application"""
    global app  # Ensure app is accessible globally for child windows
    app = PlatypusApp()
    app.root.mainloop()

if __name__ == "__main__":
    main()