import asyncio
import threading
import time
import os
import glob
import json
from concurrent.futures import ThreadPoolExecutor
from socketserver import ThreadingMixIn
from http.server import SimpleHTTPRequestHandler, HTTPServer
import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import serial
import subprocess
import socket
import psutil

class RobustHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Enhanced HTTP request handler with better concurrent file serving capabilities"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def log_message(self, format, *args):
        """Override to suppress default logging (we'll handle our own)"""
        # Optionally log to our application logger instead
        pass
    
    def do_GET(self):
        """Handle GET requests with improved error handling and performance"""
        try:
            # Add cache control headers to prevent caching issues
            self.send_response(200)
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            
            # Get the requested file path
            file_path = self.translate_path(self.path)
            
            if os.path.exists(file_path) and os.path.isfile(file_path):
                # Get file size for Content-Length header
                file_size = os.path.getsize(file_path)
                self.send_header('Content-Length', str(file_size))
                
                # Determine content type
                if file_path.endswith('.xz'):
                    self.send_header('Content-Type', 'application/x-xz')
                elif file_path.endswith('.bin'):
                    self.send_header('Content-Type', 'application/octet-stream')
                elif file_path.endswith('.bmap'):
                    self.send_header('Content-Type', 'text/plain')
                elif file_path.endswith('.itb'):
                    self.send_header('Content-Type', 'application/octet-stream')
                else:
                    self.send_header('Content-Type', 'application/octet-stream')
                
                self.end_headers()
                
                # Stream file in chunks to handle large files efficiently
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)  # 8KB chunks
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()  # Ensure data is sent immediately
            else:
                # File not found
                self.send_error(404, f"File not found: {self.path}")
                
        except BrokenPipeError:
            # Client disconnected, nothing to do
            pass
        except Exception as e:
            try:
                self.send_error(500, f"Server error: {str(e)}")
            except:
                pass  # Connection might be broken

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP Server that handles each request in a separate thread"""
    daemon_threads = True
    allow_reuse_address = True
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Increase the request queue size for better concurrent handling
        self.request_queue_size = 50

class RobustHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Enhanced HTTP request handler with better concurrent file serving capabilities"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def log_message(self, format, *args):
        """Override to suppress default logging (we'll handle our own)"""
        # Optionally log to our application logger instead
        pass
    
    def do_GET(self):
        """Handle GET requests with improved error handling and performance"""
        try:
            # Add cache control headers to prevent caching issues
            self.send_response(200)
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            
            # Get the requested file path
            file_path = self.translate_path(self.path)
            
            if os.path.exists(file_path) and os.path.isfile(file_path):
                # Get file size for Content-Length header
                file_size = os.path.getsize(file_path)
                self.send_header('Content-Length', str(file_size))
                
                # Determine content type
                if file_path.endswith('.xz'):
                    self.send_header('Content-Type', 'application/x-xz')
                elif file_path.endswith('.bin'):
                    self.send_header('Content-Type', 'application/octet-stream')
                elif file_path.endswith('.bmap'):
                    self.send_header('Content-Type', 'text/plain')
                elif file_path.endswith('.itb'):
                    self.send_header('Content-Type', 'application/octet-stream')
                else:
                    self.send_header('Content-Type', 'application/octet-stream')
                
                self.end_headers()
                
                # Stream file in chunks to handle large files efficiently
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)  # 8KB chunks
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()  # Ensure data is sent immediately
            else:
                # File not found
                self.send_error(404, f"File not found: {self.path}")
                
        except BrokenPipeError:
            # Client disconnected, nothing to do
            pass
        except Exception as e:
            try:
                self.send_error(500, f"Server error: {str(e)}")
            except:
                pass  # Connection might be broken

def read_serial_data(ser, command, timeout=10):
    """
    Send command to serial port and read response with timeout.
    This function handles serial communication for the multi-unit flashing.
    """
    try:
        # Clear any existing data in the buffer
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # Send the command
        if isinstance(command, str):
            command = command.encode('utf-8')
        
        ser.write(command)
        ser.flush()
        
        # Read response with timeout
        start_time = time.time()
        response_data = b''
        
        while (time.time() - start_time) < timeout:
            if ser.in_waiting > 0:
                chunk = ser.read(ser.in_waiting)
                response_data += chunk
                
                # Check for common completion indicators
                response_str = response_data.decode('utf-8', errors='ignore')
                if any(indicator in response_str.lower() for indicator in 
                      ['# ', '$ ', 'login:', 'password:', 'complete', 'done', 'finished', 'error']):
                    break
            else:
                time.sleep(0.1)  # Small delay to prevent busy waiting
        
        # Decode response
        response = response_data.decode('utf-8', errors='ignore')
        
        # Clean up the response
        response = response.strip()
        
        return response if response else "Command sent successfully"
        
    except serial.SerialException as e:
        return f"Serial Error: {str(e)}"
    except Exception as e:
        return f"Communication Error: {str(e)}"

