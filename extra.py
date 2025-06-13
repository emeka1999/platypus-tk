"""
Optimized Multi-Unit NanoBMC Flash Manager
Streamlined for efficiency and maintainability
"""

import asyncio
import threading
import time
import os
import glob
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
from dataclasses import dataclass

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import serial
import psutil

from utils import cleanup_all_serial_connections, read_serial_data
from network import *
from bmc import *


@dataclass
class UnitConfig:
    """Unit configuration data"""
    device: str = ""
    username: str = "root"  # Default values
    password: str = "0penBmc"  # Default values
    bmc_ip: str = ""
    host_ip: str = ""
    
    def is_valid(self) -> bool:
        # Only require device, bmc_ip, and host_ip (username/password have defaults)
        return bool(self.device and self.bmc_ip and self.host_ip)

class MultiUnitFlashWindow(ctk.CTkToplevel):
    """Streamlined multi-unit flash manager"""

    def __init__(self, parent, app_instance):
        super().__init__(parent)
        self.parent = parent
        self.app_instance = app_instance
        self.title("Multi-Unit NanoBMC Flash Manager")
        self.geometry("920x900")
        
        # Check BMC type
        if hasattr(app_instance, 'bmc_type') and app_instance.bmc_type.get() != 2:
            messagebox.showerror("Error", "Multi-unit flashing only for NanoBMC!")
            self.destroy()
            return
        
        # Initialize state
        self.units = []
        self.operation_running = False
        self.flash_threads = {}
        self.unit_progress = {}
        self.available_devices = []
        
        # Shared server management
        self.shared_servers = {}  # {host_ip: {'server': server_obj, 'usage_count': int}}
        self.server_lock = threading.RLock()
        
        # File paths
        self.firmware_folder = ctk.StringVar()
        self.fip_file = ctk.StringVar()
        self.eeprom_file = ctk.StringVar()
        
        # Config file
        self.config_file = os.path.expanduser("~/.nanobmc_multiflash_config.json")
        
        # Setup UI and load config
        self.create_ui()
        self.load_config()
        self.refresh_devices()
        
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_ui(self):
        """Create streamlined UI"""
        main_frame = ctk.CTkScrollableFrame(self)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Title
        ctk.CTkLabel(main_frame, text="Multi-Unit NanoBMC Flash Manager", 
                    font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        
        # File selection
        self._create_file_section(main_frame)
        
        # Unit management
        self._create_unit_section(main_frame)
        
        # Controls
        self._create_control_section(main_frame)
        
        # Progress and logs
        self._create_progress_section(main_frame)

    def _create_file_section(self, parent):
        """Create file selection section"""
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", pady=5)
        
        ctk.CTkLabel(frame, text="Flash Files", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        # Helper function for file selection rows
        def file_row(label, var, browse_cmd):
            row = ctk.CTkFrame(frame)
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(row, text=label, width=120).pack(side="left")
            ctk.CTkEntry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=5)
            ctk.CTkButton(row, text="Browse", command=browse_cmd, width=70).pack(side="right")
        
        file_row("Firmware Folder:", self.firmware_folder, self.select_firmware_folder)
        file_row("FIP File:", self.fip_file, self.select_fip_file)
        file_row("EEPROM File:", self.eeprom_file, self.select_eeprom_file)

    def _create_unit_section(self, parent):
        """Create unit management section"""
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", pady=5)
        
        # Header
        header = ctk.CTkFrame(frame)
        header.pack(fill="x", pady=5)
        ctk.CTkLabel(header, text="Units", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="Refresh", command=self.refresh_devices, width=80).pack(side="right", padx=5)
        ctk.CTkButton(header, text="Add Unit", command=self.add_unit, width=80).pack(side="right")
        
        # Units container - INCREASED HEIGHT
        self.units_container = ctk.CTkScrollableFrame(frame, height=300)  # Changed from 200 to 300
        self.units_container.pack(fill="both", expand=True, padx=10, pady=5)

    def _create_control_section(self, parent):
        """Create control buttons"""
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", pady=5)
        
        button_frame = ctk.CTkFrame(frame)
        button_frame.pack(pady=10)
        
        self.start_button = ctk.CTkButton(button_frame, text="Start Multi-Flash", 
                                         command=self.start_multi_flash, 
                                         height=35, width=140)
        self.start_button.pack(side="left", padx=5)
        
        
        ctk.CTkButton(button_frame, text="Multi-Console", command=self.open_console,
                     height=35, width=120).pack(side="left", padx=5)

    def _create_progress_section(self, parent):
        """Create progress and log section"""
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="both", expand=True, pady=5)
        
        ctk.CTkLabel(frame, text="Progress", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=5)
        
        # Overall progress
        self.overall_progress = ctk.CTkProgressBar(frame)
        self.overall_progress.pack(fill="x", padx=10, pady=5)
        self.overall_progress.set(0)
        
        # Log
        self.log_text = ctk.CTkTextbox(frame, height=120)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=5)

    def load_config(self):
        """Load configuration from file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                
                self.firmware_folder.set(config.get('firmware_folder', ''))
                self.fip_file.set(config.get('fip_file', ''))
                self.eeprom_file.set(config.get('eeprom_file', ''))
                
                # Load units
                for unit_data in config.get('units', []):
                    self.add_unit(unit_data)
                    
                self.log("Configuration loaded")
        except Exception as e:
            self.log(f"Config load error: {e}")

    def save_config(self):
        """Save configuration to file"""
        try:
            # Update configs from UI before saving
            for unit in self.units:
                unit['config'].device = unit['device_var'].get()
                unit['config'].username = unit['username_var'].get()
                unit['config'].password = unit['password_var'].get()
                unit['config'].bmc_ip = unit['bmc_ip_var'].get()
                unit['config'].host_ip = unit['host_ip_var'].get()
            
            config = {
                'firmware_folder': self.firmware_folder.get(),
                'fip_file': self.fip_file.get(),
                'eeprom_file': self.eeprom_file.get(),
                'units': [
                    {
                        'device': unit['config'].device,
                        'username': unit['config'].username,
                        'password': unit['config'].password,
                        'bmc_ip': unit['config'].bmc_ip,
                        'host_ip': unit['config'].host_ip
                    }
                    for unit in self.units
                ]
            }
            
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
                
            self.log(f"Configuration saved with {len(self.units)} units")
        except Exception as e:
            self.log(f"Config save error: {e}")

    def select_firmware_folder(self):
        """Select firmware folder"""
        from main import FileSelectionHelper
        folder = FileSelectionHelper.select_directory(self, "Select Firmware Folder", 
                                                     self.firmware_folder.get() or os.path.expanduser("~"))
        if folder:
            self.firmware_folder.set(folder)
            self.save_config()

    def select_fip_file(self):
        """Select FIP file with validation"""
        from main import FileSelectionHelper
        file_path = FileSelectionHelper.select_file(self, "Select FIP File", 
                                                   os.path.dirname(self.fip_file.get()) or os.path.expanduser("~"),
                                                   "FIP files (fip-snuc-nanobmc.bin) | fip-snuc-nanobmc.bin")
        if file_path:
            if os.path.basename(file_path) != "fip-snuc-nanobmc.bin":
                messagebox.showerror("Invalid File", "Must select 'fip-snuc-nanobmc.bin'")
                return
            self.fip_file.set(file_path)
            self.save_config()

    def select_eeprom_file(self):
        """Select EEPROM file with validation"""
        from main import FileSelectionHelper
        file_path = FileSelectionHelper.select_file(self, "Select EEPROM File", 
                                                   os.path.dirname(self.eeprom_file.get()) or os.path.expanduser("~"),
                                                   "FRU files (fru.bin) | fru.bin")
        if file_path:
            if os.path.basename(file_path) != "fru.bin":
                messagebox.showerror("Invalid File", "Must select 'fru.bin'")
                return
            self.eeprom_file.set(file_path)
            self.save_config()

    def refresh_devices(self):
        """Refresh available serial devices"""
        self.available_devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        self.log(f"Found {len(self.available_devices)} devices")
        
        # Update dropdowns
        for unit in self.units:
            unit['device_dropdown'].configure(values=self.available_devices)

    def get_network_interfaces(self):
        """Get list of network interfaces (copied from main.py)"""
        # Import the method from main app
        if hasattr(self.app_instance, 'get_network_interfaces'):
            return self.app_instance.get_network_interfaces()
        else:
            # Fallback to simple detection
            try:
                import subprocess
                result = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    import re
                    ips = []
                    for line in result.stdout.splitlines():
                        ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)/\d+.*scope\s+global', line)
                        if ip_match:
                            ip = ip_match.group(1)
                            if ip != "127.0.0.1":
                                ips.append(ip)
                    return ips
            except:
                pass
            return ["127.0.0.1"]

    def add_unit(self, unit_data=None):
        """Add new unit configuration"""
        unit_id = len(self.units) + 1
        
        unit_frame = ctk.CTkFrame(self.units_container)
        unit_frame.pack(fill="x", pady=3)
        
        # Create config
        config = UnitConfig()
        if unit_data:
            config.device = unit_data.get('device', '')
            config.username = unit_data.get('username', '')
            config.password = unit_data.get('password', '')
            config.bmc_ip = unit_data.get('bmc_ip', '')
            config.host_ip = unit_data.get('host_ip', '')
        else:
            # Leave everything empty for user to fill
            config.username = ''
            config.password = ''
            config.bmc_ip = ''
            config.host_ip = ''
        
        # Create UI elements
        header = ctk.CTkFrame(unit_frame)
        header.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkLabel(header, text=f"Unit {unit_id}", font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="Remove", command=lambda: self.remove_unit(unit_id-1), 
                    width=60, height=25).pack(side="right")
        
        # Labels row
        labels_frame = ctk.CTkFrame(unit_frame)
        labels_frame.pack(fill="x", padx=5, pady=(0,2))
        
        ctk.CTkLabel(labels_frame, text="Device", width=120, font=ctk.CTkFont(size=10)).grid(row=0, column=0, padx=2, pady=1)
        ctk.CTkLabel(labels_frame, text="Username", width=120, font=ctk.CTkFont(size=10)).grid(row=0, column=1, padx=2, pady=1)
        ctk.CTkLabel(labels_frame, text="Password", width=120, font=ctk.CTkFont(size=10)).grid(row=0, column=2, padx=2, pady=1)
        ctk.CTkLabel(labels_frame, text="BMC IP", width=80, font=ctk.CTkFont(size=10)).grid(row=0, column=3, padx=2, pady=1)
        ctk.CTkLabel(labels_frame, text="Host IP", width=80, font=ctk.CTkFont(size=10)).grid(row=0, column=4, padx=2, pady=1)
        ctk.CTkLabel(labels_frame, text="Progress", width=120, font=ctk.CTkFont(size=10)).grid(row=0, column=5, padx=2, pady=1)
        ctk.CTkLabel(labels_frame, text="Status", width=120, font=ctk.CTkFont(size=10)).grid(row=0, column=6, padx=2, pady=1)
        
        # Config fields
        fields = ctk.CTkFrame(unit_frame)
        fields.pack(fill="x", padx=15, pady=2)
        
        # Device
        device_var = ctk.StringVar(value=config.device)
        device_dropdown = ctk.CTkComboBox(fields, variable=device_var, values=self.available_devices, width=120)
        device_dropdown.grid(row=0, column=0, padx=8, pady=1)
        
        # Username and Password
        username_var = ctk.StringVar(value=config.username)
        password_var = ctk.StringVar(value=config.password)
        ctk.CTkEntry(fields, textvariable=username_var, placeholder_text="Username", width=120).grid(row=0, column=1, padx=2, pady=1)
        ctk.CTkEntry(fields, textvariable=password_var, placeholder_text="Password", show="*", width=80).grid(row=0, column=2, padx=2, pady=1)
        
        # IPs
        bmc_ip_var = ctk.StringVar(value=config.bmc_ip)
        host_ip_var = ctk.StringVar(value=config.host_ip)
        ctk.CTkEntry(fields, textvariable=bmc_ip_var, placeholder_text="BMC IP", width=80).grid(row=0, column=3, padx=8, pady=1)
        
        # Host IP dropdown with network interfaces
        host_ip_dropdown = ctk.CTkComboBox(fields, variable=host_ip_var, values=self.get_network_interfaces(), width=120)
        host_ip_dropdown.grid(row=0, column=4, padx=2, pady=1)
        
        # Progress
        progress_bar = ctk.CTkProgressBar(fields, width=120)
        progress_bar.grid(row=0, column=5, padx=2, pady=1)
        progress_bar.set(0)
        
        status_label = ctk.CTkLabel(fields, text="Ready", width=60)
        status_label.grid(row=0, column=6, padx=2, pady=1)
        
        # Store unit data
        unit = {
            'id': unit_id,
            'frame': unit_frame,
            'config': config,
            'device_var': device_var,
            'username_var': username_var,
            'password_var': password_var,
            'bmc_ip_var': bmc_ip_var,
            'host_ip_var': host_ip_var,
            'device_dropdown': device_dropdown,
            'host_ip_dropdown': host_ip_dropdown,
            'progress_bar': progress_bar,
            'status_label': status_label
        }
        
        self.units.append(unit)
        self.unit_progress[unit_id] = 0
        
        # Auto-save
        if not unit_data:
            self.save_config()

    def remove_unit(self, index):
        """Remove unit configuration and renumber remaining units"""
        if 0 <= index < len(self.units):
            # Remove the unit
            self.units[index]['frame'].destroy()
            removed_unit_id = self.units[index]['id']
            self.units.pop(index)
            
            # Remove from progress tracking
            if removed_unit_id in self.unit_progress:
                del self.unit_progress[removed_unit_id]
            
            # Renumber all remaining units and update their UI
            for i, unit in enumerate(self.units):
                new_id = i + 1
                old_id = unit['id']
                unit['id'] = new_id
                
                # Update the unit label in the UI
                header_frame = unit['frame'].winfo_children()[0]  # First child is the header
                label = header_frame.winfo_children()[0]  # First child of header is the label
                label.configure(text=f"Unit {new_id}")
                
                # Update the remove button command
                remove_button = header_frame.winfo_children()[1]  # Second child is remove button
                remove_button.configure(command=lambda idx=i: self.remove_unit(idx))
                
                # Update progress tracking
                if old_id in self.unit_progress:
                    self.unit_progress[new_id] = self.unit_progress[old_id]
                    if old_id != new_id:
                        del self.unit_progress[old_id]
            
            self.save_config()
            self.log(f"Unit removed. {len(self.units)} units remaining.")

    def get_shared_server(self, host_ip: str, directory: str):
        """Get or create a shared HTTP server for the host IP"""
        with self.server_lock:
            if host_ip in self.shared_servers:
                # Increment usage count for existing server
                self.shared_servers[host_ip]['usage_count'] += 1
                self.log(f"Reusing server for {host_ip} (usage: {self.shared_servers[host_ip]['usage_count']})")
                return self.shared_servers[host_ip]['server']
            
            # Create new server
            try:
                from network import start_server  # Fix: Use the correct import
                server = start_server(directory, 80, self.log)
                if server:
                    self.shared_servers[host_ip] = {
                        'server': server,
                        'usage_count': 1,
                        'directory': directory
                    }
                    self.log(f"Created new shared server for {host_ip}")
                    return server
                else:
                    raise Exception("Failed to start server")
            except Exception as e:
                self.log(f"Error creating server for {host_ip}: {e}")
                return None

    def release_shared_server(self, host_ip: str):
        """Release a shared server when unit is done"""
        with self.server_lock:
            if host_ip in self.shared_servers:
                self.shared_servers[host_ip]['usage_count'] -= 1
                remaining = self.shared_servers[host_ip]['usage_count']
                self.log(f"Released server for {host_ip} (remaining usage: {remaining})")
                
                # If no more units using this server, shut it down
                if remaining <= 0:
                    try:
                        from network import stop_server  # Fix: Use the correct import
                        stop_server(self.shared_servers[host_ip]['server'], self.log)
                        del self.shared_servers[host_ip]
                        self.log(f"Shut down shared server for {host_ip}")
                    except Exception as e:
                        self.log(f"Error shutting down server: {e}")

    def cleanup_all_servers(self):
        """Cleanup all shared servers"""
        with self.server_lock:
            for host_ip, server_info in list(self.shared_servers.items()):
                try:
                    from network import stop_server  # Fix: Use the correct import
                    stop_server(server_info['server'], self.log)
                    self.log(f"Cleaned up server for {host_ip}")
                except Exception as e:
                    self.log(f"Error cleaning server {host_ip}: {e}")
            self.shared_servers.clear()

    def validate_config(self) -> bool:
        """Validate configuration before starting"""
        # Check files
        if not all([self.firmware_folder.get(), self.fip_file.get(), self.eeprom_file.get()]):
            messagebox.showerror("Error", "Please select all required files")
            return False
            
        for path in [self.firmware_folder.get(), self.fip_file.get(), self.eeprom_file.get()]:
            if not os.path.exists(path):
                messagebox.showerror("Error", f"File not found: {path}")
                return False
        
        # Check units
        if not self.units:
            messagebox.showerror("Error", "Please add at least one unit")
            return False
            
        # Update configs and validate
        for unit in self.units:
            unit['config'].device = unit['device_var'].get()
            unit['config'].bmc_ip = unit['bmc_ip_var'].get()
            unit['config'].host_ip = unit['host_ip_var'].get()
            
            if not unit['config'].is_valid():
                messagebox.showerror("Error", f"Unit {unit['id']} has invalid configuration")
                return False
        
        # Check for duplicates
        devices = [unit['config'].device for unit in self.units]
        ips = [unit['config'].bmc_ip for unit in self.units]
        
        if len(devices) != len(set(devices)):
            messagebox.showerror("Error", "Duplicate devices found")
            return False
            
        if len(ips) != len(set(ips)):
            messagebox.showerror("Error", "Duplicate BMC IPs found")
            return False
        
        return True
        """Validate configuration before starting"""
        # Check files
        if not all([self.firmware_folder.get(), self.fip_file.get(), self.eeprom_file.get()]):
            messagebox.showerror("Error", "Please select all required files")
            return False
            
        for path in [self.firmware_folder.get(), self.fip_file.get(), self.eeprom_file.get()]:
            if not os.path.exists(path):
                messagebox.showerror("Error", f"File not found: {path}")
                return False
        
        # Check units
        if not self.units:
            messagebox.showerror("Error", "Please add at least one unit")
            return False
            
        # Update configs and validate
        for unit in self.units:
            unit['config'].device = unit['device_var'].get()
            unit['config'].bmc_ip = unit['bmc_ip_var'].get()
            unit['config'].host_ip = unit['host_ip_var'].get()
            
            if not unit['config'].is_valid():
                messagebox.showerror("Error", f"Unit {unit['id']} has invalid configuration")
                return False
        
        # Check for duplicates
        devices = [unit['config'].device for unit in self.units]
        ips = [unit['config'].bmc_ip for unit in self.units]
        
        if len(devices) != len(set(devices)):
            messagebox.showerror("Error", "Duplicate devices found")
            return False
            
        if len(ips) != len(set(ips)):
            messagebox.showerror("Error", "Duplicate BMC IPs found")
            return False
        
        return True

    def start_multi_flash(self):
        """Start multi-unit flashing"""
        if self.operation_running:
            return
            
        if not self.validate_config():
            return
            
        if not messagebox.askyesno("Confirm", 
                                  f"Flash {len(self.units)} devices?\n\n"
                                  "Ensure all are at U-Boot prompt."):
            return
        
        self.operation_running = True
        self.start_button.configure(state="disabled")
        
        # Reset progress
        self.overall_progress.set(0)
        for unit in self.units:
            unit['progress_bar'].set(0)
            unit['status_label'].configure(text="Starting...")
            
        self.log("=== MULTI-FLASH STARTED ===")
        
        # Group units by host IP for server optimization
        ip_groups = {}
        for unit in self.units:
            host_ip = unit['host_ip_var'].get()
            if host_ip not in ip_groups:
                ip_groups[host_ip] = []
            ip_groups[host_ip].append(unit)
        
        self.log(f"Server optimization: {len(ip_groups)} servers for {len(self.units)} units")
        for host_ip, units in ip_groups.items():
            unit_ids = [str(u['id']) for u in units]
            self.log(f"  {host_ip}: Units {', '.join(unit_ids)}")
        
        # Pre-create shared servers
        for host_ip in ip_groups.keys():
            server = self.get_shared_server(host_ip, self.firmware_folder.get())
            if not server:
                messagebox.showerror("Error", f"Failed to create server for {host_ip}")
                self.operation_running = False
                self.start_button.configure(state="normal")
                return
        
        # Start threads
        self.flash_threads = {}
        for unit in self.units:
            thread = threading.Thread(target=self.flash_unit, args=(unit,), daemon=True)
            self.flash_threads[unit['id']] = thread
            thread.start()
            time.sleep(0.5)  # Stagger starts
        
        # Monitor progress
        threading.Thread(target=self.monitor_progress, daemon=True).start()

    def flash_unit(self, unit):
        """Flash a single unit using shared servers"""
        unit_id = unit['id']
        config = unit['config']
        shared_server = None
        
        # Update config with current UI values BEFORE starting
        config.device = unit['device_var'].get()
        config.username = unit['username_var'].get()
        config.password = unit['password_var'].get()
        config.bmc_ip = unit['bmc_ip_var'].get()
        config.host_ip = unit['host_ip_var'].get()
        
        def update_progress(progress):
            if self.operation_running:  # Only update if still running
                self.unit_progress[unit_id] = progress
                unit['progress_bar'].set(progress)
        
        def update_status(status):
            if self.operation_running:  # Only update if still running
                unit['status_label'].configure(text=status)
        
        def unit_log(msg):
            self.log(f"[Unit {unit_id}] {msg}")
        
        try:
            if not self.operation_running:
                unit_log("Operation cancelled before start")
                return
            
            # Log the config values for debugging
            unit_log(f"Using config - Device: {config.device}, Username: {config.username}, BMC IP: {config.bmc_ip}")
            
            # Get shared server for this unit's host IP
            shared_server = self.get_shared_server(config.host_ip, self.firmware_folder.get())
            if not shared_server:
                raise Exception("Failed to get shared server")
            
            # Step 1: Flash eMMC using shared server
            if not self.operation_running:
                return
                
            update_status("Flash eMMC")
            unit_log("Starting eMMC flash")
            
            def emmc_progress(p):
                if self.operation_running:
                    update_progress(p * 0.2)  # 20% of total
            
            result = asyncio.run(self.flash_emmc_shared(
                config.bmc_ip, self.firmware_folder.get(), config.host_ip, 2,
                emmc_progress, unit_log, config.device, shared_server
            ))
            
            if not result or not self.operation_running:
                if not self.operation_running:
                    unit_log("Operation cancelled during eMMC flash")
                else:
                    raise Exception("eMMC flash failed")
                return
            
            # Step 2: Login
            if not self.operation_running:
                return
                
            update_status("Login")
            unit_log(f"Logging in with username: {config.username}")
            result = asyncio.run(self.login(config, unit_log))  # Changed from await to asyncio.run
            if not self.operation_running:
                return
            update_progress(0.4)
            
            # Step 3: Set IP
            if not self.operation_running:
                return
                
            update_status("Set IP")
            unit_log(f"Setting IP to {config.bmc_ip}")
            
            def ip_progress(p):
                if self.operation_running:
                    update_progress(0.4 + p * 0.2)
            
            asyncio.run(set_ip(config.bmc_ip, ip_progress, unit_log, config.device))
            
            # Step 4: Flash U-Boot using shared server
            if not self.operation_running:
                return
                
            update_status("Flash U-Boot")
            unit_log("Flashing U-Boot")
            
            def fip_progress(p):
                if self.operation_running:
                    update_progress(0.6 + p * 0.2)
            
            asyncio.run(self.flasher_shared(
                self.fip_file.get(), config.host_ip, 
                fip_progress, unit_log, config.device, shared_server
            ))
            
            # Step 5: Flash EEPROM using shared server
            if not self.operation_running:
                return
                
            update_status("Flash EEPROM")
            unit_log("Flashing EEPROM")
            
            def eeprom_progress(p):
                if self.operation_running:
                    update_progress(0.8 + p * 0.2)
            
            asyncio.run(self.flash_eeprom_shared(
                self.eeprom_file.get(), config.host_ip,
                eeprom_progress, unit_log, config.device, shared_server
            ))
            
            if self.operation_running:
                update_progress(1.0)
                update_status("Completed")
                unit_log("✅ Flash completed successfully")
            
        except Exception as e:
            if self.operation_running:
                update_status("Failed")
                unit_log(f"❌ Error: {e}")
            else:
                unit_log("Operation cancelled")
            update_progress(0)
        finally:
            # Release the shared server
            if shared_server:
                self.release_shared_server(config.host_ip)

    async def flash_emmc_shared(self, bmc_ip, directory, my_ip, dd_value, 
                               callback_progress, callback_output, serial_device, shared_server):
        """Flash eMMC using shared HTTP server"""
        if dd_value == 1:
            type_name = 'mos-bmc'
        else:
            type_name = 'nanobmc'

        ser = None
        try:
            callback_output("Using shared HTTP server for eMMC flash...")
            callback_progress(0.10)

            ser = serial.Serial(serial_device, 115200, timeout=0.1)

            # Setting IP Address (bootloader)
            callback_output("Setting IP Address (bootloader)...")
            command = f'setenv ipaddr {bmc_ip}\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 2)
            callback_output(response)
            callback_progress(0.20)

            # Grabbing virtual restore image
            callback_output("Grabbing virtual restore image...")
            command = f'wget ${{loadaddr}} {my_ip}:/obmc-rescue-image-snuc-{type_name}.itb; bootm\n'
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
            callback_output("Grabbing restore image...")
            command = f"curl -o obmc-phosphor-image-snuc-{type_name}.wic.xz {my_ip}/obmc-phosphor-image-snuc-{type_name}.wic.xz\n"
            response = await asyncio.to_thread(read_serial_data, ser, command, 2)
            callback_output(response)
            callback_progress(0.60)

            # Grabbing the mapping file
            callback_output("Grabbing the mapping file...")
            command = f'curl -o obmc-phosphor-image-snuc-{type_name}.wic.bmap {my_ip}/obmc-phosphor-image-snuc-{type_name}.wic.bmap\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 5)
            callback_output(response)
            callback_progress(0.90)

            # Flashing the restore image
            callback_output("Flashing the restore image...")
            command = f'bmaptool copy obmc-phosphor-image-snuc-{type_name}.wic.xz /dev/mmcblk0\n'
            response = await asyncio.to_thread(read_serial_data, ser, command, 5)
            callback_output(response)

            await asyncio.sleep(55)
            callback_output("Factory Reset Complete. Rebooting...")
            ser.write(b'reboot\n')
            callback_progress(1.00)
            await asyncio.sleep(60)
            
            return True

        except Exception as e:
            callback_output(f"Error during eMMC flash: {e}")
            return False
        finally:
            if ser and ser.is_open:
                try:
                    ser.close()
                except:
                    pass

    async def flasher_shared(self, flash_file, my_ip, callback_progress, 
                            callback_output, serial_device, shared_server):
        """Flash U-Boot using shared HTTP server"""
        file_name = os.path.basename(flash_file)
        port = 80

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

            # Remove file
            remove_command = f"rm -f {file_name}\n"
            ser.write(remove_command.encode('utf-8'))
            await asyncio.sleep(2)
            callback_output(f"{file_name} removed successfully.")
        finally:
            if ser and ser.is_open:
                ser.close()

    async def flash_eeprom_shared(self, flash_file, my_ip, callback_progress, 
                                 callback_output, serial_device, shared_server):
        """Flash EEPROM using shared HTTP server"""
        file_name = os.path.basename(flash_file)
        port = 80

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
            
        except Exception as e:
            callback_output(f"EEPROM Flash Error: {e}")
        finally:
            if ser and ser.is_open:
                ser.close()
 

    def monitor_progress(self):
        """Monitor overall progress"""
        while self.operation_running:
            active_threads = [t for t in self.flash_threads.values() if t.is_alive()]
            
            if not active_threads:
                # All done
                self.operation_running = False
                self.start_button.configure(state="normal")
                
                total_progress = sum(self.unit_progress.values())
                avg_progress = total_progress / len(self.units) if self.units else 0
                self.overall_progress.set(avg_progress)
                
                if avg_progress >= 1.0:
                    self.log("🎉 ALL UNITS COMPLETED!")
                else:
                    self.log("⚠️ Completed with errors")
                break
            else:
                # Update overall progress
                total_progress = sum(self.unit_progress.values())
                avg_progress = total_progress / len(self.units) if self.units else 0
                self.overall_progress.set(avg_progress)
            
            time.sleep(1)

    def stop_all(self):
        """Stop all operations"""
        if not self.operation_running:
            return
            
        if messagebox.askyesno("Confirm", "Stop all operations?"):
            self.log("🛑 STOPPING ALL OPERATIONS")
            self.operation_running = False
            
            for unit in self.units:
                unit['status_label'].configure(text="Stopped")
            
            # Cleanup all shared servers
            self.cleanup_all_servers()
            
            self.start_button.configure(state="normal")
            
            # Clean up serial connections without error messages
            try:
                cleanup_all_serial_connections()
            except:
                pass  # Ignore any errors during cleanup

    def open_console(self):
        """Open multi-console with persistent configuration"""
        units_with_devices = [u for u in self.units if u['device_var'].get()]
        if not units_with_devices:
            messagebox.showwarning("No Devices", "No units have devices selected")
            return
        
        try:
            self.log(f"Opening console for {len(units_with_devices)} units")
            
            # List the devices for debugging
            for unit in units_with_devices:
                device = unit['device_var'].get()
                self.log(f"  Unit {unit['id']}: {device}")
            
            # Clean up any existing console processes first
            self.cleanup_console_processes()
            
            # Try different console methods in order of preference
            console_opened = False
            
            # Method 1: Try Terminator with persistent config
            self.log("Trying Terminator...")
            console_opened = self.try_terminator_console(units_with_devices)
            
            if console_opened:
                self.log("Terminator multi-console opened successfully")
                return  # Don't try other methods if Terminator worked
            
            # Method 2: Try tmux as fallback
            self.log("Terminator failed, trying tmux...")
            console_opened = self.try_tmux_console(units_with_devices)
            
            if console_opened:
                self.log("tmux multi-console opened successfully")
                return
            
            # Method 3: Try screen as fallback
            self.log("tmux failed, trying screen...")
            console_opened = self.try_screen_console(units_with_devices)
            
            if console_opened:
                self.log("screen multi-console opened successfully")
                return
            
            # Method 4: Individual xterm windows as last resort
            self.log("All multiplexers failed, opening individual consoles...")
            self.open_individual_consoles(units_with_devices)
            
        except Exception as e:
            self.log(f"Console error: {e}")
            # Emergency fallback
            self.open_individual_consoles(units_with_devices)

    def try_terminator_console(self, units_with_devices):
        """Try to open Terminator with persistent config"""
        try:
            # If more than 4 units, fall back to individual windows
            if len(units_with_devices) > 4:
                self.log("Too many units for terminator multi-pane, falling back to individual windows")
                return False
            
            # First test if terminator works at all
            test_result = subprocess.run(['terminator', '--version'], 
                                    capture_output=True, timeout=3)
            if test_result.returncode != 0:
                self.log("Terminator version check failed")
                return False
            
            # Create persistent config directory
            config_dir = os.path.expanduser("~/.config/terminator")
            os.makedirs(config_dir, exist_ok=True)
            
            # Create persistent config file with unique name
            import time
            config_file = os.path.join(config_dir, f"nanobmc_multiflash_{int(time.time())}")
            config_content = self.generate_terminator_config(units_with_devices)
            
            # If config generation failed (too many units), fall back
            if config_content is None:
                return False
            
            # Debug: log the config content
            self.log(f"Generated config file: {config_file}")
            
            with open(config_file, 'w') as f:
                f.write(config_content)
            
            # Test the config first
            test_cmd = ["terminator", "--config", config_file, "--help"]
            test_result = subprocess.run(test_cmd, capture_output=True, timeout=5)
            
            if test_result.returncode != 0:
                self.log(f"Config test failed: {test_result.stderr.decode()}")
                return False
            
            # Launch terminator
            cmd = [
                "terminator", 
                "--config", config_file, 
                "--layout", "multi_unit",
                "--title", "NanoBMC Multi-Console"
            ]
            
            self.log(f"Launching: {' '.join(cmd)}")
            
            # Launch and detach
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid  # Create new session
            )
            
            # Store for cleanup
            if not hasattr(self, 'console_processes'):
                self.console_processes = []
            self.console_processes.append(process)
            
            # Clean up config file after a delay
            def cleanup_config():
                time.sleep(10)
                try:
                    os.remove(config_file)
                except:
                    pass
            threading.Thread(target=cleanup_config, daemon=True).start()
            
            self.log("Terminator launched")
            return True
            
        except subprocess.TimeoutExpired:
            self.log("Terminator launch timed out")
            return False
        except FileNotFoundError:
            self.log("Terminator not found")
            return False
        except Exception as e:
            self.log(f"Terminator error: {e}")
            return False
    
    def try_tmux_console(self, units_with_devices):
        """Try to open tmux session with multiple panes"""
        try:
            session_name = "nanobmc_multiflash"
            
            # Kill existing session if it exists
            subprocess.run(["tmux", "kill-session", "-t", session_name], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Create new tmux session with first unit
            first_unit = units_with_devices[0]
            subprocess.run([
                "tmux", "new-session", "-d", "-s", session_name,
                "-c", os.getcwd(),
                f"minicom -D {first_unit['device_var'].get()}"
            ])
            
            # Add additional panes for other units
            for i, unit in enumerate(units_with_devices[1:], 1):
                device = unit['device_var'].get()
                if i % 2 == 1:  # Split horizontally
                    subprocess.run(["tmux", "split-window", "-h", "-t", session_name, 
                                  f"minicom -D {device}"])
                else:  # Split vertically
                    subprocess.run(["tmux", "split-window", "-v", "-t", session_name, 
                                  f"minicom -D {device}"])
            
            # Balance the panes
            subprocess.run(["tmux", "select-layout", "-t", session_name, "tiled"])
            
            # Attach to session in new terminal
            subprocess.Popen([
                "x-terminal-emulator", "-e", 
                f"tmux attach-session -t {session_name}"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            self.log(f"tmux multi-console launched (session: {session_name})")
            return True
            
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            self.log(f"tmux not available: {e}")
            return False
        except Exception as e:
            self.log(f"tmux launch failed: {e}")
            return False

    def try_screen_console(self, units_with_devices):
        """Try to open GNU screen session with multiple windows"""
        try:
            session_name = "nanobmc_multiflash"
            
            # Kill existing session if it exists  
            subprocess.run(["screen", "-S", session_name, "-X", "quit"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Create screen session with first unit
            first_unit = units_with_devices[0]
            subprocess.run([
                "screen", "-dmS", session_name,
                "minicom", "-D", first_unit['device_var'].get()
            ])
            
            # Add windows for other units
            for i, unit in enumerate(units_with_devices[1:], 1):
                device = unit['device_var'].get()
                subprocess.run([
                    "screen", "-S", session_name, "-X", "screen", 
                    "minicom", "-D", device
                ])
            
            # Attach to session in new terminal
            subprocess.Popen([
                "x-terminal-emulator", "-e", 
                f"screen -r {session_name}"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            self.log(f"GNU screen multi-console launched (session: {session_name})")
            return True
            
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            self.log(f"screen not available: {e}")
            return False
        except Exception as e:
            self.log(f"screen launch failed: {e}")
            return False

    def open_individual_consoles(self, units_with_devices):
        """Open individual console windows as fallback"""
        self.log("Opening individual console windows...")
        
        if not hasattr(self, 'console_processes'):
            self.console_processes = []
        
        for unit in units_with_devices:
            device = unit['device_var'].get()
            unit_id = unit['id']
            bmc_ip = unit['bmc_ip_var'].get() or "No IP"
            
            try:
                # Try different terminal emulators
                terminal_commands = [
                    ["x-terminal-emulator", "-T", f"Unit {unit_id} - {device} - {bmc_ip}", 
                     "-e", f"minicom -D {device}"],
                    ["gnome-terminal", "--title", f"Unit {unit_id} - {device} - {bmc_ip}", 
                     "--", "minicom", "-D", device],
                    ["xterm", "-T", f"Unit {unit_id} - {device} - {bmc_ip}", 
                     "-e", f"minicom -D {device}"],
                    ["konsole", "--title", f"Unit {unit_id} - {device} - {bmc_ip}", 
                     "-e", f"minicom -D {device}"]
                ]
                
                process_started = False
                for cmd in terminal_commands:
                    try:
                        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self.console_processes.append(process)
                        self.log(f"Console opened for Unit {unit_id} on {device} (PID: {process.pid})")
                        process_started = True
                        break
                    except (FileNotFoundError, subprocess.SubprocessError):
                        continue
                
                if not process_started:
                    self.log(f"Failed to open console for Unit {unit_id} - no suitable terminal found")
                    
            except Exception as e:
                self.log(f"Failed to open console for Unit {unit_id}: {e}")

    def cleanup_console_processes(self):
        """Clean up console processes and close ALL minicom processes"""
        try:
            self.log("Cleaning up console processes and closing ALL minicom instances...")
            
            # Kill ALL minicom processes (not just our devices)
            try:
                import psutil
                minicom_pids = []
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] == 'minicom':
                            self.log(f"Terminating minicom process PID {proc.info['pid']}")
                            minicom_pids.append(proc.info['pid'])
                            proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                
                # Wait for minicom processes to terminate
                import time
                time.sleep(1)
                
                # Force kill any that didn't terminate
                for pid in minicom_pids:
                    try:
                        proc = psutil.Process(pid)
                        if proc.is_running():
                            self.log(f"Force killing minicom PID {pid}")
                            proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                        
                if minicom_pids:
                    self.log(f"Closed {len(minicom_pids)} minicom processes")
                            
            except ImportError:
                # Fallback if psutil not available - kill all minicom processes
                try:
                    subprocess.run(["pkill", "minicom"], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.log("Closed all minicom processes (pkill method)")
                except:
                    pass
            
            # Clean up tracked processes
            if hasattr(self, 'console_processes'):
                for proc in self.console_processes:
                    try:
                        if proc.poll() is None:  # Process is still running
                            self.log(f"Terminating console process PID {proc.pid}")
                            proc.terminate()
                            try:
                                proc.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                    except Exception:
                        pass
                self.console_processes = []
            
            # Clean up terminal multiplexers
            try:
                # Kill tmux sessions
                subprocess.run(["tmux", "kill-session", "-t", "nanobmc_multiflash"], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Kill screen sessions
                subprocess.run(["screen", "-S", "nanobmc_multiflash", "-X", "quit"], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Kill terminator processes with our config
                subprocess.run(["pkill", "-f", "nanobmc_multiflash"], 
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            
            except (FileNotFoundError, subprocess.SubprocessError):
                pass
            
            # Clean up serial connections
            try:
                from utils import cleanup_all_serial_connections
                connections_cleaned = cleanup_all_serial_connections()
                if connections_cleaned > 0:
                    self.log(f"Detached {connections_cleaned} serial connections")
            except Exception as e:
                self.log(f"Error detaching serial connections: {e}")
            
            self.log("Console cleanup completed - all minicom processes closed")
                
        except Exception as e:
            self.log(f"Error cleaning console processes: {e}")

    def generate_terminator_config(self, units):
        """Generate terminator config optimized for screen size with device and IP labels"""
        config = """[global_config]
    borderless = false
    [keybindings]
    [profiles]
    [[default]]
        scrollback_lines = 1000
        font = Monospace 9
        cursor_color = "#aaaaaa"
    """
        
        # Add profiles for each unit with device and IP info
        for unit in units:
            device = unit['device_var'].get()
            unit_id = unit['id']
            bmc_ip = unit['bmc_ip_var'].get() or "No IP"
            
            # Create a descriptive title
            device_name = device.split('/')[-1] if device else "No Device"  # Extract ttyUSB0 from /dev/ttyUSB0
            title = f"Unit{unit_id}-{device_name}-{bmc_ip}"
            
            config += f"""  [[unit_{unit_id}]]
        custom_command = minicom -D {device}
        use_custom_command = True
        scrollback_lines = 500
        font = Monospace 8
        exit_action = hold
        cursor_color = "#aaaaaa"
        background_color = "#300a24"
        foreground_color = "#ffffff"
    """
        
        config += "\n[layouts]\n  [[multi_unit]]\n"
        
        num_units = len(units)
        
        if num_units == 1:
            unit = units[0]
            device = unit['device_var'].get()
            bmc_ip = unit['bmc_ip_var'].get() or "No IP"
            device_name = device.split('/')[-1] if device else "No Device"
            title = f"Unit{unit['id']}-{device_name}-{bmc_ip}"
            
            config += f"""    [[[child1]]]
        parent = window0
        profile = unit_{unit['id']}
        type = Terminal
        title = {title}
        [[[window0]]]
        parent = ""
        type = Window
        size = 1200, 800
        title = NanoBMC Multi-Console
    """
        elif num_units == 2:
            # Side by side for 2 units
            config += f"""    [[[child1]]]
        parent = window0
        type = HPaned
        ratio = 0.5
        [[[terminal1]]]
        parent = child1
        profile = unit_{units[0]['id']}
        type = Terminal
        title = Unit{units[0]['id']}-{units[0]['device_var'].get().split('/')[-1] if units[0]['device_var'].get() else 'NoDevice'}-{units[0]['bmc_ip_var'].get() or 'NoIP'}
        [[[terminal2]]]
        parent = child1
        profile = unit_{units[1]['id']}
        type = Terminal
        title = Unit{units[1]['id']}-{units[1]['device_var'].get().split('/')[-1] if units[1]['device_var'].get() else 'NoDevice'}-{units[1]['bmc_ip_var'].get() or 'NoIP'}
        [[[window0]]]
        parent = ""
        type = Window
        size = 1400, 800
        title = NanoBMC Multi-Console
    """
        elif num_units == 3:
            # Top 2, bottom 1
            config += f"""    [[[child1]]]
        parent = window0
        type = VPaned
        ratio = 0.5
        [[[child2]]]
        parent = child1
        type = HPaned
        ratio = 0.5
        [[[terminal1]]]
        parent = child2
        profile = unit_{units[0]['id']}
        type = Terminal
        title = Unit{units[0]['id']}-{units[0]['device_var'].get().split('/')[-1] if units[0]['device_var'].get() else 'NoDevice'}-{units[0]['bmc_ip_var'].get() or 'NoIP'}
        [[[terminal2]]]
        parent = child2
        profile = unit_{units[1]['id']}
        type = Terminal
        title = Unit{units[1]['id']}-{units[1]['device_var'].get().split('/')[-1] if units[1]['device_var'].get() else 'NoDevice'}-{units[1]['bmc_ip_var'].get() or 'NoIP'}
        [[[terminal3]]]
        parent = child1
        profile = unit_{units[2]['id']}
        type = Terminal
        title = Unit{units[2]['id']}-{units[2]['device_var'].get().split('/')[-1] if units[2]['device_var'].get() else 'NoDevice'}-{units[2]['bmc_ip_var'].get() or 'NoIP'}
        [[[window0]]]
        parent = ""
        type = Window
        size = 1600, 1000
        title = NanoBMC Multi-Console
    """
        elif num_units == 4:
            # 2x2 grid
            config += f"""    [[[child1]]]
        parent = window0
        type = VPaned
        ratio = 0.5
        [[[child2]]]
        parent = child1
        type = HPaned
        ratio = 0.5
        [[[child3]]]
        parent = child1
        type = HPaned
        ratio = 0.5
        [[[terminal1]]]
        parent = child2
        profile = unit_{units[0]['id']}
        type = Terminal
        title = Unit{units[0]['id']}-{units[0]['device_var'].get().split('/')[-1] if units[0]['device_var'].get() else 'NoDevice'}-{units[0]['bmc_ip_var'].get() or 'NoIP'}
        [[[terminal2]]]
        parent = child2
        profile = unit_{units[1]['id']}
        type = Terminal
        title = Unit{units[1]['id']}-{units[1]['device_var'].get().split('/')[-1] if units[1]['device_var'].get() else 'NoDevice'}-{units[1]['bmc_ip_var'].get() or 'NoIP'}
        [[[terminal3]]]
        parent = child3
        profile = unit_{units[2]['id']}
        type = Terminal
        title = Unit{units[2]['id']}-{units[2]['device_var'].get().split('/')[-1] if units[2]['device_var'].get() else 'NoDevice'}-{units[2]['bmc_ip_var'].get() or 'NoIP'}
        [[[terminal4]]]
        parent = child3
        profile = unit_{units[3]['id']}
        type = Terminal
        title = Unit{units[3]['id']}-{units[3]['device_var'].get().split('/')[-1] if units[3]['device_var'].get() else 'NoDevice'}-{units[3]['bmc_ip_var'].get() or 'NoIP'}
        [[[window0]]]
        parent = ""
        type = Window
        size = 1600, 1200
        title = NanoBMC Multi-Console
    """
        else:
            # For more than 4, use individual windows instead
            self.log(f"Too many units ({num_units}) for multi-pane. Opening individual windows.")
            return None
        
        return config

    def log(self, message):
        """Add message to log"""
        timestamp = time.strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        
        if hasattr(self, 'log_text'):
            self.log_text.insert(tk.END, formatted)
            self.log_text.see(tk.END)
        
        # Also log to main app
        if hasattr(self.app_instance, 'log_message'):
            self.app_instance.log_message(f"[Multi] {message}")

    def on_close(self):
        """Handle window close with comprehensive cleanup"""
        self.log("Multi-unit window closing - performing cleanup...")
        
        # Save configuration before closing
        self.save_config()
        
        # Stop all operations first
        if self.operation_running:
            self.stop_all()
        else:
            # Cleanup servers even if not running operations
            self.cleanup_all_servers()
        
        # Clean up console processesf
        self.cleanup_console_processes()
        
        # Clean up any serial connections
        cleanup_all_serial_connections()
        
        # Additional cleanup for any remaining processes
        try:
            # Force cleanup of any remaining terminator processes
            subprocess.run(["pkill", "-f", "nanobmc.*terminator"], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        
        # Destroy the window
        self.destroy()

    async def login(self, config, log_func):
        """Login to BMC"""
        from utils import login
        result = await login(config.username, config.password, config.device, log_func)
        if result and "successful" not in result.lower():
            log_func("Login may have failed, continuing...")
        return result


def create_multi_unit_window(parent, app_instance):
    """Create multi-unit flash window"""
    if hasattr(app_instance, 'bmc_type') and app_instance.bmc_type.get() != 2:
        messagebox.showerror("Error", "Multi-unit flashing only for NanoBMC!")
        return None
    
    return MultiUnitFlashWindow(parent, app_instance)