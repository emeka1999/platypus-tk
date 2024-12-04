import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import asyncio
import glob
import bmc
import json
import os


class PlatypusApp:
    CONFIG_FILE = "platypus_config.json"

    def __init__(self, root):
        self.root = root
        self.root.title("Platypus")
        self.root.geometry("950x850")

        # Initialize variables
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.bmc_ip = tk.StringVar()
        self.your_ip = tk.StringVar()
        self.flash_file = None
        self.serial_device = tk.StringVar()
        self.bmc_type = tk.IntVar(value=1)  # Default to MOS BMC (value = 1)
        self.lock_buttons = False

        # Load saved configuration
        self.load_config()

        # Progress bar
        self.progress = ttk.Progressbar(root, orient="horizontal", length=300, mode="determinate")
        self.progress.grid(row=0, column=1, pady=10, padx=10)

        # Serial device dropdown
        tk.Label(root, text="Serial Device:").grid(row=1, column=0, padx=5, sticky=tk.W)
        self.serial_dropdown = ttk.Combobox(root, textvariable=self.serial_device)
        self.serial_dropdown.grid(row=1, column=1, padx=5)
        self.refresh_serial_devices()

        ttk.Button(root, text="Refresh", command=self.refresh_serial_devices).grid(row=1, column=2, padx=5)

        # Input fields
        tk.Label(root, text="Username:").grid(row=2, column=0, padx=5, sticky=tk.W)
        tk.Entry(root, textvariable=self.username).grid(row=2, column=1, padx=5)

        tk.Label(root, text="Password:").grid(row=3, column=0, padx=5, sticky=tk.W)
        tk.Entry(root, textvariable=self.password, show='*').grid(row=3, column=1, padx=5)

        tk.Label(root, text="BMC IP:").grid(row=4, column=0, padx=5, sticky=tk.W)
        tk.Entry(root, textvariable=self.bmc_ip).grid(row=4, column=1, padx=5)

        tk.Label(root, text="Host IP:").grid(row=5, column=0, padx=5, sticky=tk.W)
        tk.Entry(root, textvariable=self.your_ip).grid(row=5, column=1, padx=5)

        # BMC Type Radio Buttons
        tk.Label(root, text="BMC Type:").grid(row=6, column=0, sticky=tk.W)
        ttk.Radiobutton(root, text="MOS BMC", variable=self.bmc_type, value=1).grid(row=6, column=1, sticky=tk.W)
        ttk.Radiobutton(root, text="Nano BMC", variable=self.bmc_type, value=2).grid(row=7, column=1, sticky=tk.W)

        # Buttons
        ttk.Button(root, text="Update BMC", command=self.update_bmc).grid(row=8, column=0, pady=10)
        ttk.Button(root, text="Set BMC IP", command=self.set_bmc_ip).grid(row=8, column=1, pady=10)
        ttk.Button(root, text="Flash U-Boot", command=self.flash_u_boot).grid(row=9, column=1, pady=10)
        ttk.Button(root, text="Power ON Host", command=self.power_on_host).grid(row=10, column=0, pady=10)
        ttk.Button(root, text="Reboot BMC", command=self.reboot_bmc).grid(row=10, column=1, pady=10)
        ttk.Button(root, text="Flash eMMC", command=self.flash_emmc).grid(row=11, column=0, pady=10)
        ttk.Button(root, text="Reset BMC", command=self.reset_bmc).grid(row=11, column=1, pady=10)
        ttk.Button(root, text="Flash EEPROM", command=self.flash_eeprom).grid(row=9, column=0, pady=10)
        ttk.Button(root, text="Factory Reset", command=self.factory_reset).grid(row=12, column=0, pady=10)

        # Log box
        tk.Label(root, text="Log:").grid(row=13, column=0, sticky=tk.W)
        self.log_box = tk.Text(root, height=20, width=100, state="disabled")
        self.log_box.grid(row=14, column=0, columnspan=2, pady=10)

        # Save configuration on close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def refresh_serial_devices(self):
        """Detect available serial devices and populate the dropdown."""
        devices = glob.glob("/dev/ttyUSB*")
        self.serial_dropdown["values"] = devices
        if devices:
            self.serial_device.set(devices[0])  # Default to the first device

    def log_message(self, message):
        """Log messages to the log box."""
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, f"{message}\n")
        self.log_box.configure(state="disabled")
        self.log_box.see(tk.END)

    def update_progress(self, value):
        """Update the progress bar."""
        self.progress["value"] = value * 100

    def validate_input(self):
        """Ensure all required input fields are filled."""
        if not self.username.get() or not self.password.get() or not self.bmc_ip.get() or not self.serial_device.get():
            messagebox.showerror("Error", "Please fill in all required fields and select a serial device.")
            return False
        return True

    def validate_button_click(self):
        """Prevent multiple button clicks during ongoing operations."""
        if self.lock_buttons:
            self.log_message("Another operation is in progress. Please wait...")
            return False
        self.lock_buttons = True
        return True

    def save_config(self):
        """Save the current configuration to a JSON file."""
        config = {
            "username": self.username.get(),
            "password": self.password.get(),
            "bmc_ip": self.bmc_ip.get(),
            "your_ip": self.your_ip.get()
        }
        with open(self.CONFIG_FILE, 'w') as config_file:
            json.dump(config, config_file)

    def load_config(self):
        """Load the configuration from a JSON file if it exists."""
        if os.path.exists(self.CONFIG_FILE):
            with open(self.CONFIG_FILE, 'r') as config_file:
                config = json.load(config_file)
                self.username.set(config.get("username", ""))
                self.password.set(config.get("password", ""))
                self.bmc_ip.set(config.get("bmc_ip", ""))
                self.your_ip.set(config.get("your_ip", ""))

    def on_close(self):
        """Handle application close event."""
        self.save_config()
        self.root.destroy()

    def update_bmc(self):
        """Trigger the BMC update process."""
        if not self.validate_input() or not self.validate_button_click():
            return

        self.flash_file = filedialog.askopenfilename(filetypes=[("Tar GZ Files", "*.tar.gz")])
        if not self.flash_file:
            self.log_message("No file selected.")
            self.lock_buttons = False
            return

        threading.Thread(target=self.run_update_bmc).start()

    def run_update_bmc(self):
        """Run the BMC update process asynchronously."""
        try:
            with open(self.flash_file, 'rb') as fw_file:
                fw_content = fw_file.read()
                self.log_message("Starting BMC Update...")
                asyncio.run(bmc.bmc_update(
                    self.username.get(), self.password.get(), self.bmc_ip.get(),
                    fw_content, self.update_progress, self.log_message, self.serial_device.get()
                ))
        finally:
            self.lock_buttons = False

    def set_bmc_ip(self):
        """Handles setting the BMC IP address."""
        if not self.validate_input() or not self.validate_button_click():
            return

        threading.Thread(target=self.run_set_bmc_ip).start()

    def run_set_bmc_ip(self):
        """Runs the BMC IP setting process asynchronously."""
        try:
            response = asyncio.run(bmc.set_ip(
                self.bmc_ip.get(), self.username.get(), self.password.get(),
                self.update_progress, self.log_message, self.serial_device.get()
            ))
            if "MOS-BMC login:" in response:
                self.log_message("MOS-BMC login prompt detected. Sending credentials...")
                asyncio.run(bmc.send_credentials(
                    self.username.get(), self.password.get(), self.serial_device.get(), self.log_message
                ))
        finally:
            self.lock_buttons = False

    def network_reset(self):
        """Reset the network settings."""
        if not self.validate_input() or not self.validate_button_click():
            return

        threading.Thread(target=self.run_network_reset).start()

    def run_network_reset(self):
        """Runs the network reset process asynchronously."""
        try:
            asyncio.run(bmc.reset_ip(
                self.username.get(), self.password.get(), self.bmc_ip.get(),
                self.update_progress, self.log_message
            ))
        finally:
            self.lock_buttons = False

    def flash_u_boot(self):
        """Flash the U-Boot firmware."""
        if not self.validate_input() or not self.validate_button_click():
            return

        self.flash_file = filedialog.askopenfilename()
        if not self.flash_file:
            self.log_message("No file selected.")
            self.lock_buttons = False
            return

        threading.Thread(target=self.run_flash_u_boot).start()

    def run_flash_u_boot(self):
        """Runs the U-Boot flashing process asynchronously."""
        try:
            asyncio.run(bmc.flasher(
                self.username.get(), self.password.get(), self.flash_file,
                self.your_ip.get(), self.update_progress, self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False

    def power_on_host(self):
        """Power on the host system."""
        if not self.validate_input() or not self.validate_button_click():
            return

        threading.Thread(target=self.run_power_on_host).start()

    def run_power_on_host(self):
        """Runs the host power-on process asynchronously."""
        try:
            asyncio.run(bmc.power_host(
                self.username.get(), self.password.get(), self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False

    def reboot_bmc(self):
        """Reboot the BMC."""
        if not self.validate_input() or not self.validate_button_click():
            return

        threading.Thread(target=self.run_reboot_bmc).start()

    def run_reboot_bmc(self):
        """Runs the BMC reboot process asynchronously."""
        try:
            asyncio.run(bmc.reboot_bmc(
                self.username.get(), self.password.get(), self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False

    def flash_emmc(self):
        """Flash the eMMC storage."""
        if not self.validate_input() or not self.bmc_type.get() or not self.validate_button_click():
            return

        threading.Thread(target=self.run_flash_emmc).start()

    def run_flash_emmc(self):
        """Runs the eMMC flashing process asynchronously."""
        try:
            response = asyncio.run(bmc.flash_emmc(
                self.bmc_ip.get(), filedialog.askdirectory(), self.your_ip.get(),
                self.bmc_type.get(), self.update_progress, self.log_message
            ))
            if "HTTP/1.0 404 File not found" in response:
                raise Exception("Error: File not found during eMMC flashing.")
        except Exception as e:
            self.log_message(str(e))
        finally:
            self.lock_buttons = False

    def reset_bmc(self):
        """Reset the BMC."""
        if not self.validate_button_click():
            return

        threading.Thread(target=self.run_reset_bmc).start()

    def run_reset_bmc(self):
        """Runs the BMC reset process asynchronously."""
        try:
            asyncio.run(bmc.reset_uboot(self.log_message))
        finally:
            self.lock_buttons = False

    def flash_eeprom(self):
        """Flash the EEPROM."""
        if not self.validate_input() or not self.validate_button_click():
            return

        self.flash_file = filedialog.askopenfilename(
            title="Select FRU File",
            filetypes=[("Binary Files", "*.bin"), ("All Files", "*.*")]
        )
        if not self.flash_file:
            self.log_message("No file selected for EEPROM flashing.")
            self.lock_buttons = False
            return

        threading.Thread(target=self.run_flash_eeprom).start()

    def run_flash_eeprom(self):
        """Runs the EEPROM flashing process asynchronously."""
        try:
            self.log_message(f"Starting EEPROM flashing with file: {self.flash_file}")
            asyncio.run(bmc.flash_eeprom(
                self.username.get(), self.password.get(), self.your_ip.get(),
                self.update_progress, self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False
            
    def factory_reset(self):
        """Execute BMC factory reset."""
        if not self.validate_input() or not self.validate_button_click():
            return

        # Start the thread with the correct context
        threading.Thread(target=self.run_factory_reset).start()

    def run_factory_reset(self):
        """Run the factory reset asynchronously."""
        try:
            asyncio.run(bmc.bmc_factory_reset(
                self.log_message, self.serial_device.get()
            ))
        finally:
            self.lock_buttons = False



if __name__ == "__main__":
    root = tk.Tk()
    app = PlatypusApp(root)
    root.mainloop()
