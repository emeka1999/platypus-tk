import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import asyncio, glob, bmc, json, os, time, psutil, threading, subprocess
import serial
from utils import *
from network import *
from functools import partial
from threading import Thread
import tempfile
import atexit
from http.server import SimpleHTTPRequestHandler, HTTPServer
import functools # <-- Import functools

try:
    from extra import create_multi_unit_window
    MULTI_UNIT_AVAILABLE = True
except ImportError:
    MULTI_UNIT_AVAILABLE = False
    print("Multi-unit functionality not available (extra.py not found)")

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
    def select_file(parent, title, last_dir, file_filter=None):
        """Generic file selection with fallbacks for platform compatibility"""
        file_path = ""
        
        # Try zenity first with proper file filter formatting
        try:
            filter_params = []
            if file_filter:
                # Convert our filter format to zenity format
                # Example: "Binary files (*.bin) | *.bin" -> "*.bin"
                if '|' in file_filter:
                    zenity_filter = file_filter.split('|')[-1].strip()
                else:
                    zenity_filter = file_filter
                filter_params = ['--file-filter', zenity_filter]
                
            result = subprocess.run(
                ['zenity', '--file-selection', f'--filename={last_dir}/', 
                 f'--title={title}'] + filter_params,
                capture_output=True, text=True
            )
            if result.returncode == 0:
                file_path = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # Try kdialog next with proper filter formatting
            try:
                if file_filter:
                    # Convert our filter to kdialog format
                    if '|' in file_filter:
                        # Extract the pattern part after |
                        pattern = file_filter.split('|')[-1].strip()
                        # KDialog expects format like "*.bin *.tar.gz"
                        kdialog_filter = pattern
                    else:
                        kdialog_filter = file_filter
                else:
                    kdialog_filter = '*'
                    
                result = subprocess.run(
                    ['kdialog', '--getopenfilename', last_dir, kdialog_filter],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    file_path = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # Fall back to a simple CustomTkinter dialog
                file_path = FileSelectionHelper._show_entry_dialog(
                    parent, title, last_dir, f"Enter the full path to {title.lower()}:", file_filter
                )
                
        return file_path
        
    @staticmethod
    def select_directory(parent, title, last_dir):
        """Generic directory selection with fallbacks for platform compatibility"""
        directory = ""
        
        # Try zenity first (removed timeout)
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--directory', 
                 f'--filename={last_dir}/', f'--title={title}'],
                capture_output=True, text=True
                # Removed timeout=10 to allow unlimited time for selection
            )
            if result.returncode == 0:
                directory = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # Try kdialog next (removed timeout)
            try:
                result = subprocess.run(
                    ['kdialog', '--getexistingdirectory', last_dir, title],
                    capture_output=True, text=True
                    # Removed timeout=10 to allow unlimited time for selection
                )
                if result.returncode == 0:
                    directory = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # Fall back to a simple CustomTkinter dialog
                directory = FileSelectionHelper._show_entry_dialog(
                    parent, title, last_dir, "Enter the full path to directory:"
                )
                
        return directory
        
    @staticmethod
    def _show_entry_dialog(parent, title, default_value, message, file_filter=None):
        """Helper method to show a simple input dialog with better UX"""
        dialog = ctk.CTkToplevel(parent)
        dialog.title(title)
        dialog.geometry("600x200")  # Made slightly larger
        dialog.attributes('-topmost', True)
        
        # Make dialog resizable
        dialog.resizable(True, True)
        
        path_var = ctk.StringVar(value=default_value)
        
        # Add some padding and better layout
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Show file filter info if provided
        if file_filter:
            filter_info = f"{message}\n\nExpected file type: {file_filter}"
        else:
            filter_info = message
            
        ctk.CTkLabel(main_frame, text=filter_info, wraplength=500).pack(pady=10)
        
        # Entry with better visibility
        entry = ctk.CTkEntry(main_frame, textvariable=path_var, width=500, height=32)
        entry.pack(pady=10, fill="x")
        entry.focus_set()  # Focus on the entry field
        
        # Add browse button for convenience
        browse_frame = ctk.CTkFrame(main_frame)
        browse_frame.pack(fill="x", pady=5)
        
        def browse_for_path():
            """Allow user to browse instead of typing path"""
            try:
                if "directory" in message.lower():
                    # Use a simple directory browser
                    result = subprocess.run(
                        ['zenity', '--file-selection', '--directory', f'--title=Browse for {title}'],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        path_var.set(result.stdout.strip())
                else:
                    # Use a simple file browser with filter if available
                    cmd = ['zenity', '--file-selection', f'--title=Browse for {title}']
                    if file_filter and '|' in file_filter:
                        pattern = file_filter.split('|')[-1].strip()
                        cmd.extend(['--file-filter', pattern])
                    
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0:
                        path_var.set(result.stdout.strip())
            except:
                pass  # Ignore if zenity not available
        
        ctk.CTkButton(browse_frame, text="Browse...", command=browse_for_path, width=100).pack(side="right")
        
        result_path = []  # Use a list to store the result
        
        def on_ok():
            path = path_var.get().strip()
            if path and (os.path.exists(path) or "Enter the full path" in message):
                result_path.append(path)
                dialog.destroy()
            else:
                # Show error message
                error_label = ctk.CTkLabel(main_frame, text="⚠️ Path does not exist!", text_color="red")
                error_label.pack(pady=5)
                dialog.after(3000, error_label.destroy)  # Remove error after 3 seconds
        
        def on_cancel():
            dialog.destroy()
        
        # Handle Enter key
        def on_enter(event):
            on_ok()
        
        entry.bind('<Return>', on_enter)
        
        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(fill="x", pady=10)
        ctk.CTkButton(button_frame, text="OK", command=on_ok, width=100).pack(side="left", padx=20)
        ctk.CTkButton(button_frame, text="Cancel", command=on_cancel, width=100).pack(side="right", padx=20)
        
        # Center the dialog
        dialog.update_idletasks()
        width = dialog.winfo_width()
        height = dialog.winfo_height()
        x = (dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (dialog.winfo_screenheight() // 2) - (height // 2)
        dialog.geometry(f'{width}x{height}+{x}+{y}')
        
        dialog.grab_set()  # Make dialog modal
        dialog.wait_window()  # Wait for dialog to close
        
        return result_path[0] if result_path else ""

class FlashAllWindow(ctk.CTkToplevel):
    def __init__(self, parent, bmc_type, app_instance):
        super().__init__(parent)
        self.bmc_type = bmc_type
        self.parent = parent
        self.app_instance = app_instance  # Store the app instance
        self.title("Select Files for Flashing")
        self.geometry("500x450") # Increased height for checkbox
        
        # Set parent relationship but DON'T make it modal
        self.transient(parent)
        
        # Initialize variables
        self.firmware_folder = ctk.StringVar()
        self.fip_file = ctk.StringVar()
        self.eeprom_file = ctk.StringVar()
        self.flash_fru_var = ctk.BooleanVar() # For the checkbox
        
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
                self.log_message(f"❌ Invalid FIP file: '{filename}'")
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
            
            self.log_message(f"✓ Valid FIP file selected: {filename}")

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
                self.log_message(f"❌ Invalid EEPROM file: '{filename}'")
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
        
        # Pass do_flash_fru to the sequence
        threading.Thread(target=self.run_flash_sequence, args=(do_flash_fru,), daemon=True).start()
        self.destroy()  # Close the window when starting the flashing
    
    def run_flash_sequence(self, do_flash_fru):
        """Execute the full flashing sequence by calling the main app's method"""
        # Get required parameters
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
            do_flash_fru  # Pass the boolean flag
        )
        

class PlatypusApp:

    def __init__(self):
        """Initialize the application with auto-opening console"""
        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Create main window with specific class name
        self.root = ctk.CTk(className="PlatypusApp")  # Set class name during creation
        self.root.title("Platypus BMC Management")
        self.root.geometry("800x850")  # Adjusted to fit 1080p
        
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
        
        # Use main_container as controls_frame (no split layout anymore)
        self.controls_frame = self.main_container
        
        # Create UI sections in the controls frame
        self.create_connection_section()
        
        # --- Create Main Tab View ---
        self.main_tab_view = ctk.CTkTabview(self.controls_frame)
        self.main_tab_view.pack(fill="x", expand=False, padx=0, pady=5)
        
        self.main_tab_view.add("BMC Flashing")
        self.main_tab_view.add("FRU Data Flasher")
        
        # Get tab frames
        self.bmc_flashing_tab = self.main_tab_view.tab("BMC Flashing")
        self.fru_data_flasher_tab = self.main_tab_view.tab("FRU Data Flasher")
        
        # Populate tabs
        self.create_main_flashing_tab(self.bmc_flashing_tab)
        self.create_dmi_flasher_tab(self.fru_data_flasher_tab)
        
        self.create_log_section()
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
        
        # Update device list
        devices_found = self.refresh_devices()
        
        # Just log the status without auto-opening console
        if devices_found:
            self.log_message("Serial devices detected. Use 'Console' button to open when needed.")
        else:
            self.log_message("No serial devices found. Please connect a device and click 'Refresh'.")
                
        def auto_open_console(self):
            """Automatically open console if a serial device is available"""
            if self.serial_device.get():
                self.log_message("Auto-opening console...")
                self.open_minicom_console()
            else:
                self.log_message("No serial device selected. Console not auto-opened.")
                # Optionally, show a message to the user
                from tkinter import messagebox
                messagebox.showinfo(
                    "Console Not Opened",
                    "No serial device detected. Please select a device and click 'Console' to open."
                )


    def on_window_resize(self, event):
        """Handle window resize events to maintain proper layout"""
        # Only process if it's the main window being resized
        if event.widget == self.root:
            # Maintain a reasonable minimum size
            if event.width < 900:
                self.root.geometry(f"900x{event.height}")
            if event.height < 600:
                self.root.geometry(f"{event.width}x600")
                
            # Adjust column weights if needed
            try:
                # Get current width
                total_width = self.main_container.winfo_width()
                
                # Adjust column weights to maintain relative sizes
                if total_width > 0:
                    # We want console to be about 1/4 of the total width
                    console_width = int(total_width * 0.28)
                    controls_width = total_width - console_width
                    
                    self.main_container.columnconfigure(0, minsize=console_width)
                    self.main_container.columnconfigure(1, minsize=controls_width)
            except (AttributeError, tk.TclError):
                # This can happen during initialization or teardown
                pass

    
    def _init_variables(self):
        """Initialize all application variables"""
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

    def execute_flash_all(self, firmware_folder, fip_file, eeprom_file=None, bmc_type=2, do_flash_fru=True):
        """
        Execute the complete flash all sequence using the provided files.
        This method should be called from the FlashAllWindow.
        """
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
                
            asyncio.run(bmc.flash_emmc(
                self.bmc_ip.get(), 
                firmware_folder, 
                self.your_ip.get(), 
                self.bmc_type.get(), 
                emmc_progress_callback,
                self.log_message,
                self.serial_device.get()
            ))
            
            # Step 2: Login to BMC
            current_step = 2
            step_name = step_names[current_step]
            self.log_message(f"\n[STEP {current_step}/{total_steps}] {step_name.upper()}")
            self.log_message("-" * 30)
            update_overall_progress(0.0, current_step, step_name)
            
            login_result = asyncio.run(login(
                self.username.get(), 
                self.password.get(), 
                self.serial_device.get(), 
                self.log_message
            ))
            
            if login_result and "successful" in login_result.lower():
                update_overall_progress(1.0, current_step, step_name)
            else:
                self.log_message("⚠️  Login may have failed, but continuing...")
                update_overall_progress(1.0, current_step, step_name)
            
            # Step 3: Set BMC IP
            current_step = 3
            step_name = step_names[current_step]
            self.log_message(f"\n[STEP {current_step}/{total_steps}] {step_name.upper()}")
            self.log_message("-" * 30)
            
            def ip_progress_callback(progress):
                update_overall_progress(progress, current_step, step_name)
                
            asyncio.run(set_ip(
                self.bmc_ip.get(), 
                ip_progress_callback,
                self.log_message, 
                self.serial_device.get()
            ))
            
            # Step 4: Flash U-Boot (FIP)
            current_step = 4
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
            
            # Step 5: Flash EEPROM (if needed and requested)
            if bmc_type != 1 and eeprom_file and do_flash_fru:
                current_step = 5
                step_name = step_names[current_step]
                self.log_message(f"\n[STEP {current_step}/{total_steps}] {step_name.upper()}")
                self.log_message("-" * 30)
                
                def eeprom_progress_callback(progress):
                    update_overall_progress(progress, current_step, step_name)
                    
                asyncio.run(bmc.flash_eeprom(
                    eeprom_file, 
                    self.your_ip.get(), 
                    eeprom_progress_callback,
                    self.log_message, 
                    self.serial_device.get()
                ))
            elif bmc_type != 1:
                self.log_message(f"\n[STEP 5/{total_steps}] Skipping EEPROM Flash (as requested).")
            
            # Complete - set progress to 100%
            self.update_progress(1.0)
            self.log_message("\n" + "=" * 50)
            self.log_message("🎉 FLASH ALL SEQUENCE COMPLETED SUCCESSFULLY!")
            self.log_message("=" * 50)
            
            # Reset progress after a brief delay
            def reset_progress():
                import time
                time.sleep(3)
                self.update_progress(0)
            
            import threading
            threading.Thread(target=reset_progress, daemon=True).start()
            
        except Exception as e:
            self.log_message(f"\n❌ ERROR during Flash All sequence at Step {current_step}: {str(e)}")
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
            "EE2100", "EE2200", "EE1130", "EE1150",
            "EE1170", "EE2300"
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
        """
        Force close any processes using port 80.
        This is a more aggressive approach than cleanup_server_processes.
        """
        self.log_message("Forcibly closing any processes using port 80...")
        try:
            # Try using lsof command to find processes
            try:
                result = subprocess.run(
                    ["lsof", "-i", ":80", "-t"], 
                    capture_output=True, 
                    text=True, 
                    timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    pids = result.stdout.strip().split('\n')
                    for pid in pids:
                        if pid:
                            self.log_message(f"Killing process {pid} using port 80")
                            subprocess.run(["kill", "-9", pid], timeout=2)
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
                
            # Try using netstat command (alternative approach)
            try:
                result = subprocess.run(
                    ["netstat", "-tulpn"], 
                    capture_output=True, 
                    text=True, 
                    timeout=5
                )
                if result.returncode == 0:
                    import re
                    # Look for lines with :80 and extract PID
                    pattern = r'tcp\s+.*:80\s+.*LISTEN\s+(\d+)/'
                    matches = re.findall(pattern, result.stdout)
                    for pid in matches:
                        self.log_message(f"Killing process {pid} using port 80")
                        subprocess.run(["kill", "-9", pid], timeout=2)
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
            
            # Use psutil as another alternative
            for proc in psutil.process_iter(['pid', 'name', 'connections']):
                try:
                    for conn in proc.info['connections']:
                        if hasattr(conn, 'laddr') and hasattr(conn.laddr, 'port') and conn.laddr.port == 80:
                            self.log_message(f"Killing process {proc.info['pid']} ({proc.info['name']}) using port 80")
                            psutil.Process(proc.info['pid']).kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, KeyError, AttributeError):
                    pass
                    
            self.log_message("Port 80 should now be available")
        except Exception as e:
            self.log_message(f"Error while closing port 80 processes: {e}")

        # Give a brief pause to ensure processes are fully terminated
        time.sleep(1)

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

    def create_connection_section(self):
        """Create the connection settings section with optimized spacing"""
        section = ctk.CTkFrame(self.controls_frame)
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="Connection Settings", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
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
        
        # Title frame with Web UI hyperlink
        title_frame = ctk.CTkFrame(section)
        title_frame.pack(fill="x", pady=5)
        
        ctk.CTkLabel(title_frame, text="BMC Operations", 
                    font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=10)
        
        # Create hyperlink frame
        self.webui_link_frame = ctk.CTkFrame(title_frame, fg_color="transparent")
        self.webui_link_frame.pack(side="right", padx=10)
        
        # Initially show "No IP Set" text
        self.webui_status_label = ctk.CTkLabel(
            self.webui_link_frame, 
            text="Web UI: No IP Set",
            text_color="#999999",
            font=ctk.CTkFont(size=12)
        )
        self.webui_status_label.pack(side="right")
        
        # Create the hyperlink (initially hidden)
        self.webui_link = ctk.CTkLabel(
            self.webui_link_frame, 
            text="Open Web UI",
            text_color="#3a8eff",  # Hyperlink blue color
            font=ctk.CTkFont(size=12, underline=True),
            cursor="hand2"  # Hand cursor on hover
        )
        # Don't pack it yet - we'll show it when an IP is set
        
        # Bind click event to the hyperlink
        self.webui_link.bind("<Button-1>", self.open_web_ui)
        
        # If we already have an IP, show the hyperlink immediately
        if self.bmc_ip.get():
            self.update_webui_link(self.bmc_ip.get())
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=10)
        
        ops = [
            ("Update BMC", self.update_bmc),
            ("Update BIOS", self.update_bios),
            ("Login to BMC", self.login_to_bmc),
            ("Set BMC IP", self.set_bmc_ip),
            ("Power ON Host", self.power_on_host),
            ("Reboot BMC", self.reboot_bmc),
            ("Factory Reset", self.factory_reset)
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            ctk.CTkButton(op_frame, text=text, command=command, height=28).grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

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
            ("Reboot to Bootloader", self.reboot_to_bootloader)
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            button = ctk.CTkButton(op_frame, text=text, command=command, height=28)
            button.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
            
            # Special styling for Multi-Unit Flash button
            if text == "Multi-Unit Flash":
                button.configure(fg_color="#2B5CE6", hover_color="#1E3A8A", 
                            text_color="white", font=ctk.CTkFont(weight="bold"))
        
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

    def create_log_section(self):
        """Create the log section with reduced height"""
        section = ctk.CTkFrame(self.controls_frame)  # Changed from self.main_frame to self.controls_frame
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="Log", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        self.log_box = ctk.CTkTextbox(section, height=150, state="disabled")  # Reduced height from 200
        self.log_box.pack(padx=10, pady=5, fill="x")

    def create_progress_section(self):
        """Create the progress section with the console button restored"""
        section = ctk.CTkFrame(self.controls_frame)
        section.pack(fill="x", pady=5)
        
        progress_frame = ctk.CTkFrame(section)
        progress_frame.pack(fill="x", padx=10, pady=5)
        
        # Restore the Console button
        ctk.CTkButton(progress_frame, text="Console", command=self.open_minicom_console, height=28).pack(side="left", padx=5)
        
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
        
        # Return whether we found any devices (useful for auto-open console)
        return len(devices) > 0

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
                self.log_message("❌ No network interfaces found")
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
            self.log_message(f"❌ Error updating network interfaces: {e}")
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
            self.log_message(f"Launching Minicom on {self.serial_device.get()}...")
            
            # Clean up any existing minicom processes first
            self.cleanup_minicom_processes()
            
            # Try terminator first (as requested)
            try:
                process = subprocess.Popen(
                    ["terminator", "-e", f"minicom -D {self.serial_device.get()}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.log_message(f"Minicom launched successfully (PID: {process.pid})")
            except Exception:
                # Fall back to xterm if terminator fails
                process = subprocess.Popen(
                    ["xterm", "-e", f"minicom -D {self.serial_device.get()}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.log_message(f"Minicom launched in xterm (PID: {process.pid})")
                
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

    def force_close_port_80(self):
        """
        Force close any processes using port 80 with better error handling.
        """
        self.log_message("Forcibly closing any processes using port 80...")
        try:
            # Method 1: Use lsof command
            try:
                result = subprocess.run(
                    ["lsof", "-i", ":80", "-t"], 
                    capture_output=True, 
                    text=True, 
                    timeout=10  # Add timeout
                )
                if result.returncode == 0 and result.stdout.strip():
                    pids = result.stdout.strip().split('\n')
                    for pid in pids:
                        if pid and pid.isdigit():
                            self.log_message(f"Killing process {pid} using port 80")
                            try:
                                subprocess.run(["kill", "-9", pid], timeout=5)
                            except subprocess.TimeoutExpired:
                                self.log_message(f"Timeout killing process {pid}")
            except (subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError):
                pass
            
            # Method 2: Use psutil with better error handling
            try:
                for proc in psutil.process_iter(['pid', 'name', 'connections']):
                    try:
                        connections = proc.info.get('connections', [])
                        if connections:
                            for conn in connections:
                                if (hasattr(conn, 'laddr') and 
                                    hasattr(conn.laddr, 'port') and 
                                    conn.laddr.port == 80):
                                    self.log_message(f"Killing process {proc.info['pid']} ({proc.info['name']}) using port 80")
                                    psutil.Process(proc.info['pid']).kill()
                                    break
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, 
                            KeyError, AttributeError, TypeError):
                        pass
            except Exception as e:
                self.log_message(f"Error using psutil method: {e}")
                
            self.log_message("Port 80 cleanup completed")
        except Exception as e:
            self.log_message(f"Error while closing port 80 processes: {e}")

        # Give a brief pause to ensure processes are fully terminated
        time.sleep(1)
    
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
    
    def update_bmc(self):
        """Update BMC firmware"""
        required = {
            "Username": self.username.get(),
            "Password": self.password.get(),
            "BMC IP": self.bmc_ip.get()
        }
        self._run_operation(
            self.run_update_bmc,
            required_fields=required,
            error_msg="Please enter all required fields: Username, Password, BMC IP"
        )

    async def run_update_bmc(self):
        """Run BMC update operation"""
        try:
            # Select firmware file with specific filter
            self.flash_file = FileSelectionHelper.select_file(
                self.root, 
                "Select BMC Firmware",
                self.last_firmware_dir,
                "BMC Firmware (*.tar.gz) | *.tar.gz"
            )
            
            if not self.flash_file:
                self.log_message("No firmware file selected.")
                self.lock_buttons = False
                return
                
            with open(self.flash_file, 'rb') as fw_file:
                fw_content = fw_file.read()
                self.log_message("Starting BMC Update...")

                await bmc.bmc_update(
                    self.username.get(),
                    self.password.get(),
                    self.bmc_ip.get(),
                    fw_content,
                    self.update_progress,
                    self.log_message,
                )
        except Exception as e:
            self.log_message(f"Error during BMC update: {e}")
        finally:
            self.lock_buttons = False

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


    def set_bmc_ip(self):
        """Set BMC IP address"""
        required = {"BMC IP": self.bmc_ip.get(), "Serial Device": self.serial_device.get()}
        if self._run_operation(
            self.run_set_bmc_ip,
            required_fields=required,
            error_msg="Please enter BMC IP and select a serial device"
        ):
            self.log_message(f"Setting BMC IP to {self.bmc_ip.get()}...")
            
            # Enable the Web UI button when IP is being set
            if hasattr(self, 'open_web_ui_button'):
                self.open_web_ui_button.configure(state="normal")

    async def run_set_bmc_ip(self):
        """Run set BMC IP operation"""
        try:
            await set_ip(
                self.bmc_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
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
                self.serial_device.get()
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
                    self.log_message(f"❌ ERROR: Invalid FIP file selected!")
                    self.log_message(f"Selected file: '{filename}'")
                    self.log_message(f"Allowed files: {', '.join(allowed_fip_files)}")
                    self.log_message("⚠️  FIP flashing ABORTED for safety!")
                    
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
                self.log_message(f"❌ ERROR: Invalid EEPROM file selected!")
                self.log_message(f"Selected file: '{filename}'")
                self.log_message(f"Required file: 'fru.bin'")
                self.log_message("⚠️  EEPROM flashing ABORTED for safety!")
                
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
                self.serial_device.get()
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
        """Run BIOS update operation"""
        try:
            # Select firmware file with specific filter
            self.flash_file = FileSelectionHelper.select_file(
                self.root, 
                "Select BIOS Firmware",
                self.last_firmware_dir,
                "BIOS Firmware (*.tar.gz) | *.tar.gz"
            )
            
            if not self.flash_file:
                self.log_message("No BIOS firmware file selected.")
                self.lock_buttons = False
                return
                
            # Display confirmation message due to lengthy update process
            if not messagebox.askyesno("Confirm BIOS Update", 
                                    "BIOS update can take up to 7 minutes and requires system restart.\n\n"
                                    "During this process, do not power off the system or interrupt the update.\n\n"
                                    "Do you want to continue?"):
                self.log_message("BIOS update cancelled by user.")
                self.lock_buttons = False
                return
            
            self.log_message("BIOS update started. This will take approximately 7 minutes.")
            self.log_message("WARNING: Do NOT interrupt the power during this process!")
                
            with open(self.flash_file, 'rb') as fw_file:
                fw_content = fw_file.read()
                self.log_message("Uploading BIOS firmware image (tar.gz) to BMC...")

                await bmc.bios_update(
                    self.username.get(),
                    self.password.get(),
                    self.bmc_ip.get(),
                    fw_content,
                    self.update_progress,
                    self.log_message,
                )
        except Exception as e:
            self.log_message(f"Error during BIOS update: {e}")
        finally:
            self.lock_buttons = False

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

    def auto_connect_console(self):
        """Automatically connect to the console if a device is selected"""
        if self.serial_device.get():
            self.connect_console()
        else:
            # Try again in a second if no device is selected yet
            self.root.after(1000, self.auto_connect_console)

    def update_webui_link(self, ip_address):
        """Update the Web UI hyperlink with the current IP address"""
        # Hide the status label
        self.webui_status_label.pack_forget()
        
        # Update the hyperlink text and show it
        self.webui_link.configure(text=f"Open Web UI (https://{ip_address})")
        self.webui_link.pack(side="right")

    def open_web_ui(self, event=None):
        """Open BMC Web UI in Firefox (Snap version with lock cleanup)"""
        if not self.bmc_ip.get():
            self.log_message("Error: BMC IP is not set. Please set the IP first.")
            return
        
        # Format the URL with https prefix
        bmc_url = f"https://{self.bmc_ip.get()}"
        self.log_message(f"Opening BMC Web UI in Firefox (Snap): {bmc_url}")
        
        # Copy to clipboard for convenience
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(bmc_url)
            self.log_message("URL copied to clipboard as backup")
        except Exception:
            pass
        
        import subprocess
        import os
        import threading
        import glob
        from tkinter import messagebox
        
        def get_real_user():
            """Get the real user who launched the app (when running as root)"""
            real_user = None
            
            # Try multiple methods to get real user
            real_user = os.environ.get('SUDO_USER') or os.environ.get('PKEXEC_UID')
            
            # If PKEXEC_UID is a UID, convert to username
            if real_user and real_user.isdigit():
                import pwd
                try:
                    real_user = pwd.getpwuid(int(real_user)).pw_name
                except:
                    real_user = None
            
            # If still no user, try to detect from who command
            if not real_user:
                try:
                    who_output = subprocess.check_output(['who'], universal_newlines=True).strip()
                    for line in who_output.split('\n'):
                        if line and not line.startswith('root '):
                            real_user = line.split()[0]
                            break
                except:
                    pass
            
            return real_user
        
        def cleanup_firefox_locks(user):
            """Clean up Firefox lock files that prevent new instances"""
            self.log_message("Cleaning up Firefox lock files...")
            
            # Common Firefox profile locations for snap
            profile_paths = [
                f"/home/{user}/snap/firefox/common/.mozilla/firefox/",
                f"/home/{user}/.mozilla/firefox/",
                f"/home/{user}/snap/firefox/current/.mozilla/firefox/"
            ]
            
            cleaned_files = []
            
            for profile_base in profile_paths:
                try:
                    if os.path.exists(profile_base):
                        # Find all profile directories
                        profile_dirs = glob.glob(os.path.join(profile_base, "*.default*"))
                        
                        for profile_dir in profile_dirs:
                            # Lock files to remove
                            lock_files = [
                                os.path.join(profile_dir, "lock"),
                                os.path.join(profile_dir, ".parentlock"),
                                os.path.join(profile_dir, "parent.lock")
                            ]
                            
                            for lock_file in lock_files:
                                if os.path.exists(lock_file):
                                    try:
                                        # Use sudo to remove as the user
                                        subprocess.run(
                                            f"sudo -u {user} rm -f '{lock_file}'",
                                            shell=True,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL,
                                            timeout=3
                                        )
                                        cleaned_files.append(lock_file)
                                    except:
                                        # Try direct removal if sudo fails
                                        try:
                                            os.remove(lock_file)
                                            cleaned_files.append(lock_file)
                                        except:
                                            pass
                except Exception as e:
                    self.log_message(f"  Error cleaning {profile_base}: {e}")
            
            if cleaned_files:
                self.log_message(f"✓ Removed {len(cleaned_files)} lock file(s)")
                for file in cleaned_files:
                    self.log_message(f"  - {file}")
            else:
                self.log_message("  No lock files found to clean")
        
        def kill_zombie_firefox_processes(user):
            """Kill any zombie Firefox processes"""
            self.log_message("Checking for zombie Firefox processes...")
            
            killed_processes = []
            
            try:
                for proc in psutil.process_iter(['pid', 'name', 'username', 'status']):
                    if (proc.info['username'] == user and 
                        proc.info['name'] and 
                        'firefox' in proc.info['name'].lower()):
                        
                        try:
                            # Check if process is actually responsive
                            if proc.info['status'] == psutil.STATUS_ZOMBIE:
                                self.log_message(f"  Found zombie Firefox process: PID {proc.info['pid']}")
                                subprocess.run(f"sudo kill -9 {proc.info['pid']}", shell=True, timeout=2)
                                killed_processes.append(proc.info['pid'])
                            else:
                                # Try to send a test signal to see if it's responsive
                                try:
                                    os.kill(proc.info['pid'], 0)  # Test signal
                                except ProcessLookupError:
                                    # Process is dead but still listed
                                    killed_processes.append(proc.info['pid'])
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
            except Exception as e:
                self.log_message(f"  Error checking processes: {e}")
            
            if killed_processes:
                self.log_message(f"✓ Killed {len(killed_processes)} zombie process(es)")
            else:
                self.log_message("  No zombie processes found")
        
        def try_snap_firefox_new_tab(url, user):
            """Try to open new tab in existing Snap Firefox"""
            
            # Snap Firefox commands with new-window fallback
            snap_commands = [
                # Method 1: Try new-tab first
                f"sudo -u {user} snap run firefox --new-tab --url '{url}'",
                
            ]
            
            for i, cmd in enumerate(snap_commands, 1):
                try:
                    method_name = (
                        "new-tab" if "--new-tab" in cmd else
                        "new-window" if "--new-window" in cmd else
                        "new-instance" if "--new-instance" in cmd else
                        "no-remote" if "--no-remote" in cmd else
                        "direct"
                    )
                    
                    self.log_message(f"Firefox method {i} ({method_name}): Trying...")
                    
                    result = subprocess.run(
                        cmd,
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10
                    )
                    
                    if result.returncode == 0:
                        self.log_message(f"✓ Firefox launched successfully with {method_name}")
                        return True
                    else:
                        self.log_message(f"  Method {i} failed (code: {result.returncode})")
                        
                except subprocess.TimeoutExpired:
                    self.log_message(f"  Method {i} timed out")
                except Exception as e:
                    self.log_message(f"  Method {i} error: {e}")
                    
            return False
        
        def launch_snap_firefox():
            try:
                # Get real user
                real_user = get_real_user()
                
                if not real_user:
                    self.log_message("❌ Could not determine real user")

                    return False
                
                self.log_message(f"Real user: {real_user}")
                self.log_message("Preparing Firefox launch...")
                
                # Step 1: Clean up lock files
                cleanup_firefox_locks(real_user)
                
                # Step 2: Kill any zombie processes
                kill_zombie_firefox_processes(real_user)
                
                # Step 3: Wait a moment for cleanup to complete
                import time
                time.sleep(1)
                
                # Step 4: Try to launch Firefox
                self.log_message("Attempting to launch Firefox...")
                
                if try_snap_firefox_new_tab(bmc_url, real_user):
                    self.log_message("🎉 Firefox opened successfully!")
                    return True
                
                # All methods failed
                self.log_message("❌ All Firefox launch methods failed")
                
                # Show helpful instructions

                return False
                    
            except Exception as e:
                self.log_message(f"❌ Error during Firefox launch: {e}")
                

                return False
        
        # Launch Firefox in background thread
        threading.Thread(target=launch_snap_firefox, daemon=True).start()

    async def run_set_bmc_ip(self):
        """Run set BMC IP operation with Web UI hyperlink update"""
        try:
            await set_ip(
                self.bmc_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
            )
            
            # Add notification about Web UI after IP is set
            self.log_message(f"IP set successfully to {self.bmc_ip.get()}")
            self.log_message("You can now access the BMC Web UI through your browser.")
            
            # Update the Web UI hyperlink
            if hasattr(self, 'update_webui_link'):
                self.update_webui_link(self.bmc_ip.get())
                
        except Exception as e:
            self.log_message(f"Error during IP setup: {e}")
        finally:
            self.lock_buttons = False
            

def main():
    """Main entry point for the application"""
    global app  # Ensure app is accessible globally for child windows
    app = PlatypusApp()
    app.root.mainloop()

if __name__ == "__main__":
    main()