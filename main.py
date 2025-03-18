import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import asyncio
import glob
import bmc
import json
import os
import subprocess 
import psutil

from utils import login
from network import set_ip

class FlashAllWindow(ctk.CTkToplevel):
    def __init__(self, parent, bmc_type):
    
        super().__init__(parent)
        self.bmc_type = bmc_type
        self.title("Select Files for Flashing")
        self.geometry("500x400")
        
        # Set the window to always stay on top
        self.attributes("-topmost", True)
        
        self.firmware_folder = ctk.StringVar()
        self.fip_file = ctk.StringVar()
        self.eeprom_file = ctk.StringVar()
        
        ctk.CTkLabel(self, text="Firmware Folder (eMMC):").pack(pady=5)
        ctk.CTkEntry(self, textvariable=self.firmware_folder, width=400).pack()
        ctk.CTkButton(self, text="Browse", command=self.select_firmware_folder).pack(pady=5)
        
        ctk.CTkLabel(self, text="FIP File (U-Boot):").pack(pady=5)
        ctk.CTkEntry(self, textvariable=self.fip_file, width=400).pack()
        ctk.CTkButton(self, text="Browse", command=self.select_fip_file).pack(pady=5)
        
        if self.bmc_type != 1:
            ctk.CTkLabel(self, text="EEPROM File (FRU):").pack(pady=5)
        if self.bmc_type != 1:
            ctk.CTkEntry(self, textvariable=self.eeprom_file, width=400).pack()
        if self.bmc_type != 1:
            ctk.CTkButton(self, text="Browse", command=self.select_eeprom_file).pack(pady=5)
        
        ctk.CTkButton(self, text="Start Flashing", command=self.start_flashing).pack(pady=20)

    def select_firmware_folder(self):
        # Use Linux native dialog for directory selection
        import subprocess
        
        folder = ""
        
        # Try zenity first
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--directory', '--title=Select Firmware Folder'],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                folder = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # If zenity fails, try kdialog
            try:
                result = subprocess.run(
                    ['kdialog', '--getexistingdirectory', 'Select Firmware Folder'],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    folder = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # Fall back to a simple entry dialog
                dialog = ctk.CTkToplevel(self)
                dialog.title("Enter Folder Path")
                dialog.geometry("500x150")
                dialog.attributes('-topmost', True)
                
                path_var = ctk.StringVar()
                ctk.CTkLabel(dialog, text="Enter the full path to firmware folder:").pack(pady=10)
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
                
                if result_path:
                    folder = result_path[0]
        
        if folder:
            self.firmware_folder.set(folder)
    
    def select_fip_file(self):
        # Use Linux native dialog for file selection
        import subprocess
        
        file_path = ""
        
        # Try zenity first
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--title=Select FIP File', '--file-filter=Binary files (*.bin) | *.bin'],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                file_path = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # If zenity fails, try kdialog
            try:
                result = subprocess.run(
                    ['kdialog', '--getopenfilename', '.', 'Binary Files (*.bin)'],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    file_path = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # Fall back to a simple entry dialog
                dialog = ctk.CTkToplevel(self)
                dialog.title("Enter FIP File Path")
                dialog.geometry("500x150")
                dialog.attributes('-topmost', True)
                
                path_var = ctk.StringVar()
                ctk.CTkLabel(dialog, text="Enter the full path to FIP file (*.bin):").pack(pady=10)
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
                
                if result_path:
                    file_path = result_path[0]
        
        if file_path:
            self.fip_file.set(file_path)
    
    def select_eeprom_file(self):
        # Use Linux native dialog for file selection
        import subprocess
        
        file_path = ""
        
        # Try zenity first
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--title=Select EEPROM File', '--file-filter=Binary files (*.bin) | *.bin'],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                file_path = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # If zenity fails, try kdialog
            try:
                result = subprocess.run(
                    ['kdialog', '--getopenfilename', '.', 'Binary Files (*.bin)'],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    file_path = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # Fall back to a simple entry dialog
                dialog = ctk.CTkToplevel(self)
                dialog.title("Enter EEPROM File Path")
                dialog.geometry("500x150")
                dialog.attributes('-topmost', True)
                
                path_var = ctk.StringVar()
                ctk.CTkLabel(dialog, text="Enter the full path to EEPROM file (*.bin):").pack(pady=10)
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
                
                if result_path:
                    file_path = result_path[0]
        
        if file_path:
            self.eeprom_file.set(file_path)
    
    def start_flashing(self):
        if not self.firmware_folder.get() or not self.fip_file.get() or (self.bmc_type != 1 and not self.eeprom_file.get()):
            messagebox.showerror("Error", "Please select all required files before proceeding.")
            return
        
        threading.Thread(target=self.run_flash_sequence).start()
    
    def run_flash_sequence(self):
        app.log_message("Starting full flashing process...")
        
        # Step 1: Flash eMMC
        app.log_message("Flashing eMMC...")
        asyncio.run(bmc.flash_emmc(app.bmc_ip.get(), self.firmware_folder.get(), app.your_ip.get(), app.bmc_type.get(), app.update_progress, app.log_message))
        
        # Step 2: Login to BMC
        app.log_message("Logging into BMC...")
        asyncio.run(login(app.username.get(), app.password.get(), app.serial_device.get(), app.log_message))
        
        # Step 3: Set BMC IP
        app.log_message("Setting BMC IP...")
        asyncio.run(set_ip(app.bmc_ip.get(), app.update_progress, app.log_message, app.serial_device.get()))
        
        # Step 4: Flash U-Boot
        app.log_message("Flashing U-Boot...")
        asyncio.run(bmc.flasher(self.fip_file.get(), app.your_ip.get(), app.update_progress, app.log_message, app.serial_device.get()))
        
        # Step 5: Flash EEPROM
        app.log_message("Flashing EEPROM...")
        asyncio.run(bmc.flash_eeprom(self.eeprom_file.get(), app.your_ip.get(), app.update_progress, app.log_message, app.serial_device.get()))
        
        app.log_message("Flashing process complete!")
        app.lock_buttons = False

class PlatypusApp:
    def __init__(self):
        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Create configuration directory
        self.config_dir = os.path.expanduser("~/.local/platypus")
        os.makedirs(self.config_dir, exist_ok=True)
        self.CONFIG_FILE = os.path.join(self.config_dir, "platypus_config.json")

        # Create main window
        self.root = ctk.CTk()
        self.root.title("Platypus BMC Management")
        self.root.geometry("800x1900")

        # Initialize variables
        self.username = ctk.StringVar()
        self.password = ctk.StringVar()
        self.bmc_ip = ctk.StringVar()
        self.your_ip = ctk.StringVar()
        self.flash_file = None
        self.serial_device = ctk.StringVar()
        self.bmc_type = ctk.IntVar(value=2)
        self.lock_buttons = False

        # Load saved configuration
        self.load_config()

        # Create main frame
        self.main_frame = ctk.CTkScrollableFrame(self.root)
        self.main_frame.pack(padx=20, pady=20, fill="both", expand=True)

        # Create sections
        self.create_connection_section()
        self.create_bmc_operations_section()
        self.create_flashing_operations_section()
        self.create_log_section()
        self.create_progress_section()

        # Bind close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
    def run(self):
        """Start the GUI application."""
        self.root.mainloop()   

    def create_connection_section(self):
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=10)
        
        ctk.CTkLabel(section, text="Connection Settings", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        
        # Serial Device
        device_frame = ctk.CTkFrame(section)
        device_frame.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(device_frame, text="Serial Device:").pack(side="left", padx=5)
        serial_dropdown = ctk.CTkComboBox(device_frame, variable=self.serial_device)
        serial_dropdown.pack(side="left", expand=True, fill="x", padx=5)
        ctk.CTkButton(device_frame, text="Refresh", command=self.refresh_devices, width=100).pack(side="right", padx=5)

        # Credentials
        cred_frame = ctk.CTkFrame(section)
        cred_frame.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(cred_frame, text="Username:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        ctk.CTkEntry(cred_frame, textvariable=self.username).grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        ctk.CTkLabel(cred_frame, text="Password:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        ctk.CTkEntry(cred_frame, textvariable=self.password, show='*').grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        
        cred_frame.grid_columnconfigure(1, weight=1)

        # IP Settings
        ip_frame = ctk.CTkFrame(section)
        ip_frame.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(ip_frame, text="BMC IP:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        ctk.CTkEntry(ip_frame, textvariable=self.bmc_ip).grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        ctk.CTkLabel(ip_frame, text="Host IP:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        ctk.CTkEntry(ip_frame, textvariable=self.your_ip).grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        
        ip_frame.grid_columnconfigure(1, weight=1)

        # BMC Type
        type_frame = ctk.CTkFrame(section)
        type_frame.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(type_frame, text="BMC Type:").pack(side="left", padx=5)
        ctk.CTkRadioButton(type_frame, text="MOS BMC", variable=self.bmc_type, value=1).pack(side="left", padx=10)
        ctk.CTkRadioButton(type_frame, text="Nano BMC", variable=self.bmc_type, value=2).pack(side="left")


    def safe_select_directory(parent_window, title="Select Directory"):
        """
        A more stable directory selector that won't freeze the application
        when used multiple times.
        
        Args:
            parent_window: The parent window
            title: Dialog title
            
        Returns:
            Selected directory path or empty string if canceled
        """
        # Try using system dialog through zenity if available
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--directory', f'--title={title}'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass  # Fall back to tkinter if zenity isn't available or fails
            
        # If zenity failed or isn't available, use a fresh tkinter dialog
        try:
            # Create a separate, dedicated window for the dialog
            dialog_root = tk.Tk()
            dialog_root.withdraw()  # Hide the window
            dialog_root.attributes('-topmost', True)  # Keep on top
            dialog_root.focus_force()  # Force focus
            
            # Use this new window as the dialog parent
            directory = filedialog.askdirectory(parent=dialog_root, title=title)
            
            # Clean up
            dialog_root.destroy()
            
            return directory
        except Exception as e:
            print(f"Error in directory selection: {e}")
            return ""

    def create_bmc_operations_section(self):
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=10)
        
        ctk.CTkLabel(section, text="BMC Operations", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=20)
        
        ops = [
            ("Update BMC", self.update_bmc),
            ("Login to BMC", self.login_to_bmc),
            ("Set BMC IP", self.set_bmc_ip),
            ("Power ON Host", self.power_on_host),
            ("Reboot BMC", self.reboot_bmc),
            ("Factory Reset", self.factory_reset)
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            ctk.CTkButton(op_frame, text=text, command=command).grid(row=row, column=col, padx=5, pady=5, sticky="ew")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

    def create_flashing_operations_section(self):
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=10)
        
        ctk.CTkLabel(section, text="Flashing Operations", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=20)
        
        ops = [
            ("Flash FIP (U-Boot)", self.flash_u_boot),
            ("Flash eMMC", self.flash_emmc),
            ("Reset BMC", self.reset_bmc),
            ("Flash FRU (EEPROM)", self.flash_eeprom),
            ("Flash All", self.on_flash_all)
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            ctk.CTkButton(op_frame, text=text, command=command).grid(row=row, column=col, padx=5, pady=5, sticky="ew")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

    def create_log_section(self):
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=10)
        
        ctk.CTkLabel(section, text="Log", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        
        self.log_box = ctk.CTkTextbox(section, height=200, state="disabled")
        self.log_box.pack(padx=20, pady=10, fill="x")

    def create_progress_section(self):
        section = ctk.CTkFrame(self.main_frame)
        section.pack(fill="x", pady=10)
        
        progress_frame = ctk.CTkFrame(section)
        progress_frame.pack(fill="x", padx=20)
        
        ctk.CTkButton(progress_frame, text="Console", command=self.open_minicom_console).pack(side="left", padx=5)
        
        self.progress = ctk.CTkProgressBar(progress_frame)
        self.progress.pack(side="left", expand=True, fill="x", padx=5)
        self.progress.set(0)

    def load_config(self):
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r') as config_file:
                    config = json.load(config_file)
                    self.username.set(config.get("username", ""))
                    self.password.set(config.get("password", ""))
                    self.bmc_ip.set(config.get("bmc_ip", ""))
                    self.your_ip.set(config.get("your_ip", ""))
        except (json.JSONDecodeError, FileNotFoundError):
            # Create an empty config file if it doesn't exist or is invalid
            self.save_config()

    def save_config(self):
        config = {
            "username": self.username.get(),
            "password": self.password.get(),
            "bmc_ip": self.bmc_ip.get(),
            "your_ip": self.your_ip.get()
        }
        with open(self.CONFIG_FILE, 'w') as config_file:
            json.dump(config, config_file)

    def on_close(self):
        self.save_config()
        self.root.destroy()

    def refresh_devices(self):
        """Find all available serial devices and update the dropdown."""
        # Find all serial devices
        devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        
        if not devices:
            self.log_message("No serial devices found.")
            self.serial_device.set("")
            return
        
        # Find the dropdown widget
        for widget in self.main_frame.winfo_children():
            if isinstance(widget, ctk.CTkFrame):
                for frame in widget.winfo_children():
                    if isinstance(frame, ctk.CTkFrame):
                        for child in frame.winfo_children():
                            if isinstance(child, ctk.CTkComboBox):
                                # Found the dropdown, update its values
                                child.configure(values=devices)
                                # Log the found devices
                                self.log_message(f"Found {len(devices)} serial devices: {', '.join(devices)}")
                                # Set the first device if not already set
                                if not self.serial_device.get() and devices:
                                    self.serial_device.set(devices[0])
                                return
        
        # If we get here, we didn't find the dropdown - still set the variable
        self.log_message(f"Found {len(devices)} serial devices, but couldn't update dropdown")
        if devices:
            self.serial_device.set(devices[0])

    def log_message(self, message):
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, f"{message}\n")
        self.log_box.configure(state="disabled")
        self.log_box.see(tk.END)

    def update_progress(self, value):
        self.progress.set(value)

    def validate_button_click(self):
        if self.lock_buttons:
            self.log_message("Another operation is in progress. Please wait...")
            return False
        self.lock_buttons = True
        return True

    def open_minicom_console(self):
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

    # All other methods from the original implementation would be copied here
    # This includes methods like update_bmc, login_to_bmc, set_bmc_ip, etc.
    # I'll provide a placeholder for these methods:

    def update_bmc(self):
        if not self.validate_button_click():
            return
        if not self.username.get() or not self.password or not self.bmc_ip:
            self.log_message("Please enter all required fields: Username, Password, BMC IP")
            self.lock_buttons = False
            return 

        self.flash_file = filedialog.askopenfilename(filetypes=[("Tar GZ Files", "*.tar.gz")])
        if not self.flash_file:
            self.log_message("No file selected.")
            self.lock_buttons = False
            return

        threading.Thread(target=self.run_update_bmc).start()

    def run_update_bmc(self):
        self.lock_buttons = True
        try:
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
        if not self.validate_button_click():
            return
        if not self.username.get() or not self.password.get() or not self.serial_device.get():
            self.log_message("Error: Missing input(s). Please enter username, password, and select a device.")
            self.lock_buttons = False
            return
        try:
            self.lock_buttons = True
            self.log_message("Attempting to log in to BMC...")
            response = asyncio.run(login(
                self.username.get(), self.password.get(), self.serial_device.get(),
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
        if not self.validate_button_click():
            return
        if not self.bmc_ip.get():
            self.log_message("Please enter BMC IP")
            self.lock_buttons = False
            return

        threading.Thread(target=self.run_set_bmc_ip).start()

    def run_set_bmc_ip(self):
        self.lock_buttons = True
        try:
            asyncio.run(set_ip(
                self.bmc_ip.get(), self.update_progress, self.log_message, self.serial_device.get()
            ))
        except Exception as e:
            self.log_message(f"Error during IP setup: {e}")
        finally:
            self.lock_buttons = False

    def power_on_host(self):
        if not self.validate_button_click():
            return
        if not self.serial_device.get():
            self.log_message("Please enter all required fields: Serial Device")
            self.lock_buttons = False
            return

        threading.Thread(target=self.run_power_on_host).start()

    def run_power_on_host(self):
        self.lock_buttons = True
        try:
            asyncio.run(bmc.power_host(
                self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False

    def reboot_bmc(self):
        if not self.validate_button_click():
            return
        if not self.serial_device.get():
            self.log_message("No serial device selected. Please select a device.")
            self.lock_buttons = False
            return
        try:
            self.lock_buttons = True
            asyncio.run(bmc.reboot_bmc(
                self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False

    def factory_reset(self):
        if not self.validate_button_click():
            return
        if not self.serial_device.get():
            self.log_message("Please select a device.")
            self.lock_buttons = False
            return

        # Start the thread with the correct context
        threading.Thread(target=self.run_factory_reset).start()

    def run_factory_reset(self):
        self.lock_buttons = True
        try:
            asyncio.run(bmc.bmc_factory_reset(
                self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False
    def flash_u_boot(self):
        if not self.validate_button_click():
            return
        if (not self.your_ip.get() or not self.update_progress or 
            not self.log_message or not self.serial_device.get()):
            self.log_message("Please enter all required fields: Host IP and Serial Device")
            self.lock_buttons = False
            return

        # Start thread for file selection and flashing
        threading.Thread(target=self.run_flash_u_boot).start()

    def run_flash_u_boot(self):
        self.lock_buttons = True
        try:
            # Use Linux native file dialog
            import subprocess
            
            file_path = ""
            
            # Try zenity first (common on most Linux distributions)
            try:
                result = subprocess.run(
                    ['zenity', '--file-selection', '--title=Select FIP File', '--file-filter=Binary files (*.bin) | *.bin'],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    file_path = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # If zenity fails or isn't available, try kdialog (KDE)
                try:
                    result = subprocess.run(
                        ['kdialog', '--getopenfilename', '.', 'Binary Files (*.bin)'],
                        capture_output=True, text=True, timeout=60
                    )
                    if result.returncode == 0:
                        file_path = result.stdout.strip()
                except (subprocess.SubprocessError, FileNotFoundError):
                    # If all else fails, fall back to a simple text prompt
                    self.log_message("Could not open a graphical file dialog. Please type the file path manually...")
                    # Create a simple entry dialog using CustomTkinter
                    dialog = ctk.CTkToplevel(self.root)
                    dialog.title("Enter FIP File Path")
                    dialog.geometry("500x150")
                    dialog.attributes('-topmost', True)
                    
                    path_var = ctk.StringVar()
                    ctk.CTkLabel(dialog, text="Enter the full path to the FIP file (*.bin):").pack(pady=10)
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
                    
                    if result_path:
                        file_path = result_path[0]
            
            if not file_path:
                self.log_message("No file selected. Flashing aborted.")
                self.lock_buttons = False
                return
                
            # Validate filename
            import os
            allowed_files = {"fip-snuc-nanobmc.bin", "fip-snuc-mos-bmc.bin"}
            if os.path.basename(file_path) not in allowed_files:
                self.log_message("Invalid file selected. Please choose either 'fip-snuc-nanobmc.bin' or 'fip-snuc-mos-bmc.bin'.")
                self.lock_buttons = False
                return
            
            self.flash_file = file_path
            self.log_message(f"Selected FIP file: {file_path}")
            
            # Run the flashing process
            asyncio.run(bmc.flasher(
                self.flash_file, self.your_ip.get(), self.update_progress, self.log_message, self.serial_device.get()
            ))
        except Exception as e:
            self.log_message(f"Error during FIP flashing: {e}")
        finally:
            self.lock_buttons = False

    def flash_emmc(self):
        # Check if already running
        if hasattr(self, 'emmc_operation_running') and self.emmc_operation_running:
            self.log_message("Flash eMMC operation already in progress. Please wait for it to complete.")
            return
            
        # Standard validation
        if not self.validate_button_click():
            return
        if not self.bmc_type.get():
            self.log_message("Please select a BMC type.")
            self.lock_buttons = False
            return
        if not self.bmc_ip.get() or not self.your_ip.get():
            self.log_message("Please enter all required fields: BMC IP and Host IP")
            self.lock_buttons = False
            return

        # Set flag to indicate operation is running
        self.emmc_operation_running = True
        
        # Start thread
        threading.Thread(target=self.run_flash_emmc).start()

    def run_flash_emmc(self):
        self.lock_buttons = True
        try:
            self.log_message("Starting eMMC flashing process...")

            # Use Linux native file dialog
            import subprocess
            
            firmware_directory = ""
            
            # Try zenity first (common on most Linux distributions)
            try:
                result = subprocess.run(
                    ['zenity', '--file-selection', '--directory', '--title=Select Firmware Directory'],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    firmware_directory = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # If zenity fails or isn't available, try kdialog (KDE)
                try:
                    result = subprocess.run(
                        ['kdialog', '--getexistingdirectory', 'Select Firmware Directory'],
                        capture_output=True, text=True, timeout=60
                    )
                    if result.returncode == 0:
                        firmware_directory = result.stdout.strip()
                except (subprocess.SubprocessError, FileNotFoundError):
                    # If all else fails, fall back to a simple text prompt
                    self.log_message("Could not open a graphical file dialog. Please type the directory path manually...")
                    # Create a simple entry dialog using CustomTkinter
                    dialog = ctk.CTkToplevel(self.root)
                    dialog.title("Enter Directory Path")
                    dialog.geometry("500x150")
                    dialog.attributes('-topmost', True)
                    
                    path_var = ctk.StringVar()
                    ctk.CTkLabel(dialog, text="Enter the full path to firmware directory:").pack(pady=10)
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
                    
                    if result_path:
                        firmware_directory = result_path[0]
            
            if not firmware_directory:
                self.log_message("No directory selected. Cleaning up and aborting...")
                # Cleanup any running server processes
                self.cleanup_server_processes()
                self.lock_buttons = False
                self.emmc_operation_running = False
                return

            # Continue with the flashing process
            asyncio.run(bmc.flash_emmc(
                self.bmc_ip.get(),
                firmware_directory,
                self.your_ip.get(),
                self.bmc_type.get(),
                self.update_progress,
                self.log_message,
            ))
        except Exception as e:
            self.log_message(f"Error during eMMC flashing: {e}")
            # Cleanup on error
            self.cleanup_server_processes()
        finally:
            self.lock_buttons = False
            self.emmc_operation_running = False  # Always reset the operation flag when done

    def cleanup_server_processes(self):
        """Clean up any running server processes (TFTP, HTTP, etc.)"""
        self.log_message("Stopping any running servers...")
        try:
            # Look for TFTP server processes
            import psutil
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
            import subprocess
            try:
                # Try to kill any TFTP servers bound to our IP
                subprocess.run(f"pkill -f 'tftp.*{self.your_ip.get()}'", shell=True)
                # Try to kill any HTTP servers bound to our IP
                subprocess.run(f"pkill -f 'http.server.*{self.your_ip.get()}'", shell=True)
            except Exception:
                pass
            
            self.log_message("Server cleanup completed.")
        except Exception as e:
            self.log_message(f"Warning: Error during server cleanup: {e}")

    def reset_bmc(self):
        if not self.validate_button_click():
            return

        threading.Thread(target=self.run_reset_bmc).start()

    def run_reset_bmc(self):
        self.lock_buttons = True
        try:
            asyncio.run(bmc.reset_uboot(self.log_message))
        finally:
            self.lock_buttons = False

    def flash_eeprom(self):
        if not self.validate_button_click():
            return
        if not self.your_ip.get() or not self.serial_device.get():
            self.log_message("Please enter all required fields: Host IP and Serial Device")
            self.lock_buttons = False
            return

        threading.Thread(target=self.run_flash_eeprom).start()

    def run_flash_eeprom(self):
        self.lock_buttons = True
        try:
            # Use Linux native file dialog
            import subprocess
            
            file_path = ""
            
            # Try zenity first (common on most Linux distributions)
            try:
                result = subprocess.run(
                    ['zenity', '--file-selection', '--title=Select FRU EEPROM File', '--file-filter=Binary files (*.bin) | *.bin'],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    file_path = result.stdout.strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                # If zenity fails or isn't available, try kdialog (KDE)
                try:
                    result = subprocess.run(
                        ['kdialog', '--getopenfilename', '.', 'Binary Files (*.bin)'],
                        capture_output=True, text=True, timeout=60
                    )
                    if result.returncode == 0:
                        file_path = result.stdout.strip()
                except (subprocess.SubprocessError, FileNotFoundError):
                    # If all else fails, fall back to a simple text prompt
                    self.log_message("Could not open a graphical file dialog. Please type the file path manually...")
                    # Create a simple entry dialog using CustomTkinter
                    dialog = ctk.CTkToplevel(self.root)
                    dialog.title("Enter EEPROM File Path")
                    dialog.geometry("500x150")
                    dialog.attributes('-topmost', True)
                    
                    path_var = ctk.StringVar()
                    ctk.CTkLabel(dialog, text="Enter the full path to the FRU EEPROM file (*.bin):").pack(pady=10)
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
                    
                    if result_path:
                        file_path = result_path[0]
            
            if not file_path:
                self.log_message("No file selected for EEPROM flashing. Process aborted.")
                self.lock_buttons = False
                return
            
            self.flash_file = file_path
            self.log_message(f"Starting EEPROM flashing with file: {self.flash_file}")
            
            # Run the flashing process
            asyncio.run(bmc.flash_eeprom(
                self.flash_file, self.your_ip.get(), self.update_progress, self.log_message, self.serial_device.get()
            ))
        except Exception as e:
            self.log_message(f"Error during EEPROM flashing: {e}")
        finally:
            self.lock_buttons = False
            
    def on_flash_all(self):
        if app.bmc_type.get() == 0:
            messagebox.showerror("Error", "Please select a BMC type before proceeding.")
            return
        FlashAllWindow(self.root, self.bmc_type.get())


def main():
    global app  # Ensure app is accessible globally if needed
    app = PlatypusApp()
    app.run()

if __name__ == "__main__":
    main()