class MultiUnitFlashWindow(ctk.CTkToplevel):
    """Window for managing multi-unit NanoBMC flashing operations"""

    @staticmethod
    def read_serial_data(ser, command, timeout=10):
        """
        Send command to serial port and read response with timeout.
        This function handles serial communication for the multi-unit flashing.
        """
        try:
            # Clear any existing data in the buffer
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            # Send the command
            if isinstance(command, str):
                command = command.encode('utf-8')
            
            ser.write(command)
            ser.flush()
            
            # Read response with timeout
            start_time = time.time()
            response_data = b''
            
            while (time.time() - start_time) < timeout:
                if ser.in_waiting > 0:
                    chunk = ser.read(ser.in_waiting)
                    response_data += chunk
                    
                    # Check for common completion indicators
                    response_str = response_data.decode('utf-8', errors='ignore')
                    if any(indicator in response_str.lower() for indicator in 
                          ['# ', '$ ', 'login:', 'password:', 'complete', 'done', 'finished', 'error']):
                        break
                else:
                    time.sleep(0.1)  # Small delay to prevent busy waiting
            
            # Decode response
            response = response_data.decode('utf-8', errors='ignore')
            
            # Clean up the response
            response = response.strip()
            
            return response if response else "Command sent successfully"
            
        except serial.SerialException as e:
            return f"Serial Error: {str(e)}"
        except Exception as e:
            return f"Communication Error: {str(e)}"
    
    def __init__(self, parent, app_instance):
        super().__init__(parent)
        self.parent = parent
        self.app_instance = app_instance
        self.title("Multi-Unit NanoBMC Flash Manager")
        self.geometry("700x1000")
        
        # Only allow NanoBMC devices
        if hasattr(app_instance, 'bmc_type') and app_instance.bmc_type.get() != 2:
            messagebox.showerror("Error", "Multi-unit flashing is only available for NanoBMC devices!")
            self.destroy()
            return
        
        # Initialize variables
        self.units = []  # List of unit configurations
        self.operation_running = False
        self.flash_threads = {}
        self.unit_progress = {}
        self.shared_servers = {}  # Track shared HTTP servers
        
        # File paths
        self.firmware_folder = ctk.StringVar()
        self.fip_file = ctk.StringVar()
        self.eeprom_file = ctk.StringVar()
        
        # Configuration file path
        self.config_file = os.path.join(os.path.expanduser("~"), ".nanobmc_multiflash_config.json")
        
        # Load previous selections if available
        self.load_configuration()
        
        # Create UI
        self.create_ui()
        
        # Auto-detect available devices
        self.refresh_devices()
        
        # Also clean up console processes when window closes
        self.protocol("WM_DELETE_WINDOW", self.on_window_close)

    def load_configuration(self):
            """Load configuration from file"""
            try:
                if os.path.exists(self.config_file):
                    with open(self.config_file, 'r') as f:
                        config = json.load(f)
                    
                    # Load file paths
                    self.firmware_folder.set(config.get('firmware_folder', ''))
                    self.fip_file.set(config.get('fip_file', ''))
                    self.eeprom_file.set(config.get('eeprom_file', ''))
                    
                    # Store unit configurations for later loading
                    self.saved_units = config.get('units', [])
                    
                    self.log_message("✓ Configuration loaded successfully")
                else:
                    self.saved_units = []
                    self.log_message("No previous configuration found - starting fresh")
                    
            except Exception as e:
                self.log_message(f"Warning: Could not load configuration: {e}")
                self.saved_units = []

    def save_configuration(self):
        """Save current configuration to file"""
        try:
            config = {
                'firmware_folder': self.firmware_folder.get(),
                'fip_file': self.fip_file.get(),
                'eeprom_file': self.eeprom_file.get(),
                'units': []
            }
            
            # Save unit configurations
            for unit in self.units:
                unit_config = {
                    'device': unit['device_var'].get(),
                    'username': unit['username_var'].get(),
                    'password': unit['password_var'].get(),
                    'bmc_ip': unit['bmc_ip_var'].get(),
                    'host_ip': unit['host_ip_var'].get()
                }
                config['units'].append(unit_config)
            
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            
            self.log_message("✓ Configuration saved successfully")
            
        except Exception as e:
            self.log_message(f"Warning: Could not save configuration: {e}")

    def auto_save_config(self):
        """Auto-save configuration when values change"""
        # Save configuration after a short delay to avoid excessive saves
        if hasattr(self, '_save_timer'):
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(1000, self.save_configuration)  # Save after 1 second delay


    def cleanup_port_80(self):
        """Aggressively clean up anything using port 80"""
        self.log_message("Cleaning up port 80...")
        
        try:
            # Method 1: Kill processes using port 80
            for proc in psutil.process_iter(['pid', 'name', 'connections']):
                try:
                    for conn in proc.info.get('connections', []):
                        if (hasattr(conn, 'laddr') and 
                            hasattr(conn.laddr, 'port') and 
                            conn.laddr.port == 80):
                            self.log_message(f"Killing process {proc.info['pid']} ({proc.info['name']}) using port 80")
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        except Exception as e:
            self.log_message(f"Error cleaning port 80: {e}")
        
        # Method 2: Use system commands as backup
        try:
            import subprocess
            subprocess.run("pkill -f ':80'", shell=True, timeout=5)
            subprocess.run("fuser -k 80/tcp", shell=True, timeout=5)
        except Exception:
            pass
        
        # Wait for cleanup
        import time
        time.sleep(2)

    def on_window_close(self):
        """Handle window closing with complete cleanup and save"""
        self.log_message("Multi-unit window closing - saving configuration and performing cleanup...")
        
        # Save configuration before closing
        self.save_configuration()
        
        # Stop all operations first
        if self.operation_running:
            self.operation_running = False
        
        # Clean up all servers
        for server_key, server_info in list(self.shared_servers.items()):
            try:
                server_info['server'].shutdown()
                server_info['server'].server_close()
            except Exception as e:
                self.log_message(f"Error shutting down server during close: {e}")
        
        self.shared_servers = {}
        
        # Clean up port 80
        self.cleanup_port_80()
        
        # Clean up console processes
        self.cleanup_console_processes()
        
        # Clean up any serial connections
        cleanup_all_serial_connections()
        
        # Destroy the window
        self.destroy()

    # ... (keep all other methods unchanged)

    def load_previous_selections(self):
        """Load previously selected files from app config"""
        if hasattr(self.app_instance, 'last_flash_all_folder'):
            self.firmware_folder.set(self.app_instance.last_flash_all_folder)
        if hasattr(self.app_instance, 'last_flash_all_fip'):
            self.fip_file.set(self.app_instance.last_flash_all_fip)
        if hasattr(self.app_instance, 'last_flash_all_eeprom'):
            self.eeprom_file.set(self.app_instance.last_flash_all_eeprom)

    def load_saved_units(self):
        """Load saved unit configurations into UI"""
        if hasattr(self, 'saved_units') and self.saved_units:
            for unit_data in self.saved_units:
                self.add_unit(unit_data)
            self.log_message(f"✓ Restored {len(self.saved_units)} unit configurations")

    def position_window(self):
        """Position the window relative to parent"""
        self.update_idletasks()
        parent_x = self.parent.winfo_rootx()
        parent_y = self.parent.winfo_rooty()
        x = parent_x + 25
        y = parent_y + 25
        self.geometry(f'+{x}+{y}')

    def create_ui(self):
        """Create the user interface"""
        # Main container with scrollable frame
        main_frame = ctk.CTkScrollableFrame(self)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Title
        title_label = ctk.CTkLabel(main_frame, text="Multi-Unit NanoBMC Flash Manager", 
                                  font=ctk.CTkFont(size=20, weight="bold"))
        title_label.pack(pady=10)
        
        # File selection section
        self.create_file_selection_section(main_frame)
        
        # Unit management section
        self.create_unit_management_section(main_frame)
        
        # Control buttons
        self.create_control_section(main_frame)
        
        # Progress and log section
        self.create_progress_section(main_frame)

    def create_file_selection_section(self, parent):
        """Create file selection UI section"""
        file_frame = ctk.CTkFrame(parent)
        file_frame.pack(fill="x", pady=10)
        
        ctk.CTkLabel(file_frame, text="Flash Files Configuration", 
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        
        # Firmware Folder
        folder_frame = ctk.CTkFrame(file_frame)
        folder_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(folder_frame, text="Firmware Folder (eMMC):").pack(anchor="w")
        folder_entry_frame = ctk.CTkFrame(folder_frame)
        folder_entry_frame.pack(fill="x", pady=2)
        ctk.CTkEntry(folder_entry_frame, textvariable=self.firmware_folder).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(folder_entry_frame, text="Browse", command=self.select_firmware_folder, width=80).pack(side="right", padx=5)
        
        # FIP File
        fip_frame = ctk.CTkFrame(file_frame)
        fip_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(fip_frame, text="FIP File (U-Boot):").pack(anchor="w")
        fip_entry_frame = ctk.CTkFrame(fip_frame)
        fip_entry_frame.pack(fill="x", pady=2)
        ctk.CTkEntry(fip_entry_frame, textvariable=self.fip_file).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(fip_entry_frame, text="Browse", command=self.select_fip_file, width=80).pack(side="right", padx=5)
        
        # EEPROM File
        eeprom_frame = ctk.CTkFrame(file_frame)
        eeprom_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(eeprom_frame, text="EEPROM File (FRU):").pack(anchor="w")
        eeprom_entry_frame = ctk.CTkFrame(eeprom_frame)
        eeprom_entry_frame.pack(fill="x", pady=2)
        ctk.CTkEntry(eeprom_entry_frame, textvariable=self.eeprom_file).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(eeprom_entry_frame, text="Browse", command=self.select_eeprom_file, width=80).pack(side="right", padx=5)

    def create_unit_management_section(self, parent):
        """Create unit management UI section"""
        unit_frame = ctk.CTkFrame(parent)
        unit_frame.pack(fill="x", pady=10)
        
        # Header
        header_frame = ctk.CTkFrame(unit_frame)
        header_frame.pack(fill="x", pady=10)
        ctk.CTkLabel(header_frame, text="Unit Configuration", 
                    font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        ctk.CTkButton(header_frame, text="Refresh Devices", command=self.refresh_devices, width=120).pack(side="right", padx=5)
        ctk.CTkButton(header_frame, text="Add Unit", command=self.add_unit, width=80).pack(side="right")
        
        # Units container
        self.units_container = ctk.CTkScrollableFrame(unit_frame, height=200)
        self.units_container.pack(fill="both", expand=True, padx=10, pady=5)

    def create_control_section(self, parent):
        """Create control buttons section"""
        control_frame = ctk.CTkFrame(parent)
        control_frame.pack(fill="x", pady=10)
        
        button_frame = ctk.CTkFrame(control_frame)
        button_frame.pack(pady=10)
        
        self.start_button = ctk.CTkButton(button_frame, text="Start Multi-Flash", 
                                         command=self.start_multi_flash, 
                                         font=ctk.CTkFont(size=14, weight="bold"),
                                         height=40, width=150)
        self.start_button.pack(side="left", padx=5)
        
        self.stop_button = ctk.CTkButton(button_frame, text="Stop All", 
                                        command=self.stop_all_operations,
                                        font=ctk.CTkFont(size=14, weight="bold"),
                                        height=40, width=100)
        self.stop_button.pack(side="left", padx=5)
        
        self.console_button = ctk.CTkButton(button_frame, text="Open Multi-Console", 
                                           command=self.open_multi_console,
                                           font=ctk.CTkFont(size=14, weight="bold"),
                                           height=40, width=150,
                                           fg_color="#16A085", hover_color="#138D75")
        self.console_button.pack(side="left", padx=5)

    def create_progress_section(self, parent):
        """Create progress and log section"""
        progress_frame = ctk.CTkFrame(parent)
        progress_frame.pack(fill="both", expand=True, pady=10)
        
        ctk.CTkLabel(progress_frame, text="Progress & Logs", 
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=5)
        
        # Overall progress
        self.overall_progress_label = ctk.CTkLabel(progress_frame, text="Overall Progress: 0%")
        self.overall_progress_label.pack(pady=2)
        self.overall_progress = ctk.CTkProgressBar(progress_frame)
        self.overall_progress.pack(fill="x", padx=10, pady=5)
        self.overall_progress.set(0)
        
        # Log area
        self.log_text = ctk.CTkTextbox(progress_frame, height=150)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=5)

    def select_firmware_folder(self):
        """Select firmware folder and auto-save"""
        from main import FileSelectionHelper
        folder = FileSelectionHelper.select_directory(
            self, "Select Firmware Folder", 
            self.firmware_folder.get() or os.path.expanduser("~")
        )
        if folder:
            self.firmware_folder.set(folder)
            self.auto_save_config()

    def select_fip_file(self):
        """Select FIP file with validation and auto-save"""
        from main import FileSelectionHelper
        file_path = FileSelectionHelper.select_file(
            self, "Select FIP File", 
            os.path.dirname(self.fip_file.get()) or os.path.expanduser("~"),
            "FIP Binary files (fip-snuc-nanobmc.bin) | fip-snuc-nanobmc.bin"
        )
        if file_path:
            filename = os.path.basename(file_path)
            if filename != "fip-snuc-nanobmc.bin":
                messagebox.showerror("Invalid FIP File", 
                                   f"Only 'fip-snuc-nanobmc.bin' is allowed for NanoBMC devices.\n"
                                   f"Selected file: '{filename}'")
                return
            self.fip_file.set(file_path)
            self.log_message(f"✓ Valid FIP file selected: {filename}")
            self.auto_save_config()

    def select_eeprom_file(self):
        """Select EEPROM file with validation and auto-save"""
        from main import FileSelectionHelper
        file_path = FileSelectionHelper.select_file(
            self, "Select EEPROM File", 
            os.path.dirname(self.eeprom_file.get()) or os.path.expanduser("~"),
            "FRU Binary files (fru.bin) | fru.bin"
        )
        if file_path:
            filename = os.path.basename(file_path)
            if filename != "fru.bin":
                messagebox.showerror("Invalid EEPROM File", 
                                   f"Only 'fru.bin' files are allowed.\n"
                                   f"Selected file: '{filename}'")
                return
            self.eeprom_file.set(file_path)
            self.log_message(f"✓ Valid EEPROM file selected: {filename}")
            self.auto_save_config()

    def refresh_devices(self):
        """Refresh available serial devices"""
        devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        self.available_devices = devices
        self.log_message(f"Found {len(devices)} serial devices: {', '.join(devices)}")
        
        # Update existing unit dropdowns
        for unit in self.units:
            if hasattr(unit, 'device_dropdown'):
                unit['device_dropdown'].configure(values=devices)

    def add_unit(self, unit_data=None):
        """Add a new unit configuration with optional saved data and auto-save"""
        unit_id = len(self.units) + 1
        
        unit_frame = ctk.CTkFrame(self.units_container)
        unit_frame.pack(fill="x", pady=5)
        
        # Unit info
        info_frame = ctk.CTkFrame(unit_frame)
        info_frame.pack(fill="x", padx=5, pady=5)
        
        ctk.CTkLabel(info_frame, text=f"Unit {unit_id}", 
                    font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, padx=5, pady=2, sticky="w")
        
        # Device selection
        ctk.CTkLabel(info_frame, text="Device:").grid(row=1, column=0, padx=5, pady=2, sticky="w")
        device_var = ctk.StringVar()
        device_dropdown = ctk.CTkComboBox(info_frame, variable=device_var, values=self.available_devices, width=120,
                                         command=lambda x: self.auto_save_config())
        device_dropdown.grid(row=1, column=1, padx=5, pady=2, sticky="w")
        
        # Credentials
        ctk.CTkLabel(info_frame, text="Username:").grid(row=2, column=0, padx=5, pady=2, sticky="w")
        username_var = ctk.StringVar(value="root")
        username_entry = ctk.CTkEntry(info_frame, textvariable=username_var, width=120)
        username_entry.grid(row=2, column=1, padx=5, pady=2, sticky="w")
        username_entry.bind('<KeyRelease>', lambda e: self.auto_save_config())
        
        ctk.CTkLabel(info_frame, text="Password:").grid(row=3, column=0, padx=5, pady=2, sticky="w")
        password_var = ctk.StringVar(value="0penBmc")
        password_entry = ctk.CTkEntry(info_frame, textvariable=password_var, show="*", width=120)
        password_entry.grid(row=3, column=1, padx=5, pady=2, sticky="w")
        password_entry.bind('<KeyRelease>', lambda e: self.auto_save_config())
        
        # IP Configuration
        ctk.CTkLabel(info_frame, text="BMC IP:").grid(row=1, column=2, padx=5, pady=2, sticky="w")
        bmc_ip_var = ctk.StringVar(value=f"192.168.1.{100 + unit_id}")
        bmc_ip_entry = ctk.CTkEntry(info_frame, textvariable=bmc_ip_var, width=120)
        bmc_ip_entry.grid(row=1, column=3, padx=5, pady=2, sticky="w")
        bmc_ip_entry.bind('<KeyRelease>', lambda e: self.auto_save_config())
        
        ctk.CTkLabel(info_frame, text="Host IP:").grid(row=2, column=2, padx=5, pady=2, sticky="w")
        host_ip_var = ctk.StringVar()
        if hasattr(self.app_instance, 'your_ip') and self.app_instance.your_ip.get():
            host_ip_var.set(self.app_instance.your_ip.get())
        host_ip_entry = ctk.CTkEntry(info_frame, textvariable=host_ip_var, width=120)
        host_ip_entry.grid(row=2, column=3, padx=5, pady=2, sticky="w")
        host_ip_entry.bind('<KeyRelease>', lambda e: self.auto_save_config())
        
        # Load saved data if provided
        if unit_data:
            device_var.set(unit_data.get('device', ''))
            username_var.set(unit_data.get('username', 'root'))
            password_var.set(unit_data.get('password', '0penBmc'))
            bmc_ip_var.set(unit_data.get('bmc_ip', f"192.168.1.{100 + unit_id}"))
            host_ip_var.set(unit_data.get('host_ip', ''))
        
        # Progress bar for this unit
        progress_label = ctk.CTkLabel(info_frame, text="Progress: 0%")
        progress_label.grid(row=4, column=0, columnspan=2, padx=5, pady=2, sticky="w")
        progress_bar = ctk.CTkProgressBar(info_frame)
        progress_bar.grid(row=4, column=2, columnspan=2, padx=5, pady=2, sticky="ew")
        progress_bar.set(0)
        
        # Status label
        status_label = ctk.CTkLabel(info_frame, text="Status: Ready")
        status_label.grid(row=5, column=0, columnspan=4, padx=5, pady=2, sticky="w")
        
        # Remove button
        remove_button = ctk.CTkButton(info_frame, text="Remove", 
                                     command=lambda: self.remove_unit(unit_id-1),
                                     width=80, height=28)
        remove_button.grid(row=0, column=3, padx=5, pady=2, sticky="e")
        
        # Configure grid weights
        info_frame.grid_columnconfigure(1, weight=1)
        info_frame.grid_columnconfigure(3, weight=1)
        
        # Store unit configuration
        unit_config = {
            'id': unit_id,
            'frame': unit_frame,
            'device_var': device_var,
            'device_dropdown': device_dropdown,
            'username_var': username_var,
            'password_var': password_var,
            'bmc_ip_var': bmc_ip_var,
            'host_ip_var': host_ip_var,
            'progress_label': progress_label,
            'progress_bar': progress_bar,
            'status_label': status_label
        }
        
        self.units.append(unit_config)
        self.unit_progress[unit_id] = 0
        
        self.log_message(f"Added Unit {unit_id}")
        
        # Auto-save after adding unit (unless we're loading saved units)
        if not unit_data:
            self.auto_save_config()

    def remove_unit(self, index):
        """Remove a unit configuration and auto-save"""
        if 0 <= index < len(self.units):
            unit = self.units[index]
            unit['frame'].destroy()
            self.units.pop(index)
            
            # Update unit IDs
            for i, unit in enumerate(self.units):
                unit['id'] = i + 1
                
            self.log_message(f"Removed unit configuration")
            self.auto_save_config()

    def validate_configuration(self):
        """Validate all configuration before starting"""
        # Check files
        if not self.firmware_folder.get() or not os.path.exists(self.firmware_folder.get()):
            messagebox.showerror("Error", "Please select a valid firmware folder")
            return False
            
        if not self.fip_file.get() or not os.path.exists(self.fip_file.get()):
            messagebox.showerror("Error", "Please select a valid FIP file")
            return False
            
        if not self.eeprom_file.get() or not os.path.exists(self.eeprom_file.get()):
            messagebox.showerror("Error", "Please select a valid EEPROM file")
            return False
        
        # Check units
        if not self.units:
            messagebox.showerror("Error", "Please add at least one unit")
            return False
            
        for unit in self.units:
            if not unit['device_var'].get():
                messagebox.showerror("Error", f"Please select a device for Unit {unit['id']}")
                return False
            if not unit['username_var'].get():
                messagebox.showerror("Error", f"Please enter username for Unit {unit['id']}")
                return False
            if not unit['password_var'].get():
                messagebox.showerror("Error", f"Please enter password for Unit {unit['id']}")
                return False
            if not unit['bmc_ip_var'].get():
                messagebox.showerror("Error", f"Please enter BMC IP for Unit {unit['id']}")
                return False
            if not unit['host_ip_var'].get():
                messagebox.showerror("Error", f"Please enter Host IP for Unit {unit['id']}")
                return False
        
        # Check for duplicate devices
        devices = [unit['device_var'].get() for unit in self.units]
        if len(devices) != len(set(devices)):
            messagebox.showerror("Error", "Each unit must use a different serial device")
            return False
            
        # Check for duplicate IPs
        ips = [unit['bmc_ip_var'].get() for unit in self.units]
        if len(ips) != len(set(ips)):
            messagebox.showerror("Error", "Each unit must have a different BMC IP address")
            return False
        
        return True

    def start_multi_flash(self):
        """Start the multi-unit flashing process with improved server management"""
        if self.operation_running:
            messagebox.showwarning("Warning", "Multi-flash operation is already running")
            return
            
        if not self.validate_configuration():
            return
            
        # Show confirmation dialog
        unit_count = len(self.units)
        if not messagebox.askyesno("Confirm Multi-Flash", 
                                  f"This will flash {unit_count} NanoBMC devices simultaneously.\n\n"
                                  f"Make sure all devices are at the U-Boot bootloader prompt.\n\n"
                                  f"Continue?"):
            return
        
        # Clean up any existing servers before starting
        self.log_message("Cleaning up any existing servers...")
        self.cleanup_port_80()
        self.shared_servers = {}  # Reset server tracking
        
        self.operation_running = True
        self.start_button.configure(state="disabled")
        self.flash_threads = {}
        
        # Reset progress
        self.overall_progress.set(0)
        for unit in self.units:
            unit['progress_bar'].set(0)
            unit['status_label'].configure(text="Status: Starting...")
            unit['progress_label'].configure(text="Progress: 0%")
        
        self.log_message("=" * 60)
        self.log_message(f"STARTING MULTI-UNIT FLASH OPERATION ({unit_count} devices)")
        self.log_message("=" * 60)
        
        # Group units by host IP to optimize server usage
        self.ip_groups = self.group_units_by_host_ip()
        self.shared_servers = {}  # Initialize shared server tracking
        
        self.log_message(f"Optimized server usage: {len(self.ip_groups)} server(s) for {unit_count} units")
        
        # Pre-create servers for each unique host IP
        self.log_message("Pre-creating HTTP servers...")
        for host_ip, units in self.ip_groups.items():
            try:
                server = self.get_shared_server(host_ip, self.firmware_folder.get())
                self.log_message(f"✓ Pre-created server for {host_ip} (will serve {len(units)} units)")
            except Exception as e:
                self.log_message(f"❌ Failed to create server for {host_ip}: {e}")
                messagebox.showerror("Server Error", f"Failed to create HTTP server for {host_ip}:\n{e}")
                self.operation_running = False
                self.start_button.configure(state="normal")
                return
        
        # Add delay between server creation and unit start
        self.log_message("Waiting 3 seconds for servers to stabilize...")
        import time
        time.sleep(3)
        
        # Start flash thread for each unit
        for unit in self.units:
            thread = threading.Thread(
                target=self.flash_unit,
                args=(unit,),
                daemon=True
            )
            self.flash_threads[unit['id']] = thread
            thread.start()
            
            # Small delay between starting threads to prevent race conditions
            time.sleep(0.5)
        
        # Start progress monitoring thread
        threading.Thread(target=self.monitor_progress, daemon=True).start()

    def group_units_by_host_ip(self):
        """Group units by their host IP addresses for server optimization"""
        ip_groups = {}
        for unit in self.units:
            host_ip = unit['host_ip_var'].get()
            if host_ip not in ip_groups:
                ip_groups[host_ip] = []
            ip_groups[host_ip].append(unit)
        
        # Log the grouping for transparency
        for host_ip, units in ip_groups.items():
            unit_ids = [str(unit['id']) for unit in units]
            self.log_message(f"Host IP {host_ip}: Units {', '.join(unit_ids)} (shared server)")
        
        return ip_groups

    def get_shared_server(self, host_ip, firmware_folder):
        """Get or create a shared HTTP server for the given host IP with better error handling"""
        server_key = f"{host_ip}:80"
        
        if server_key in self.shared_servers:
            # Server already exists for this IP
            server_info = self.shared_servers[server_key]
            server_info['usage_count'] += 1
            self.log_message(f"Reusing existing server for {host_ip} (usage count: {server_info['usage_count']})")
            return server_info['server']
        else:
            # Create new server for this IP
            port = 80
            max_attempts = 3
            
            for attempt in range(max_attempts):
                try:
                    self.log_message(f"Creating HTTP server attempt {attempt + 1}/{max_attempts} for {host_ip}:{port}")
                    
                    # Clean up port 80 before attempting to create server
                    if attempt > 0:
                        self.cleanup_port_80()
                    
                    # Change to the firmware directory
                    original_dir = os.getcwd()
                    os.chdir(firmware_folder)
                    
                    # Create the server with our robust threaded implementation
                    httpd = ThreadedHTTPServer(('0.0.0.0', port), RobustHTTPRequestHandler)
                    
                    # Start server in background thread with better configuration
                    server_thread = threading.Thread(
                        target=httpd.serve_forever, 
                        daemon=True,
                        name=f"HTTPServer-{host_ip}"
                    )
                    server_thread.start()
                    
                    self.log_message(f"✓ Started threaded HTTP server for {host_ip}:{port} (max 50 concurrent connections)")
                    
                    # Restore original directory
                    os.chdir(original_dir)
                    
                    # Test if server is actually working with retry logic
                    import urllib.request
                    import time
                    time.sleep(2)  # Give server more time to start
                    
                    max_verify_attempts = 3
                    for verify_attempt in range(max_verify_attempts):
                        try:
                            # Try to connect to verify server is working
                            test_url = f"http://{host_ip}:{port}/"
                            req = urllib.request.Request(test_url)
                            req.add_header('User-Agent', 'NanoBMC-MultiFlash/1.0')
                            response = urllib.request.urlopen(req, timeout=10)
                            response.close()
                            self.log_message(f"✓ HTTP server verified working at {host_ip}:{port}")
                            break
                        except Exception as e:
                            if verify_attempt < max_verify_attempts - 1:
                                self.log_message(f"Server verification attempt {verify_attempt + 1} failed, retrying...")
                                time.sleep(2)
                            else:
                                self.log_message(f"Server verification failed after {max_verify_attempts} attempts: {e}")
                                httpd.shutdown()
                                raise Exception(f"Server verification failed: {e}")
                    
                    # Store server info with additional monitoring data
                    self.shared_servers[server_key] = {
                        'server': httpd,
                        'thread': server_thread,
                        'usage_count': 1,
                        'firmware_folder': firmware_folder,
                        'host_ip': host_ip,
                        'port': port,
                        'created_time': time.time(),
                        'total_requests': 0
                    }
                    
                    self.log_message(f"✓ Created new HTTP server for {host_ip}:{port}")
                    return httpd
                    
                except OSError as e:
                    if e.errno == 98:  # Address already in use
                        self.log_message(f"Port {port} in use, cleaning up...")
                        self.cleanup_port_80()
                        if attempt < max_attempts - 1:
                            import time
                            time.sleep(3)  # Wait before retry
                            continue
                        else:
                            raise Exception(f"Could not bind to port {port} after {max_attempts} attempts")
                    else:
                        raise
                except Exception as e:
                    self.log_message(f"Server creation attempt {attempt + 1} failed: {e}")
                    if attempt == max_attempts - 1:
                        raise
                    import time
                    time.sleep(2)
            
            raise Exception(f"Failed to create server after {max_attempts} attempts")

    def check_server_health(self, host_ip):
        """Check if the shared server is still healthy and responding"""
        server_key = f"{host_ip}:80"
        
        if server_key not in self.shared_servers:
            return False
            
        try:
            import urllib.request
            test_url = f"http://{host_ip}:80/"
            req = urllib.request.Request(test_url)
            req.add_header('User-Agent', 'NanoBMC-MultiFlash-HealthCheck/1.0')
            response = urllib.request.urlopen(req, timeout=5)
            response.close()
            return True
        except Exception as e:
            self.log_message(f"Server health check failed for {host_ip}: {e}")
            return False

    def get_server_stats(self, host_ip):
        """Get statistics about the shared server"""
        server_key = f"{host_ip}:80"
        
        if server_key in self.shared_servers:
            server_info = self.shared_servers[server_key]
            uptime = time.time() - server_info['created_time']
            return {
                'uptime': uptime,
                'usage_count': server_info['usage_count'],
                'total_requests': server_info.get('total_requests', 0),
                'is_alive': server_info['thread'].is_alive()
            }
        return None
        """Release a shared HTTP server when a unit is done with it"""
        server_key = f"{host_ip}:80"
        
        if server_key in self.shared_servers:
            server_info = self.shared_servers[server_key]
            server_info['usage_count'] -= 1
            
            self.log_message(f"Released server usage for {host_ip} (remaining usage: {server_info['usage_count']})")
            
            # If no more units are using this server, shut it down
            if server_info['usage_count'] <= 0:
                try:
                    server_info['server'].shutdown()
                    server_info['server'].server_close()
                    self.log_message(f"✓ Shut down HTTP server for {host_ip}")
                except Exception as e:
                    self.log_message(f"Error shutting down server: {e}")
                
                del self.shared_servers[server_key]

    def flash_unit(self, unit):
        """Flash a single unit (runs in separate thread) with improved error handling"""
        unit_id = unit['id']
        device = unit['device_var'].get()
        username = unit['username_var'].get()
        password = unit['password_var'].get()
        bmc_ip = unit['bmc_ip_var'].get()
        host_ip = unit['host_ip_var'].get()
        
        def update_unit_progress(progress):
            self.unit_progress[unit_id] = progress
            percentage = int(progress * 100)
            unit['progress_bar'].set(progress)
            unit['progress_label'].configure(text=f"Progress: {percentage}%")
        
        def update_unit_status(status):
            unit['status_label'].configure(text=f"Status: {status}")
        
        def unit_log(message):
            self.log_message(f"[Unit {unit_id}] {message}")
        
        # Get shared server for this unit's host IP
        shared_server = None
        
        try:
            # Check if operation was stopped before starting
            if not self.operation_running:
                update_unit_status("Stopped")
                return
                
            update_unit_status("Initializing...")
            unit_log("Flash sequence starting")
            
            # Get shared server with retry logic
            max_server_attempts = 3
            for server_attempt in range(max_server_attempts):
                try:
                    shared_server = self.get_shared_server(host_ip, self.firmware_folder.get())
                    unit_log(f"✓ Obtained shared server for {host_ip}")
                    break
                except Exception as e:
                    unit_log(f"Server attempt {server_attempt + 1}/{max_server_attempts} failed: {e}")
                    if server_attempt == max_server_attempts - 1:
                        raise Exception(f"Could not obtain shared server after {max_server_attempts} attempts: {e}")
                    import time
                    time.sleep(5)  # Wait before retry
            
            if not shared_server:
                raise Exception("Failed to obtain shared server")
            
            # Execute the flash sequence
            total_steps = 5
            current_step = 0
            
            # Step 1: Flash eMMC (with shared server optimization)
            if not self.operation_running:
                return
                
            current_step = 1
            update_unit_status(f"Step {current_step}/5: Flash eMMC")
            unit_log("Starting eMMC flash...")
            
            def emmc_progress_callback(progress):
                if self.operation_running:  # Only update if still running
                    step_progress = ((current_step - 1) / total_steps) + (progress / total_steps)
                    update_unit_progress(step_progress)
            
            result = asyncio.run(self.flash_emmc_shared_optimized(
                bmc_ip,
                self.firmware_folder.get(),
                host_ip,
                2,  # NanoBMC
                emmc_progress_callback,
                unit_log,
                device,
                shared_server
            ))
            
            if not result:
                raise Exception("eMMC flash failed")
            
            # Check if operation was stopped
            if not self.operation_running:
                return
            
            # Step 2: Login to BMC
            current_step = 2
            update_unit_status(f"Step {current_step}/5: Login")
            unit_log("Logging into BMC...")
            
            login_result = asyncio.run(login(username, password, device, unit_log))
            
            if self.operation_running:
                step_progress = current_step / total_steps
                update_unit_progress(step_progress)
            
            # Step 3: Set BMC IP
            if not self.operation_running:
                return
                
            current_step = 3
            update_unit_status(f"Step {current_step}/5: Set IP")
            unit_log(f"Setting BMC IP to {bmc_ip}...")
            
            def ip_progress_callback(progress):
                if self.operation_running:
                    step_progress = ((current_step - 1) / total_steps) + (progress / total_steps)
                    update_unit_progress(step_progress)
            
            asyncio.run(set_ip(bmc_ip, ip_progress_callback, unit_log, device))
            
            # Step 4: Flash U-Boot (FIP)
            if not self.operation_running:
                return
                
            current_step = 4
            update_unit_status(f"Step {current_step}/5: Flash U-Boot")
            unit_log("Flashing U-Boot (FIP)...")
            
            def fip_progress_callback(progress):
                if self.operation_running:
                    step_progress = ((current_step - 1) / total_steps) + (progress / total_steps)
                    update_unit_progress(step_progress)
            
            asyncio.run(self.flasher_shared(
                self.fip_file.get(),
                host_ip,
                fip_progress_callback,
                unit_log,
                device,
                shared_server
            ))
            
            # Step 5: Flash EEPROM
            if not self.operation_running:
                return
                
            current_step = 5
            update_unit_status(f"Step {current_step}/5: Flash EEPROM")
            unit_log("Flashing EEPROM...")
            
            def eeprom_progress_callback(progress):
                if self.operation_running:
                    step_progress = ((current_step - 1) / total_steps) + (progress / total_steps)
                    update_unit_progress(step_progress)
            
            asyncio.run(self.flash_eeprom_shared(
                self.eeprom_file.get(),
                host_ip,
                eeprom_progress_callback,
                unit_log,
                device,
                shared_server
            ))
            
            # Complete
            if self.operation_running:
                update_unit_progress(1.0)
                update_unit_status("Completed Successfully!")
                unit_log("✅ Flash sequence completed successfully!")
            else:
                update_unit_status("Stopped")
                unit_log("⚠️ Flash sequence was stopped")
            
        except Exception as e:
            error_msg = str(e)
            update_unit_status(f"Failed: {error_msg}")
            unit_log(f"❌ Error during flash sequence: {error_msg}")
            update_unit_progress(0)
        finally:
            # Always release the shared server when done
            if shared_server:
                try:
                    self.release_shared_server(host_ip)
                except Exception as e:
                    unit_log(f"Warning: Error releasing server: {e}")

    def monitor_progress(self):
        """Monitor overall progress and completion"""
        while self.operation_running:
            if not self.flash_threads:
                break
                
            # Check if all threads are complete
            active_threads = [t for t in self.flash_threads.values() if t.is_alive()]
            
            if not active_threads:
                # All threads completed
                self.operation_running = False
                self.start_button.configure(state="normal")
                
                # Calculate overall completion
                total_progress = sum(self.unit_progress.values())
                unit_count = len(self.units)
                overall_progress = total_progress / unit_count if unit_count > 0 else 0
                
                self.overall_progress.set(overall_progress)
                percentage = int(overall_progress * 100)
                self.overall_progress_label.configure(text=f"Overall Progress: {percentage}%")
                
                if overall_progress >= 1.0:
                    self.log_message("🎉 ALL UNITS COMPLETED SUCCESSFULLY!")
                else:
                    self.log_message("⚠️ Multi-flash operation completed with some errors")
                
                self.log_message("=" * 60)
                break
            else:
                # Update overall progress
                total_progress = sum(self.unit_progress.values())
                unit_count = len(self.units)
                overall_progress = total_progress / unit_count if unit_count > 0 else 0
                
                self.overall_progress.set(overall_progress)
                percentage = int(overall_progress * 100)
                self.overall_progress_label.configure(text=f"Overall Progress: {percentage}%")
            
            time.sleep(1)

    def open_multi_console(self):
        """Open a single Terminator instance with split terminals for each unit"""
        if not self.units:
            messagebox.showwarning("No Units", "Please add at least one unit before opening console")
            return
        
        # Check if units have devices selected
        units_with_devices = [unit for unit in self.units if unit['device_var'].get()]
        if not units_with_devices:
            messagebox.showwarning("No Devices", "Please select serial devices for units before opening console")
            return
        
        try:
            self.log_message(f"Opening multi-console for {len(units_with_devices)} units...")
            
            # Clean up any existing console processes
            self.cleanup_console_processes()
            
            # Create Terminator configuration for multiple terminals
            config_content = self.generate_terminator_config(units_with_devices)
            
            # Write temporary config file
            import tempfile
            config_dir = tempfile.mkdtemp()
            config_file = os.path.join(config_dir, "terminator_config")
            
            with open(config_file, 'w') as f:
                f.write(config_content)
            
            self.log_message(f"Generated Terminator config for {len(units_with_devices)} terminals")
            
            # Launch Terminator with custom config
            try:
                process = subprocess.Popen(
                    ["terminator", "--config", config_file, "--layout", "multi_unit"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.log_message(f"Multi-console launched successfully (PID: {process.pid})")
                
                # Store process for cleanup
                if not hasattr(self, 'console_processes'):
                    self.console_processes = []
                self.console_processes.append(process)
                
            except FileNotFoundError:
                # Fallback to individual xterm windows if terminator is not available
                self.log_message("Terminator not found, falling back to individual xterm windows...")
                self.open_individual_consoles(units_with_devices)
                
        except Exception as e:
            self.log_message(f"Error opening multi-console: {e}")
            messagebox.showerror("Console Error", f"Failed to open multi-console:\n{e}")


    def generate_terminator_config(self, units):
        """Generate Terminator configuration with optimized splits for each ttyUSB device"""
        config = """[global_config]
    enabled_plugins = LaunchpadCodeURLHandler, APTURLHandler, LaunchpadBugURLHandler
    title_hide_sizetext = False
    title_transmit_fg_color = "#ffffff"
    title_transmit_bg_color = "#c80003"
    title_receive_fg_color = "#ffffff"
    title_receive_bg_color = "#0076c9"
    
    [keybindings]

    [profiles]
    """
        
        # Create a profile for each unit with device-specific colors and titles
        colors = [
            ("#002b36", "#839496"),  # Dark blue/cyan
            ("#073642", "#93a1a1"),  # Darker blue/light gray
            ("#2d1b3d", "#c792ea"),  # Purple/light purple
            ("#1e3d2b", "#82c366"),  # Dark green/light green
            ("#3d2b1e", "#f5a623"),  # Brown/orange
            ("#3d1e2b", "#ff6b9d"),  # Dark red/pink
        ]
        
        for i, unit in enumerate(units):
            device = unit['device_var'].get()
            unit_id = unit['id']
            bmc_ip = unit['bmc_ip_var'].get() or "No IP"
            
            # Use different colors for each unit
            bg_color, fg_color = colors[i % len(colors)]
            
            config += f"""  [[Unit_{unit_id}_{device.replace('/', '_')}]]
        background_color = "{bg_color}"
        cursor_color = "#aaaaaa"
        font = Monospace 11
        foreground_color = "{fg_color}"
        show_titlebar = True
        title_hide_sizetext = False
        use_system_font = False
        custom_command = minicom -D {device}
        use_custom_command = True
        scrollback_lines = 1000
        
    """
        
        config += "\n[layouts]\n  [[multi_unit]]\n"
        
        num_units = len(units)
        
        if num_units == 1:
            # Single terminal - full window
            unit = units[0]
            device = unit['device_var'].get()
            bmc_ip = unit['bmc_ip_var'].get() or "No IP"
            
            config += f"""    [[[child0]]]
        parent = ""
        profile = Unit_{unit['id']}_{device.replace('/', '_')}
        type = Terminal
        title = Unit {unit['id']} | {device} | {bmc_ip}
    """
            
        elif num_units == 2:
            # Two terminals - horizontal split
            config += """    [[[child0]]]
        order = 0
        parent = ""
        position = 0:0
        size = 1400, 700
        type = Window
        [[[child1]]]
        order = 0
        parent = child0
        position = 700
        type = HPaned
    """
            
            for i, unit in enumerate(units):
                device = unit['device_var'].get()
                bmc_ip = unit['bmc_ip_var'].get() or "No IP"
                
                config += f"""    [[[terminal{i+2}]]]
        order = {i}
        parent = child1
        profile = Unit_{unit['id']}_{device.replace('/', '_')}
        type = Terminal
        title = Unit {unit['id']} | {device} | {bmc_ip}
    """
                
        elif num_units == 3:
            # Three terminals - one on left, two stacked on right
            config += """    [[[child0]]]
        order = 0
        parent = ""
        position = 0:0
        size = 1400, 800
        type = Window
        [[[child1]]]
        order = 0
        parent = child0
        position = 700
        type = HPaned
        [[[child2]]]
        order = 1
        parent = child1
        position = 400
        type = VPaned
    """
            
            # First terminal (left side)
            unit = units[0]
            device = unit['device_var'].get()
            bmc_ip = unit['bmc_ip_var'].get() or "No IP"
            config += f"""    [[[terminal3]]]
        order = 0
        parent = child1
        profile = Unit_{unit['id']}_{device.replace('/', '_')}
        type = Terminal
        title = Unit {unit['id']} | {device} | {bmc_ip}
    """
            
            # Second and third terminals (right side, stacked)
            for i, unit in enumerate(units[1:], 1):
                device = unit['device_var'].get()
                bmc_ip = unit['bmc_ip_var'].get() or "No IP"
                
                config += f"""    [[[terminal{i+3}]]]
        order = {i-1}
        parent = child2
        profile = Unit_{unit['id']}_{device.replace('/', '_')}
        type = Terminal
        title = Unit {unit['id']} | {device} | {bmc_ip}
    """
                
        elif num_units == 4:
            # Four terminals - 2x2 grid
            config += """    [[[child0]]]
        order = 0
        parent = ""
        position = 0:0
        size = 1400, 800
        type = Window
        [[[child1]]]
        order = 0
        parent = child0
        position = 700
        type = HPaned
        [[[child2]]]
        order = 0
        parent = child1
        position = 400
        type = VPaned
        [[[child3]]]
        order = 1
        parent = child1
        position = 400
        type = VPaned
    """
            
            for i, unit in enumerate(units):
                device = unit['device_var'].get()
                bmc_ip = unit['bmc_ip_var'].get() or "No IP"
                parent = "child2" if i < 2 else "child3"
                order = i % 2
                
                config += f"""    [[[terminal{i+4}]]]
        order = {order}
        parent = {parent}
        profile = Unit_{unit['id']}_{device.replace('/', '_')}
        type = Terminal
        title = Unit {unit['id']} | {device} | {bmc_ip}
    """
                
        elif num_units <= 6:
            # 6 terminals - 3x2 grid
            config += """    [[[child0]]]
        order = 0
        parent = ""
        position = 0:0
        size = 1500, 900
        type = Window
        [[[child1]]]
        order = 0
        parent = child0
        position = 750
        type = HPaned
        [[[child2]]]
        order = 0
        parent = child1
        position = 300
        type = VPaned
        [[[child3]]]
        order = 1
        parent = child1
        position = 300
        type = VPaned
    """
            
            for i, unit in enumerate(units):
                device = unit['device_var'].get()
                bmc_ip = unit['bmc_ip_var'].get() or "No IP"
                
                if i < 3:
                    parent = "child2"
                    order = i
                else:
                    parent = "child3"
                    order = i - 3
                
                config += f"""    [[[terminal{i+4}]]]
        order = {order}
        parent = {parent}
        profile = Unit_{unit['id']}_{device.replace('/', '_')}
        type = Terminal
        title = Unit {unit['id']} | {device} | {bmc_ip}
    """
                
        else:
            # More than 6 terminals - dynamic vertical splits
            config += f"""    [[[child0]]]
        order = 0
        parent = ""
        position = 0:0
        size = 1600, 1000
        type = Window
    """
            
            # Create nested vertical splits
            for i in range(num_units - 1):
                if i == 0:
                    config += f"""    [[[child{i+1}]]]
        order = 0
        parent = child0
        position = {800 // num_units * (i + 1)}
        type = VPaned
    """
                else:
                    config += f"""    [[[child{i+1}]]]
        order = 1
        parent = child{i}
        position = {800 // (num_units - i)}
        type = VPaned
    """
            
            # Add terminals
            for i, unit in enumerate(units):
                device = unit['device_var'].get()
                bmc_ip = unit['bmc_ip_var'].get() or "No IP"
                
                if i == 0:
                    parent = "child1"
                    order = 0
                elif i == num_units - 1:
                    parent = f"child{num_units - 1}"
                    order = 1
                else:
                    parent = f"child{i + 1}"
                    order = 0
                
                config += f"""    [[[terminal{i+2}]]]
        order = {order}
        parent = {parent}
        profile = Unit_{unit['id']}_{device.replace('/', '_')}
        type = Terminal
        title = Unit {unit['id']} | {device} | {bmc_ip}
    """
        
        config += "\n[plugins]\n"
        
        return config

    def open_individual_consoles(self, units):
        """Fallback method to open individual console windows"""
        self.log_message("Opening individual console windows...")
        
        for unit in units:
            device = unit['device_var'].get()
            unit_id = unit['id']
            
            try:
                # Try xterm with a descriptive title
                process = subprocess.Popen(
                    ["xterm", "-T", f"Unit {unit_id} - {device}", "-e", f"minicom -D {device}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.log_message(f"Console opened for Unit {unit_id} on {device} (PID: {process.pid})")
                
                # Store process for cleanup
                if not hasattr(self, 'console_processes'):
                    self.console_processes = []
                self.console_processes.append(process)
                
            except Exception as e:
                self.log_message(f"Failed to open console for Unit {unit_id}: {e}")

    def cleanup_console_processes(self):
        """Clean up any existing console processes"""
        try:
            # Clean up tracked processes
            if hasattr(self, 'console_processes'):
                for proc in self.console_processes:
                    try:
                        if proc.poll() is None:  # Process is still running
                            proc.terminate()
                    except:
                        pass
                self.console_processes = []
            
            # Clean up any terminator or minicom processes
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] in ['terminator', 'xterm']:
                        cmdline = ' '.join(proc.info['cmdline'] or [])
                        # Check if it's one of our console processes
                        if any(unit['device_var'].get() in cmdline for unit in self.units if unit['device_var'].get()):
                            self.log_message(f"Terminating existing console process: {proc.info['pid']}")
                            proc.terminate()
                    elif proc.info['name'] == 'minicom':
                        # Clean up any orphaned minicom processes for our devices
                        cmdline = ' '.join(proc.info['cmdline'] or [])
                        if any(unit['device_var'].get() in cmdline for unit in self.units if unit['device_var'].get()):
                            self.log_message(f"Terminating minicom process: {proc.info['pid']}")
                            proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
        except Exception as e:
            self.log_message(f"Error cleaning console processes: {e}")

    def stop_all_operations(self):
        """Stop all running operations and clean up servers"""
        if not self.operation_running:
            return
            
        if messagebox.askyesno("Confirm Stop", "Are you sure you want to stop all operations?\n\nThis may leave devices in an incomplete state."):
            self.log_message("🛑 STOPPING ALL OPERATIONS...")
            
            # Set flag to stop operations
            self.operation_running = False
            
            # Update all unit statuses
            for unit in self.units:
                unit['status_label'].configure(text="Status: Stopped")
            
            # Clean up all servers
            self.log_message("Cleaning up HTTP servers...")
            for server_key, server_info in list(self.shared_servers.items()):
                try:
                    server_info['server'].shutdown()
                    server_info['server'].server_close()
                    self.log_message(f"Shut down server: {server_key}")
                except Exception as e:
                    self.log_message(f"Error shutting down server {server_key}: {e}")
            
            self.shared_servers = {}
            
            # Additional port cleanup
            self.cleanup_port_80()
            
            # Enable start button
            self.start_button.configure(state="normal")
            
            # Clean up any serial connections
            cleanup_all_serial_connections()
            
            # Clean up console processes
            self.cleanup_console_processes()
            
            self.log_message("All operations stopped and resources cleaned up.")

    async def flash_emmc_shared_optimized(self, bmc_ip, directory, my_ip, dd_value, callback_progress, callback_output, serial_device, shared_server):
        """
        Simplified eMMC flash function for multi-unit operations using shared HTTP server.
        Uses the same approach as the original flash_emmc function.
        """
        port = 80
        
        if dd_value == 1:
            type = 'mos-bmc'
        else:
            type = 'nanobmc'

        ser = None
        
        try:
            # Use the provided shared server - no server creation needed
            callback_output("Using shared HTTP server for eMMC flash...")
            callback_progress(0.10)

            # FIX: Use the passed serial_device parameter instead of hardcoded '/dev/ttyUSB0'
            ser = serial.Serial(serial_device, 115200, timeout=0.1)

            # Setting IP Address (bootloader)
            callback_output("Setting IP Address (bootloader)...")
            command = f'setenv ipaddr {bmc_ip}\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 2)
            callback_output(response)
            callback_progress(0.20)

            # Grabbing virtual restore image
            callback_output("Grabbing virtual restore image...")
            command = f'wget ${{loadaddr}} {my_ip}:/obmc-rescue-image-snuc-{type}.itb; bootm\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 2)
            callback_output(response)
            callback_progress(0.40)

            # Setting IP Address (BMC)
            callback_output("Setting IP Address (BMC)...")
            await asyncio.sleep(20)
            command = f'ifconfig eth0 up {bmc_ip}\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 2)
            callback_output(response)
            callback_progress(0.50)

            # Grabbing restore image
            callback_output("Grabbing restore image to your system...")
            command = f"curl -o obmc-phosphor-image-snuc-{type}.wic.xz {my_ip}/obmc-phosphor-image-snuc-{type}.wic.xz\n"
            response = await asyncio.to_thread(read_serial_data, ser, command, 2)
            callback_output(response)
            callback_progress(0.60)

            # Grabbing the mapping file
            callback_output("Grabbing the mapping file...")
            command = f'curl -o obmc-phosphor-image-snuc-{type}.wic.bmap {my_ip}/obmc-phosphor-image-snuc-{type}.wic.bmap\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 5)
            callback_output(response)
            callback_progress(0.90)

            # Flashing the restore image
            callback_output("Flashing the restore image to your system...")
            command = f'bmaptool copy obmc-phosphor-image-snuc-{type}.wic.xz /dev/mmcblk0\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 5)
            callback_output(response)

            await asyncio.sleep(55)
            callback_output("Factory Reset Complete. Please let the BMC reboot.")
            ser.write(b'reboot\n')
            ser.close()
            callback_progress(1.00)
            await asyncio.sleep(60)
            
            return True

        except Exception as e:
            callback_output(f"Error during eMMC flash: {e}")
            callback_output("Flash unsuccessful.")
            callback_progress(0)
            return False
        finally:
            if ser and ser.is_open:
                try:
                    ser.write(b'\n')  # Send newline to reset state
                    ser.close()
                except:
                    pass

    async def flasher_shared(self, flash_file, my_ip, callback_progress, callback_output, serial_device, shared_server):
        """Flash U-Boot using a shared HTTP server."""
        file_name = os.path.basename(flash_file)
        port = 80

        # Use the provided shared server instead of creating a new one
        callback_output("Using shared HTTP server for U-Boot flash...")
        callback_progress(0.2)

        ser = None
        try:
            ser = serial.Serial(serial_device, 115200, timeout=1)
            ser.dtr = True

            url = f"http://{my_ip}:{port}/{file_name}"
            curl_command = f"curl -o {file_name} {url}\n"
            ser.write(curl_command.encode('utf-8'))
            await asyncio.sleep(5)
            callback_output('Curl command sent.')

            callback_progress(0.6)

            command = "echo 0 > /sys/block/mmcblk0boot0/force_ro\n"
            ser.write(command.encode('utf-8'))
            await asyncio.sleep(4)
            callback_output('Changed MMC to RW')

            callback_progress(0.8)

            command = f'dd if={file_name} of=/dev/mmcblk0boot0 bs=512 seek=256\n'
            ser.write(command.encode('utf-8'))
            await asyncio.sleep(7)
            callback_output("U-Boot flashing complete")
            callback_progress(1)

            # Remove fip file after flashing
            remove_command = f"rm -f {file_name}\n"
            ser.write(remove_command.encode('utf-8'))
            await asyncio.sleep(2)
            callback_output(f"{file_name} removed successfully.")
        finally:
            # Always clean up resources safely
            if ser and ser.is_open:
                ser.close()
            callback_progress(1.0)

    async def flash_eeprom_shared(self, flash_file, my_ip, callback_progress, callback_output, serial_device, shared_server):
        """Flash EEPROM using a shared HTTP server."""
        file_name = os.path.basename(flash_file)
        port = 80

        # Use the provided shared server instead of creating a new one
        callback_output("Using shared HTTP server for EEPROM flash...")
        callback_progress(0.2)

        ser = None
        try:
            ser = serial.Serial(serial_device, 115200, timeout=1)
            ser.dtr = True

            # Power on
            callback_output("Powering on...")
            ser.write(b"obmcutil poweron\n")
            await asyncio.sleep(8)
            callback_progress(0.4)

            # Configure EEPROM
            callback_output("Configuring EEPROM...")
            ser.write(b"echo 24c02 0x50 > /sys/class/i2c-adapter/i2c-1/new_device\n")
            await asyncio.sleep(8)
            callback_progress(0.6)

            # Fetch FRU binary
            url = f"http://{my_ip}:{port}/{file_name}"
            curl_command = f"curl -o {file_name} {url}\n"
            ser.write(curl_command.encode('utf-8'))
            await asyncio.sleep(8)
            callback_output(f"Fetching FRU binary from {url}")

            callback_progress(0.8)

            # Flash EEPROM
            callback_output("Flashing EEPROM...")
            flash_command = f"dd if={file_name} of=/sys/bus/i2c/devices/1-0050/eeprom\n"
            ser.write(flash_command.encode('utf-8'))
            await asyncio.sleep(8)
            callback_output("EEPROM flashing complete.")
            callback_progress(1.0)

            # Remove FRU binary
            callback_output("Removing FRU binary...")
            remove_command = f"rm -f {file_name}\n"
            ser.write(remove_command.encode('utf-8'))
            await asyncio.sleep(5)
            callback_output("FRU binary removed successfully.")

            # Reboot
            callback_output("Rebooting system...")
            ser.write(b"obmcutil poweroff && reboot\n")
            await asyncio.sleep(5)
            callback_output("System reboot initiated.")
            
        except serial.SerialException as e:
            callback_output(f"Serial Error: {e}")
        except Exception as e:
            callback_output(f"EEPROM Flash Error: {e}")
        finally:
            if ser and ser.is_open:
                ser.close()
            callback_progress(1.0)

    def log_message(self, message):
        """Add a message to the log"""
        timestamp = time.strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}\n"
        
        # Create log_text if it doesn't exist yet (during early initialization)
        if hasattr(self, 'log_text'):
            self.log_text.insert(tk.END, formatted_message)
            self.log_text.see(tk.END)
        
        # Also log to main app if available
        if hasattr(self.app_instance, 'log_message'):
            self.app_instance.log_message(f"[Multi-Flash] {message}")


def create_multi_unit_window(parent, app_instance):
    """Create and return the multi-unit flash window"""
    # Check if NanoBMC is selected
    if hasattr(app_instance, 'bmc_type') and app_instance.bmc_type.get() != 2:
        messagebox.showerror("Error", "Multi-unit flashing is only available for NanoBMC devices!\n\nPlease select 'Nano BMC' and try again.")
        return None
    
    return MultiUnitFlashWindow(parent, app_instance)