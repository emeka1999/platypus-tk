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
import pty
import io
import fcntl
import termios
import tty
import select
from utils import *
from network import *
from functools import partial
import queue
from threading import Thread


class EmbeddedConsole(ctk.CTkFrame):
    """Embedded console widget that displays serial output and allows interaction"""
    
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        
        # Console styling colors
        self.bg_color = "#0f0f0f"         # Very dark gray/black for terminal look
        self.text_color = "#cccccc"        # Light gray for standard text
        self.cmd_color = "#00cc00"         # Bright green for commands
        self.err_color = "#ff5555"         # Bright red for errors
        self.system_color = "#5599ff"      # Blue for system messages
        
        # Create frame for console and scrollbar
        self.console_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.console_frame.pack(fill="both", expand=True, padx=3, pady=(3, 0))
        
        # Create console text widget with monospace font and terminal-like styling
        self.console = ctk.CTkTextbox(
            self.console_frame, 
            font=("Courier", 11),
            wrap="none",
            fg_color=self.bg_color,
            text_color=self.text_color,
            corner_radius=3,
            border_width=1,
            border_color="#333333"
        )
        self.console.pack(side="left", fill="both", expand=True)
        self.console.configure(state="disabled")
        
        # Add scrollbar - now using pack with the same parent as the text widget
        scrollbar = ctk.CTkScrollbar(self.console_frame, command=self.console.yview)
        scrollbar.pack(side="right", fill="y")
        self.console.configure(yscrollcommand=scrollbar.set)
        
        # Input entry for sending commands
        self.input_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.input_frame.pack(fill="x", side="bottom", padx=3, pady=(0, 3))
        
        # Command prompt label
        self.prompt_label = ctk.CTkLabel(
            self.input_frame, 
            text="$", 
            font=("Courier", 11, "bold"),
            text_color=self.cmd_color, 
            width=20
        )
        self.prompt_label.pack(side="left", padx=(5, 0))
        
        # Command input with custom styling
        self.input_entry = ctk.CTkEntry(
            self.input_frame,
            font=("Courier", 11),
            fg_color=self.bg_color,
            text_color=self.cmd_color,
            border_width=1,
            border_color="#333333"
        )
        self.input_entry.pack(fill="x", side="left", expand=True, padx=5, pady=3)
        
        # Send button
        self.send_button = ctk.CTkButton(
            self.input_frame, 
            text="Send", 
            width=60, 
            height=24,
            command=self.send_command,
            fg_color="#005500",
            hover_color="#007700"
        )
        self.send_button.pack(side="right", padx=3, pady=3)
        
        # Bind Enter key to send command
        self.input_entry.bind("<Return>", lambda event: self.send_command())
        
        # Add command history functionality
        self.command_history = []
        self.history_position = 0
        self.input_entry.bind("<Up>", self.previous_command)
        self.input_entry.bind("<Down>", self.next_command)
        
        # Serial connection
        self.serial_device = None
        self.serial_thread = None
        self.running = False
        self.serial_queue = queue.Queue()
        
        # Add text tags for styling
        self.console.tag_config("cmd", foreground=self.cmd_color)
        self.console.tag_config("err", foreground=self.err_color)
        self.console.tag_config("sys", foreground=self.system_color)
        
        # System welcome message
        self.append_text("┌─────────────────────────────────────┐\n", "sys")
        self.append_text("│ Platypus BMC Serial Console         │\n", "sys")
        self.append_text("│ Select a device and click Connect   │\n", "sys")
        self.append_text("└─────────────────────────────────────┘\n", "sys")
    
    # Rest of the methods remain the same
    def previous_command(self, event):
        """Go to previous command in history"""
        if not self.command_history:
            return
            
        if self.history_position > 0:
            self.history_position -= 1
            self.input_entry.delete(0, 'end')
            self.input_entry.insert(0, self.command_history[self.history_position])
            
        return "break"  # Prevent default behavior
        
    def next_command(self, event):
        """Go to next command in history"""
        if not self.command_history:
            return
            
        if self.history_position < len(self.command_history) - 1:
            self.history_position += 1
            self.input_entry.delete(0, 'end')
            self.input_entry.insert(0, self.command_history[self.history_position])
        elif self.history_position == len(self.command_history) - 1:
            # Clear input if at end of history
            self.history_position = len(self.command_history)
            self.input_entry.delete(0, 'end')
            
        return "break"  # Prevent default behavior
    
    def connect(self, serial_device):
        """Connect to the specified serial device"""
        if self.running and self.serial_device == serial_device:
            self.append_text("Already connected to this device.\n", "err")
            return True
            
        if self.running:
            self.disconnect()
            
        self.serial_device = serial_device
        
        self.append_text(f"Connecting to {serial_device}...\n", "sys")
        
        try:
            # Start the serial reading thread
            self.running = True
            self.serial_thread = Thread(target=self.read_serial_thread, daemon=True)
            self.serial_thread.start()
            return True
        except Exception as e:
            self.append_text(f"Error connecting to {serial_device}: {str(e)}\n", "err")
            self.running = False
            return False
    
    def disconnect(self):
        """Disconnect from the current serial device"""
        if not self.running:
            return
            
        self.running = False
        self.append_text("Disconnected from serial device.\n", "sys")
        
        # Wait for thread to terminate
        if self.serial_thread:
            self.serial_thread.join(timeout=1.0)
            self.serial_thread = None
    
    def read_serial_thread(self):
        """Thread that reads from the serial device and puts data in the queue"""
        import serial
        
        try:
            ser = serial.Serial(self.serial_device, 115200, timeout=0.1)
            
            self.append_text_queue(f"Connected to {self.serial_device}\n", "sys")
            
            # Put empty string to ensure UI updates
            self.serial_queue.put("")
            
            # Main read loop
            while self.running:
                try:
                    # Try to read data
                    if ser.in_waiting:
                        data = ser.read(ser.in_waiting)
                        if data:
                            # Try to decode as UTF-8 with error handling
                            text = data.decode('utf-8', errors='replace')
                            self.serial_queue.put(text)
                    
                    # Brief sleep to reduce CPU usage
                    time.sleep(0.05)
                except Exception as e:
                    self.serial_queue.put((f"Read error: {str(e)}\n", "err"))
                    time.sleep(1)  # Longer sleep after error
            
            # Close serial port when done
            ser.close()
            
        except Exception as e:
            self.serial_queue.put((f"Serial connection error: {str(e)}\n", "err"))
        
        self.serial_queue.put(("Serial thread terminated.\n", "sys"))
    
    def process_serial_queue(self):
        """Process any pending data in the serial queue"""
        try:
            while not self.serial_queue.empty():
                text = self.serial_queue.get_nowait()
                if text:
                    self.append_text(text)
        except Exception as e:
            print(f"Error processing serial queue: {e}")
        
        # Schedule next check if still running
        if self.running:
            self.after(50, self.process_serial_queue)
    
    def append_text_queue(self, text, tag=None):
        """Add text to the queue from a non-UI thread"""
        if tag:
            self.serial_queue.put((text, tag))
        else:
            self.serial_queue.put(text)
    
    def append_text(self, text, tag=None):
        """Add text to the console with optional tag"""
        self.console.configure(state="normal")
        
        if isinstance(text, tuple):
            text, tag = text
            
        if tag:
            self.console.insert("end", text, tag)
        else:
            self.console.insert("end", text)
            
        self.console.see("end")
        self.console.configure(state="disabled")
    
    def send_command(self):
        """Send the current command to the serial device"""
        command = self.input_entry.get()
        if not command:
            return
            
        if not self.running or not self.serial_device:
            self.append_text("Not connected to any device.\n", "err")
            return
            
        # Add to command history
        self.command_history.append(command)
        self.history_position = len(self.command_history)
            
        try:
            import serial
            # Open serial port briefly to send command
            with serial.Serial(self.serial_device, 115200, timeout=1) as ser:
                # Add newline if not present
                if not command.endswith('\n'):
                    command += '\n'
                    
                # Show command in console
                self.append_text(f"$ {command}", "cmd")
                
                # Send command
                ser.write(command.encode('utf-8'))
                
            # Clear input field
            self.input_entry.delete(0, 'end')
            
        except Exception as e:
            self.append_text(f"Error sending command: {str(e)}\n", "err")
    
    def clear(self):
        """Clear the console"""
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")
        
        # Add a system message indicating console was cleared
        self.append_text("Console cleared.\n", "sys")

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
                capture_output=True, text=True
                # Removed timeout parameter
            )
            if result.returncode == 0:
                file_path = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # Try kdialog next
            try:
                filter_str = 'All Files (*)' if not file_filter else file_filter
                result = subprocess.run(
                    ['kdialog', '--getopenfilename', last_dir, filter_str],
                    capture_output=True, text=True
                    # Removed timeout parameter
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
                capture_output=True, text=True
                # Removed timeout parameter
            )
            if result.returncode == 0:
                directory = result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            # Try kdialog next
            try:
                result = subprocess.run(
                    ['kdialog', '--getexistingdirectory', last_dir, title],
                    capture_output=True, text=True
                    # Removed timeout parameter
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
        """Initialize the application with a left-protruding console"""
        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Create main window with specific class name
        self.root = ctk.CTk(className="PlatypusApp")
        self.root.title("Platypus BMC Management")
        self.root.geometry("1100x800")  # Wider window to accommodate side-by-side layout
        
        # Initialize variables
        self._init_variables()
        
        # Create configuration directory
        self.config_dir = os.path.expanduser("~/.local/platypus")
        os.makedirs(self.config_dir, exist_ok=True)
        self.CONFIG_FILE = os.path.join(self.config_dir, "platypus_config.json")

        # Try to set icon (optional)
        try:
            icon_path = os.path.join(self.config_dir, "platypus_icon.png")
            if os.path.exists(icon_path):
                img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, img)
        except Exception:
            pass  # Continue without icon if there's an error

        # Load saved configuration
        self.load_config()

        # Create UI with embedded console on the left
        self.create_ui_with_embedded_console()
        
        # Do an initial refresh of networks and devices
        self.update_ip_dropdown()
        self.refresh_devices()

        # Bind close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Add resize handler to maintain proper proportions
        self.root.bind("<Configure>", self.on_window_resize)


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
        """Handle application closing"""
        # Disconnect console if connected
        if hasattr(self, 'embedded_console'):
            self.embedded_console.disconnect()
        
        # Force close any servers before exiting
        self.log_message("Closing application. Cleaning up servers...")
        self.force_close_port_80()
        
        # Save configuration
        self.save_config()
        
        # Destroy the main window
        self.root.destroy()

    def create_connection_section(self):
        """Create the connection settings section with optimized spacing"""
        section = ctk.CTkFrame(self.controls_frame)  # Changed from self.main_frame to self.controls_frame
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
        """Create the BMC operations section with hyperlink to Web UI"""
        section = ctk.CTkFrame(self.controls_frame)
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

    def create_flashing_operations_section(self):
        """Create the flashing operations section with optimized spacing"""
        section = ctk.CTkFrame(self.controls_frame)  # Changed from self.main_frame to self.controls_frame
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="Flashing Operations", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        op_frame = ctk.CTkFrame(section)
        op_frame.pack(fill="x", padx=10)
        
        ops = [
            ("Flash FIP (U-Boot)", self.flash_u_boot),
            ("Flash eMMC", self.flash_emmc),
            ("Reset BMC", self.reset_bmc),
            ("Flash FRU (EEPROM)", self.flash_eeprom),
            ("Flash All", self.on_flash_all),
            ("Reboot to Bootloader", self.reboot_to_bootloader)
        ]
        
        for i, (text, command) in enumerate(ops):
            row, col = divmod(i, 3)
            ctk.CTkButton(op_frame, text=text, command=command, height=28).grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        
        op_frame.grid_columnconfigure((0,1,2), weight=1)

    def create_log_section(self):
        """Create the log section with reduced height"""
        section = ctk.CTkFrame(self.controls_frame)  # Changed from self.main_frame to self.controls_frame
        section.pack(fill="x", pady=5)
        
        ctk.CTkLabel(section, text="Log", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        self.log_box = ctk.CTkTextbox(section, height=150, state="disabled")  # Reduced height from 200
        self.log_box.pack(padx=10, pady=5, fill="x")

    def create_progress_section(self):
        """Create the progress section without the standalone console button"""
        section = ctk.CTkFrame(self.controls_frame)  # Changed from self.main_frame to self.controls_frame
        section.pack(fill="x", pady=5)
        
        progress_frame = ctk.CTkFrame(section)
        progress_frame.pack(fill="x", padx=10, pady=5)
        
        # Progress bar takes full width
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
        """Open a minicom console for the selected serial device using terminator"""
        if not self.serial_device.get():
            self.log_message("No serial device selected. Please select a device.")
            return
        try:
            self.log_message(f"Launching Minicom on {self.serial_device.get()} using terminator...")
            subprocess.Popen(["terminator", "-e", f"minicom -D {self.serial_device.get()}"])
        except FileNotFoundError:
            self.log_message("Terminator or minicom not found. Please ensure they are installed.")
            # Try fallback to xterm if terminator is not available
            try:
                self.log_message("Attempting fallback to xterm...")
                subprocess.Popen(["xterm", "-e", f"minicom -D {self.serial_device.get()}"])
            except FileNotFoundError:
                self.log_message("Both terminator and xterm are not available. Please install one of them.")
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
            
            # Enable the Web UI button when IP is being set
            if hasattr(self, 'open_web_ui_button'):
                self.open_web_ui_button.configure(state="normal")

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

    def reboot_to_bootloader(self):
        """Reboot the OpenBMC to bootloader (U-Boot)"""
        required = {"Serial Device": self.serial_device.get()}
        if self._run_operation(
            self.run_reboot_to_bootloader,
            required_fields=required,
            error_msg="Please select a serial device before attempting to reboot to bootloader"
        ):
            self.log_message("Sending reboot to U-Boot command...")

    def run_reboot_to_bootloader(self):
        """Run reboot to U-Boot bootloader operation for OpenBMC"""
        try:
            # Call the OpenBMC-specific reset to U-Boot function
            asyncio.run(bmc.reset_to_uboot(self.log_message, self.serial_device.get()))
            
            # Inform user about U-Boot interaction
            self.log_message("System should now be at the U-Boot prompt")
            self.log_message("TIP: Use the Console button to interact with U-Boot if needed")
                
        except Exception as e:
            self.log_message(f"Error rebooting to bootloader: {e}")
        finally:
            self.lock_buttons = False

    def create_ui_with_embedded_console(self):
        """Create UI with console protruding on the left side"""
        # Main container frame
        self.main_container = ctk.CTkFrame(self.root)
        self.main_container.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Create a horizontal layout with two columns
        self.main_container.columnconfigure(0, weight=1)  # Console column
        self.main_container.columnconfigure(1, weight=3)  # Controls column
        
        # Create left frame for console that protrudes (taller than the controls)
        self.console_frame = ctk.CTkFrame(self.main_container, fg_color="#1a1a1a")  # Darker background
        self.console_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        # Create right frame for controls
        self.controls_frame = ctk.CTkFrame(self.main_container)
        self.controls_frame.grid(row=0, column=1, sticky="nsew")
        
        # Create UI sections in the controls frame
        self.create_connection_section()
        self.create_bmc_operations_section()
        self.create_flashing_operations_section()
        self.create_log_section()
        self.create_progress_section()
        
        # Create embedded console that protrudes on the left
        self.create_embedded_console()

    def create_embedded_console(self):
        """Create the embedded console section on the left side"""
        # Create title frame for console
        console_title_frame = ctk.CTkFrame(self.console_frame)
        console_title_frame.pack(fill="x", pady=(5, 0))
        
        ctk.CTkLabel(console_title_frame, text="Serial Console", 
                    font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", pady=5, padx=5)
        
        # Add control buttons
        self.console_buttons_frame = ctk.CTkFrame(console_title_frame)
        self.console_buttons_frame.pack(side="right", fill="y")
        
        # Connect button - will connect to currently selected serial device
        self.connect_button = ctk.CTkButton(self.console_buttons_frame, text="Connect", 
                                        command=self.connect_console, width=70, height=24)
        self.connect_button.pack(side="left", padx=2)
        
        # Disconnect button
        self.disconnect_button = ctk.CTkButton(self.console_buttons_frame, text="Disconnect", 
                                            command=self.disconnect_console, width=70, height=24)
        self.disconnect_button.pack(side="left", padx=2)
        
        # Clear button
        self.clear_button = ctk.CTkButton(self.console_buttons_frame, text="Clear", 
                                        command=self.clear_console, width=50, height=24)
        self.clear_button.pack(side="left", padx=2)
        
        # Create the embedded console widget with custom styling
        self.embedded_console = EmbeddedConsole(self.console_frame, fg_color="#1a1a1a", 
                                            border_width=1, border_color="#444444")
        self.embedded_console.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Auto-connect to the selected device when it's available
        self.root.after(500, self.auto_connect_console)


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
        """Open the BMC web UI using a more robust browser detection approach"""
        if not self.bmc_ip.get():
            self.log_message("Error: BMC IP is not set. Please set the IP first.")
            return
        
        # Format the URL with https prefix
        bmc_url = f"https://{self.bmc_ip.get()}"
        self.log_message(f"Opening BMC Web UI at {bmc_url}")
        
        # Try multiple browser opening methods
        try:
            # First try direct command approach
            self.open_url_with_browser(bmc_url)
        except Exception as e:
            self.log_message(f"Error opening Web UI: {e}")
            self.log_message("Please open the URL manually in your browser.")

    def open_url_with_browser(self, url):
        """Try multiple methods to open a URL in a browser"""
        import subprocess
        import os
        import webbrowser
        
        # List of browsers to try directly (in order of preference)
        browsers = [
            ["firefox", url],
            ["google-chrome", url],
            ["chromium-browser", url],
            ["chromium", url],
            ["brave-browser", url],
            ["opera", url],
            ["microsoft-edge", url],
            ["firefox-esr", url]
        ]
        
        # Try each browser directly
        for browser_cmd in browsers:
            try:
                self.log_message(f"Trying to open with {browser_cmd[0]}...")
                process = subprocess.Popen(
                    browser_cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE
                )
                # Wait briefly to see if the process starts without error
                returncode = process.poll()
                if returncode is None or returncode == 0:
                    self.log_message(f"Successfully opened URL with {browser_cmd[0]}")
                    return True
            except FileNotFoundError:
                # Browser not found, continue to next one
                pass
            except Exception as e:
                self.log_message(f"Error with {browser_cmd[0]}: {e}")
        
        # If direct browser commands fail, try Python's webbrowser module
        self.log_message("Trying Python's webbrowser module...")
        try:
            opened = webbrowser.open(url)
            if opened:
                self.log_message("URL opened using Python webbrowser module")
                return True
        except Exception as e:
            self.log_message(f"Python webbrowser error: {e}")
        
        # As a last resort, try xdg-open with explicit error redirection
        self.log_message("Trying xdg-open directly...")
        try:
            with open(os.devnull, 'w') as devnull:
                subprocess.Popen(
                    ["xdg-open", url],
                    stdout=devnull,
                    stderr=devnull,
                    start_new_session=True  # Detach from parent process
                )
            self.log_message("Command sent to xdg-open (success unknown)")
            return True
        except Exception as e:
            self.log_message(f"xdg-open error: {e}")
        
        # If we get here, all methods failed
        self.log_message(f"Could not open browser. Please navigate manually to: {url}")
        self.copy_to_clipboard(url)
        self.log_message("URL has been copied to clipboard for convenience")
        return False

    def copy_to_clipboard(self, text):
        """Copy text to clipboard using various methods"""
        try:
            # First try using tkinter's clipboard
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            return
        except Exception:
            pass
        
        # Try with xclip
        try:
            import subprocess
            subprocess.run(['xclip', '-selection', 'clipboard'], input=text.encode('utf-8'), check=False)
            return
        except Exception:
            pass
        
        # Try with xsel
        try:
            import subprocess
            subprocess.run(['xsel', '--clipboard'], input=text.encode('utf-8'), check=False)
            return
        except Exception:
            pass

    def run_set_bmc_ip(self):
        """Run set BMC IP operation with Web UI hyperlink update"""
        try:
            asyncio.run(set_ip(
                self.bmc_ip.get(), 
                self.update_progress, 
                self.log_message, 
                self.serial_device.get()
            ))
            
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