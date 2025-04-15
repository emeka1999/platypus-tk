import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import threading
import asyncio
import glob
import bmc
import json
import os
import time
import subprocess 
import psutil
from functools import partial

from utils import login
from network import set_ip

class FileSelectionHelper:
    """Helper class to standardize and simplify file/directory selection dialogs"""
    
    @staticmethod
    def select_file(parent, title, last_dir, file_filter=None):
        """Generic file selection with fallbacks for platform compatibility"""
        file_path = ""
        
        # Try zenity first
        try:
            filter_param = []
            if file_filter:
                filter_param = ['--file-filter', file_filter]
                
            result = subprocess.run(
                ['zenity', '--file-selection', f'--filename={last_dir}/', 
                 f'--title={title}'] + filter_param,
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                file_path = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # Try kdialog next
            try:
                filter_str = 'All Files (*)' if not file_filter else file_filter
                result = subprocess.run(
                    ['kdialog', '--getopenfilename', last_dir, filter_str],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    file_path = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # Fall back to a simple CustomTkinter dialog
                file_path = FileSelectionHelper._show_entry_dialog(
                    parent, title, last_dir, f"Enter the full path to {title.lower()}:"
                )
                
        return file_path
        
    @staticmethod
    def select_directory(parent, title, last_dir):
        """Generic directory selection with fallbacks for platform compatibility"""
        directory = ""
        
        # Try zenity first
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--directory', 
                 f'--filename={last_dir}/', f'--title={title}'],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                directory = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # Try kdialog next
            try:
                result = subprocess.run(
                    ['kdialog', '--getexistingdirectory', last_dir, title],
                    capture_output=True, text=True, timeout=60
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
    def _show_entry_dialog(parent, title, default_value, message):
        """Helper method to show a simple input dialog"""
        dialog = ctk.CTkToplevel(parent)
        dialog.title(title)
        dialog.geometry("500x150")
        dialog.attributes('-topmost', True)
        
        path_var = ctk.StringVar(value=default_value)
        ctk.CTkLabel(dialog, text=message).pack(pady=10)
        ctk.CTkEntry(dialog, textvariable=path_var, width=400).pack(pady=10)
        
        result_path = []  # Use a list to store the result
        
        def on_ok():
            result_path.append(path_var.get())
            dialog.destroy()
        
        def on_cancel():
            dialog.destroy()
        
        button_frame = ctk.CTkFrame(dialog)
        button_frame.pack(fill="x", padx=20, pady=10)
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
        self.geometry("500x400")
        
        # Set parent relationship but DON'T make it modal
        self.transient(parent)
        
        # Initialize variables
        self.firmware_folder = ctk.StringVar()
        self.fip_file = ctk.StringVar()
        self.eeprom_file = ctk.StringVar()
        
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

    def save_selections_to_config(self):
        """Save current selections to app config"""
        if self.firmware_folder.get():
            self.app_instance.last_flash_all_folder = self.firmware_folder.get()
            
        if self.fip_file.get():
            self.app_instance.last_flash_all_fip = self.fip_file.get()
            
        if self.eeprom_file.get():
            self.app_instance.last_flash_all_eeprom = self.eeprom_file.get()
            
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
        
        if self.bmc_type != 1:
            ctk.CTkLabel(self, text="EEPROM File (FRU):").pack(pady=5)
            ctk.CTkEntry(self, textvariable=self.eeprom_file, width=400).pack()
            ctk.CTkButton(self, text="Browse", command=self.select_eeprom_file).pack(pady=5)
        
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
        """Select FIP file for flashing U-Boot"""
        # Start with last selected FIP file directory or fall back to general FIP dir
        last_dir = os.path.dirname(self.fip_file.get()) if self.fip_file.get() else None
        if not last_dir:
            last_dir = app.last_fip_dir if hasattr(app, 'last_fip_dir') else os.path.expanduser("~")
        
        file_path = FileSelectionHelper.select_file(
            self, "Select FIP File", 
            last_dir,
            "Binary files (*.bin) | *.bin"
        )
        
        if file_path:
            self.fip_file.set(file_path)
            # Save to both specific and general file paths
            self.app_instance.last_flash_all_fip = file_path
            if hasattr(app, 'last_fip_dir'):
                app.last_fip_dir = os.path.dirname(file_path)
            # Save the configuration if method exists
            if hasattr(app, 'save_config'):
                app.save_config()
    
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
        if not self.firmware_folder.get() or not self.fip_file.get() or (self.bmc_type != 1 and not self.eeprom_file.get()):
            messagebox.showerror("Error", "Please select all required files before proceeding.")
            return
        
        # Save the selections before starting the thread
        self.save_selections_to_config()
        
        threading.Thread(target=self.run_flash_sequence, daemon=True).start()
        self.destroy()  # Close the window when starting the flashing
    
    def run_flash_sequence(self):
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
            bmc_type
        )
        
class PlatypusApp:
    def __init__(self):
        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Create main window
        self.root = ctk.CTk()
        self.root.title("Platypus BMC Management")
        self.root.geometry("800x805")  # Adjusted to fit 1080p
        
        # Initialize variables
        self._init_variables()
        
        # Create configuration directory
        self.config_dir = os.path.expanduser("~/.local/platypus")
        os.makedirs(self.config_dir, exist_ok=True)
        self.CONFIG_FILE = os.path.join(self.config_dir, "platypus_config.json")

        # Load saved configuration
        self.load_config()

        # Create main frame - no longer using ScrollableFrame to avoid scrolling
        self.main_frame = ctk.CTkFrame(self.root)
        self.main_frame.pack(padx=10, pady=10, fill="both", expand=True)

        # Create UI sections
        self._create_ui()
        
        # Do an initial refresh of networks and devices
        self.update_ip_dropdown()
        self.refresh_devices()

        # Bind close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
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

    def execute_flash_all(self, firmware_folder, fip_file, eeprom_file=None, bmc_type=2):
        """
        Execute the complete flash all sequence using the provided files.
        This method should be called from the FlashAllWindow.
        """
        self.log_message("Starting Flash All sequence...")
        self.lock_buttons = True
        
        try:
            # Step 1: Flash eMMC
            self.log_message("Step 1: Flashing eMMC...")
            asyncio.run(bmc.flash_emmc(
                self.bmc_ip.get(), 
                firmware_folder, 
                self.your_ip.get(), 
                self.bmc_type.get(), 
                self.update_progress, 
                self.log_message
            ))
            
            # Step 2: Login to BMC
            self.log_message("Step 2: Logging into BMC...")
            asyncio.run(login(
                self.username.get(), 
                self.password.get(), 
                self.serial_device.get(), 
                self.log_message
            ))
            
            # Step 3: Set BMC IP
            self.log_message("Step 3: Setting BMC IP...")
            asyncio.run(set_ip(
                self.bmc_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
            ))
            
            # Step 4: Flash U-Boot (FIP)
            self.log_message("Step 4: Flashing U-Boot...")
            asyncio.run(bmc.flasher(
                fip_file, 
                self.your_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
            ))
            
            # Step 5: Flash EEPROM (if needed)
            if bmc_type != 1 and eeprom_file:
                self.log_message("Step 5: Flashing EEPROM...")
                asyncio.run(bmc.flash_eeprom(
                    eeprom_file, 
                    self.your_ip.get(), 
                    self.update_progress, 
                    self.log_message, 
                    self.serial_device.get()
                ))
            
            self.log_message("Flash All sequence completed successfully!")
        except Exception as e:
            self.log_message(f"Error during Flash All sequence: {str(e)}")
        finally:
            self.lock_buttons = False

        
    def _create_ui(self):
        """Create all UI sections with reduced vertical spacing"""
        self.create_connection_section()
        self.create_bmc_operations_section()
        self.create_flashing_operations_section()
        self.create_log_section()
        self.create_progress_section()

        
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
            "last_flash_all_eeprom": getattr(self, 'last_flash_all_eeprom', "")
        }
        try:
            with open(self.CONFIG_FILE, 'w') as config_file:
                json.dump(config, config_file)
        except Exception as e:
            print(f"Error saving configuration: {e}")

    def on_close(self):
        """Handle application closing with server cleanup"""
        # Force close any servers before exiting
        self.log_message("Closing application. Cleaning up servers...")
        self.force_close_port_80()
        
        # Save configuration
        self.save_config()
        
        # Destroy the main window
        self.root.destroy()

    def create_connection_section(self):
        """Create the connection settings section with optimized spacing"""
        section = ctk.CTkFrame(self.main_frame)
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
        ctk.CTkEntry(ip_frame, textvariable=self.bmc_ip, height=28).grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        
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


    def create_bmc_operations_section(self):
        """Create the BMC operations section with optimized spacing"""
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="BMC Operations", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=10)
        
        ops = [
            ("Update BMC", self.update_bmc),
            ("Update BIOS", self.update_bios),
            ("Login to BMC", self.login_to_bmc),
            ("Set BMC IP", self.set_bmc_ip),
            ("Power ON Host", self.power_on_host),
            ("Reboot BMC", self.reboot_bmc),
            ("Virtual Media", self.mount_virtual_media),  # New button
            ("Factory Reset", self.factory_reset)
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            ctk.CTkButton(op_frame, text=text, command=command, height=28).grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

    def create_flashing_operations_section(self):
        """Create the flashing operations section with optimized spacing"""
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="Flashing Operations", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=10)
        
        ops = [
            ("Flash FIP (U-Boot)", self.flash_u_boot),
            ("Flash eMMC", self.flash_emmc),
            ("Reset BMC", self.reset_bmc),
            ("Flash FRU (EEPROM)", self.flash_eeprom),
            ("Flash All", self.on_flash_all)
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            ctk.CTkButton(op_frame, text=text, command=command, height=28).grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

    def create_log_section(self):
        """Create the log section with reduced height"""
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="Log", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        self.log_box = ctk.CTkTextbox(section, height=150, state="disabled")  # Reduced height from 200
        self.log_box.pack(padx=10, pady=5, fill="x")

    def create_progress_section(self):
        """Create the progress section and console button with optimized spacing"""
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=5)
        
        progress_frame = ctk.CTkFrame(section)
        progress_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkButton(progress_frame, text="Console", command=self.open_minicom_console, height=28).pack(side="left", padx=5)
        
        self.progress = ctk.CTkProgressBar(progress_frame)
        self.progress.pack(side="left", expand=True, fill="x", padx=5)
        self.progress.set(0)

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

    def get_network_interfaces(self):
        """Get a list of network interfaces with valid IP addresses"""
        ips = []
        
        try:
            # Try to get interfaces using 'ip addr' command (Linux-specific)
            try:
                result = subprocess.run(['ip', 'addr'], capture_output=True, text=True)
                if result.returncode == 0:
                    import re
                    # Parse ip addr output to find interfaces and IPs
                    for line in result.stdout.splitlines():
                        # Look for inet lines with IP addresses
                        match = re.search(r'inet (\d+\.\d+\.\d+\.\d+).*scope global', line)
                        if match:
                            ip = match.group(1)
                            if ip != "127.0.0.1" and ip not in ips:
                                ips.append(ip)
            except:
                pass
                
            # If no interfaces found yet, try the hostname method
            if not ips:
                import socket
                hostname = socket.gethostname()
                
                # Get the primary IP
                try:
                    primary_ip = socket.gethostbyname(hostname)
                    if primary_ip != "127.0.0.1" and primary_ip not in ips:
                        ips.append(primary_ip)
                except:
                    pass
                    
                # Get all interface IPs
                try:
                    hostname_ips = socket.gethostbyname_ex(hostname)[2]
                    for ip in hostname_ips:
                        if ip != "127.0.0.1" and ip not in ips:
                            ips.append(ip)
                except:
                    pass
                
            # If still no interfaces found, add localhost as a fallback
            if not ips:
                ips.append("127.0.0.1")
                
        except Exception as e:
            print(f"Error detecting network interfaces: {e}")
        
        return ips

    def update_ip_dropdown(self):
        """Update the IP address dropdown with available network interfaces"""
        try:
            if not hasattr(self, 'ip_dropdown'):
                return
                
            # Get network interfaces
            ips = self.get_network_interfaces()
            
            # If no interfaces found, leave as is
            if not ips:
                if hasattr(self, 'log_box') and self.log_box:
                    self.log_message("No network interfaces with valid IP addresses found.")
                return
                
            # Update the dropdown values with IPs
            self.ip_dropdown.configure(values=ips)
            
            # Get current IP
            current_ip = self.your_ip.get()
            
            # If we have a current IP, try to select it in the dropdown
            if current_ip in ips:
                self.ip_dropdown.set(current_ip)
            # Otherwise set a default value
            elif ips:
                self.ip_dropdown.set(ips[0])
                self.your_ip.set(ips[0])
                
        except Exception as e:
            if hasattr(self, 'log_box') and self.log_box:
                self.log_message(f"Error updating network interfaces: {e}")
            else:
                print(f"Error updating network interfaces: {e}")



    def create_virtual_media_menu(self):
        """Create a Virtual Media submenu window"""
        vm_window = ctk.CTkToplevel(self.root)
        vm_window.title("Virtual Media Operations")
        vm_window.geometry("300x200")
        vm_window.attributes("-topmost", True)
        
        # Center the window
        vm_window.update_idletasks()
        width = vm_window.winfo_width()
        height = vm_window.winfo_height()
        x = (vm_window.winfo_screenwidth() // 2) - (width // 2)
        y = (vm_window.winfo_screenheight() // 2) - (height // 2)
        vm_window.geometry(f'{width}x{height}+{x}+{y}')
        
        # Create buttons
        ctk.CTkLabel(vm_window, text="Virtual Media Operations", 
                    font=ctk.CTkFont(size=14, weight="bold")).pack(pady=10)
        
        ctk.CTkButton(vm_window, text="Mount ISO", 
                    command=self.mount_virtual_media, 
                    height=35).pack(fill="x", padx=20, pady=10)
        
        ctk.CTkButton(vm_window, text="Eject Media", 
                    command=self.eject_virtual_media, 
                    height=35).pack(fill="x", padx=20, pady=10)
        
        # Make the window modal
        vm_window.grab_set()
        vm_window.focus_set()

    # Update the BMC Operations button handler to call this menu
    def handle_virtual_media(self):
        """Show virtual media operations menu"""
        self.create_virtual_media_menu()

    # Update the virtual media button in create_bmc_operations_section
    # Replace the previous "Virtual Media" entry with this:
    # ("Virtual Media", self.handle_virtual_media)
    

    def eject_virtual_media(self):
        """Eject any mounted virtual media"""
        required = {
            "Username": self.username.get(),
            "Password": self.password.get(),
            "BMC IP": self.bmc_ip.get()
        }
        if self._run_operation(
            self.run_eject_virtual_media,
            required_fields=required,
            error_msg="Please enter all required fields: Username, Password, and BMC IP"
        ):
            self.log_message("Starting Virtual Media ejection operation...")

    def run_eject_virtual_media(self):
        """Run Virtual Media ejection operation"""
        try:
            self.log_message("Ejecting virtual media...")
            result = asyncio.run(bmc.eject_virtual_media(
                self.username.get(),
                self.password.get(),
                self.bmc_ip.get(),
                self.update_progress,
                self.log_message
            ))
            
            if result:
                self.log_message("Virtual Media ejected successfully.")
                
                # Ask if user wants to stop the HTTP server
                if messagebox.askyesno(
                    "Stop HTTP Server", 
                    "Do you want to stop any running HTTP server?\n\n"
                    "This will free up port 80 if it's currently in use."
                ):
                    self.force_close_port_80()
                    self.log_message("HTTP server stopped.")
            else:
                self.log_message("Failed to eject virtual media.")
                
        except Exception as e:
            self.log_message(f"Error during virtual media ejection: {e}")
        finally:
            self.lock_buttons = False

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
        """Open a minicom console for the selected serial device"""
        if not self.serial_device.get():
            self.log_message("No serial device selected. Please select a device.")
            return
        try:
            self.log_message(f"Launching Minicom on {self.serial_device.get()}...")
            subprocess.Popen(["xterm", "-e", "minicom", "-D", self.serial_device.get()])
        except FileNotFoundError:
            self.log_message("Minicom or xterm not found. Please ensure they are installed.")
        except Exception as e:
            self.log_message(f"Error launching Minicom: {e}")

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
        threading.Thread(target=operation_func, daemon=True).start()
        return True
    
    # BMC OPERATIONS



    def mount_virtual_media(self):
        """Mount virtual media (ISO) via Redfish"""
        required = {
            "Username": self.username.get(),
            "Password": self.password.get(),
            "BMC IP": self.bmc_ip.get(),
            "Host IP": self.your_ip.get()
        }
        if self._run_operation(
            self.run_mount_virtual_media,
            required_fields=required,
            error_msg="Please enter all required fields: Username, Password, BMC IP, and Host IP"
        ):
            self.log_message("Starting Virtual Media mounting operation...")

    def run_mount_virtual_media(self):
        """Run Virtual Media mounting operation optimized for OpenBMC"""
        try:
            # First, clean up any existing servers
            self.force_close_port_80()
            
            # Select ISO file
            iso_file = FileSelectionHelper.select_file(
                self.root, 
                "Select ISO Image",
                self.last_firmware_dir,
                "ISO Images (*.iso) | *.iso"
            )
            
            if not iso_file:
                self.log_message("No ISO file selected. Operation aborted.")
                self.lock_buttons = False
                return
                
            # Update last used directory
            self.last_firmware_dir = os.path.dirname(iso_file)
            self.save_config()
            
            self.log_message(f"Selected ISO file: {iso_file}")
            
            # Use the OpenBMC-specific implementation
            result = asyncio.run(bmc.mount_virtual_media_openbmc(
                self.username.get(),
                self.password.get(),
                self.bmc_ip.get(),
                iso_file,
                self.your_ip.get(),
                self.update_progress,
                self.log_message
            ))
            
            if result:
                self.log_message("Virtual Media mounted successfully.")
                
                # Ask if user wants to keep the server running
                if messagebox.askyesno(
                    "Server Running", 
                    "HTTP server is running to serve the ISO.\n\n"
                    "Do you want to keep the server running?\n\n"
                    "Note: You must keep the server running for the virtual media to remain accessible."
                ):
                    self.log_message("HTTP server will continue running in the background.")
                    self.log_message("Remember to close the application properly when done.")
                else:
                    self.log_message("Stopping HTTP server. Virtual media may no longer be accessible.")
                    self.force_close_port_80()
            else:
                self.log_message("Failed to mount virtual media.")
                
        except Exception as e:
            self.log_message(f"Error during virtual media mount: {e}")
        finally:
            self.lock_buttons = False
    
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

    def run_update_bmc(self):
        """Run BMC update operation"""
        try:
            # Select firmware file
            self.flash_file = FileSelectionHelper.select_file(
                self.root, 
                "Select BMC Firmware",
                self.last_firmware_dir,
                "Tar GZ Files (*.tar.gz)"
            )
            
            if not self.flash_file:
                self.log_message("No firmware file selected.")
                self.lock_buttons = False
                return
                
            with open(self.flash_file, 'rb') as fw_file:
                fw_content = fw_file.read()
                self.log_message("Starting BMC Update...")

                asyncio.run(bmc.bmc_update(
                    self.username.get(),
                    self.password.get(),
                    self.bmc_ip.get(),
                    fw_content,
                    self.update_progress,
                    self.log_message,
                ))
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

    def run_login_to_bmc(self):
        """Run BMC login operation"""
        try:
            response = asyncio.run(login(
                self.username.get(), 
                self.password.get(), 
                self.serial_device.get(),
                self.log_message
            ))

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

    def run_set_bmc_ip(self):
        """Run set BMC IP operation"""
        try:
            asyncio.run(set_ip(
                self.bmc_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
            ))
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

    def run_power_on_host(self):
        """Run power on host operation"""
        try:
            asyncio.run(bmc.power_host(
                self.log_message, 
                self.serial_device.get()
            ))
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

    def run_reboot_bmc(self):
        """Run reboot BMC operation"""
        try:
            asyncio.run(bmc.reboot_bmc(
                self.log_message, 
                self.serial_device.get()
            ))
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

    def run_factory_reset(self):
        """Run factory reset operation"""
        try:
            asyncio.run(bmc.bmc_factory_reset(
                self.log_message, 
                self.serial_device.get()
            ))
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

    def run_flash_u_boot(self):
        """Run flash U-Boot operation"""
        try:
            # Select FIP file
            file_path = FileSelectionHelper.select_file(
                self.root,
                "Select FIP File", 
                self.last_fip_dir,
                "Binary files (*.bin) | *.bin"
            )
            
            if not file_path:
                self.log_message("No file selected. Flashing aborted.")
                self.lock_buttons = False
                return
                
            # Update last used directory
            self.last_fip_dir = os.path.dirname(file_path)
            self.save_config()
                
            # Validate filename (optional)
            allowed_files = {"fip-snuc-nanobmc.bin", "fip-snuc-mos-bmc.bin"}
            filename = os.path.basename(file_path)
            if filename not in allowed_files:
                self.log_message(f"Warning: Selected file '{filename}' does not match expected filenames. "
                                 f"Expected: {' or '.join(allowed_files)}. Continuing anyway...")
            
            self.flash_file = file_path
            self.log_message(f"Selected FIP file: {file_path}")
            
            # Run the flashing process
            asyncio.run(bmc.flasher(
                self.flash_file, 
                self.your_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
            ))
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

    def run_flash_emmc(self):
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

            # Continue with flashing process
            asyncio.run(bmc.flash_emmc(
                self.bmc_ip.get(),
                firmware_directory,
                self.your_ip.get(),
                self.bmc_type.get(),
                self.update_progress,
                self.log_message
            ))
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

    def run_reset_bmc(self):
        """Run reset BMC operation"""
        try:
            asyncio.run(bmc.reset_uboot(self.log_message))
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

    def run_flash_eeprom(self):
        """Run flash EEPROM operation"""
        try:
            # Select EEPROM file
            file_path = FileSelectionHelper.select_file(
                self.root,
                "Select EEPROM File", 
                self.last_eeprom_dir,
                "Binary files (*.bin) | *.bin"
            )
            
            if not file_path:
                self.log_message("No file selected for EEPROM flashing. Process aborted.")
                self.lock_buttons = False
                return
                
            # Update last used directory
            self.last_eeprom_dir = os.path.dirname(file_path)
            self.save_config()
            
            self.flash_file = file_path
            self.log_message(f"Starting EEPROM flashing with file: {self.flash_file}")
            
            # Run the flashing process
            asyncio.run(bmc.flash_eeprom(
                self.flash_file, 
                self.your_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
            ))
        except Exception as e:
            self.log_message(f"Error during EEPROM flashing: {e}")
        finally:
            self.lock_buttons = False
            
    def on_flash_all(self):
        """Open the Flash All window"""
        if self.bmc_type.get() == 0:
            messagebox.showerror("Error", "Please select a BMC type before proceeding.")
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

    def run_update_bios(self):
        """Run BIOS update operation"""
        try:
            # Select firmware file
            self.flash_file = FileSelectionHelper.select_file(
                self.root, 
                "Select BIOS Firmware",
                self.last_firmware_dir,
                "BIOS Firmware Files (*.tar.gz)"
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

                asyncio.run(bmc.bios_update(
                    self.username.get(),
                    self.password.get(),
                    self.bmc_ip.get(),
                    fw_content,
                    self.update_progress,
                    self.log_message,
                ))
        except Exception as e:
            self.log_message(f"Error during BIOS update: {e}")
        finally:
            self.lock_buttons = False

def main():
    """Main entry point for the application"""
    global app  # Ensure app is accessible globally for child windows
    app = PlatypusApp()
    app.root.mainloop()

if __name__ == "__main__":
    main()