import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import subprocess
import os
import sys
import threading
import http.server
import socketserver

class SNUCFlasher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SNUC DMI & FRU Tool")
        self.geometry("750x700")
        self.resizable(True, True)

        # Standard Presets from Email
        self.presets = {
            "Device 1 (MAC ending 52)": {
                "family": "extremeEDGE", "platform": "extremeEDGE", "manuf": "Simply NUC",
                "vendor": "Simply NUC", "sys_name": "EES24E30r8", "sku": "SNUC-EE3200-1U",
                "serial": "7M22002G"
            },
            "Device 2 (MAC ending 04)": {
                "family": "extremeEDGE", "platform": "extremeEDGE", "manuf": "Simply NUC",
                "vendor": "Simply NUC", "sys_name": "EES24E30r8", "sku": "SNUC-EE3200-1U",
                "serial": "7M22002F"
            },
            "Device 3 (MAC ending A0)": {
                "family": "extremeEDGE", "platform": "extremeEDGE", "manuf": "Simply NUC",
                "vendor": "Simply NUC", "sys_name": "EES24E30r8", "sku": "SNUC-EE3200-1U",
                "serial": "7J06ZZX3"
            },
            "Device 4 (MAC ending 40)": {
                "family": "extremeEDGE", "platform": "extremeEDGE", "manuf": "Simply NUC",
                "vendor": "Simply NUC", "sys_name": "EES24E30r8", "sku": "SNUC-EE3200-1U",
                "serial": "7J06ZZX1"
            }
        }

        # Variables
        self.tlv_path = tk.StringVar(value=os.path.join(os.getcwd(), "TLVwriter.py"))
        self.i2c_bus = tk.StringVar(value="6")
        self.http_server_process = None

        self.create_widgets()

    def create_widgets(self):
        tab_control = ttk.Notebook(self)
        tab_dmi = ttk.Frame(tab_control)
        tab_fru = ttk.Frame(tab_control)
        tab_control.add(tab_dmi, text='Host DMI Flasher')
        tab_control.add(tab_fru, text='BMC FRU Helper')
        tab_control.pack(expand=1, fill="both")

        self.build_dmi_tab(tab_dmi)
        self.build_fru_tab(tab_fru)

        # Common Console
        console_frame = ttk.LabelFrame(self, text="Operation Log / Console Output")
        console_frame.pack(side="bottom", fill="both", expand=True, padx=10, pady=10)
        self.console = scrolledtext.ScrolledText(console_frame, height=10, bg="black", fg="#00FF00")
        self.console.pack(fill="both", expand=True)

    def build_dmi_tab(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(padx=10, pady=10, fill="x")

        # --- Configuration Area ---
        config_frame = ttk.LabelFrame(frame, text="Configuration")
        config_frame.pack(fill="x", pady=5)

        ttk.Label(config_frame, text="Path to TLVwriter.py:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        ttk.Entry(config_frame, textvariable=self.tlv_path, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(config_frame, text="Browse", command=self.browse_tlv).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(config_frame, text="I2C Bus Number:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        bus_frame = ttk.Frame(config_frame)
        bus_frame.grid(row=1, column=1, sticky="w")
        ttk.Entry(bus_frame, textvariable=self.i2c_bus, width=5).pack(side="left", padx=5)
        ttk.Button(bus_frame, text="Test Bus (dump 0x50)", command=self.test_bus).pack(side="left", padx=5)

        # --- Preset Selection ---
        preset_frame = ttk.LabelFrame(frame, text="Unit Presets")
        preset_frame.pack(fill="x", pady=10)

        ttk.Label(preset_frame, text="Select Target Device:").pack(side="left", padx=5, pady=10)
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_var, state="readonly", width=30)
        self.preset_combo['values'] = list(self.presets.keys())
        self.preset_combo.pack(side="left", padx=5)
        self.preset_combo.bind("<<ComboboxSelected>>", self.load_preset)

        # --- Data Fields ---
        fields_frame = ttk.LabelFrame(frame, text="DMI Data (Editable)")
        fields_frame.pack(fill="x", pady=5)

        self.entries = {}
        labels = ["Family", "Platform Name", "Manuf Name", "Vendor Name", "Sys Name", "Sys SKU", "Sys Serial Number"]
        keys = ["family", "platform", "manuf", "vendor", "sys_name", "sku", "serial"]

        for i, (label, key) in enumerate(zip(labels, keys)):
            ttk.Label(fields_frame, text=f"{label}:").grid(row=i, column=0, sticky="e", padx=5, pady=2)
            self.entries[key] = tk.StringVar()
            ttk.Entry(fields_frame, textvariable=self.entries[key], width=40).grid(row=i, column=1, sticky="w", padx=5, pady=2)

        # --- Action ---
        action_frame = ttk.Frame(frame)
        action_frame.pack(fill="x", pady=20)
        btn_flash = tk.Button(action_frame, text="FLASH DMI NOW", bg="#dd5555", fg="white", font=("Arial", 12, "bold"), height=2, command=self.flash_dmi)
        btn_flash.pack(fill="x", padx=50)

    def build_fru_tab(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(padx=10, pady=10, fill="both", expand=True)

        info_lbl = ttk.Label(frame, text="FRU Flashing must be done ON the BMC.\nThis tool helps host the required file and generate the commands.", justify="center", font=("Arial", 10, "italic"))
        info_lbl.pack(pady=10)

        # Step 1: Host Server
        step1 = ttk.LabelFrame(frame, text="Step 1: Host FRU Script (Run on THIS Host)")
        step1.pack(fill="x", pady=10)

        self.server_btn_var = tk.StringVar(value="Start HTTP Server (Port 80)")
        tk.Button(step1, textvariable=self.server_btn_var, command=self.toggle_http_server, bg="#55dd55", height=2).pack(fill="x", padx=50, pady=10)
        ttk.Label(step1, text="Ensure 'FRU_flash_v2.sh' is in the same folder as this app.").pack()

        # Step 2: BMC Commands
        step2 = ttk.LabelFrame(frame, text="Step 2: Run on BMC (Copy/Paste these)")
        step2.pack(fill="both", expand=True, pady=10)

        self.bmc_text = tk.Text(step2, wrap="word", height=15, font=("Courier", 10))
        self.bmc_text.pack(fill="both", expand=True, padx=5, pady=5)

        # Populate initial IP guess
        try:
            # Attempt to get local IP that isn't localhost
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            host_ip = s.getsockname()[0]
            s.close()
        except:
            host_ip = "<YOUR_HOST_IP>"

        cmds = f"""# 1. SSH into the BMC.
# 2. Download the script from this host:
curl -o FRU_flash_v2.sh http://{host_ip}/FRU_flash_v2.sh
chmod +x ./FRU_flash_v2.sh

# 3. Execute FLASH (Select device below to generate specific command):
"""
        self.bmc_text.insert("1.0", cmds)

        # BMC Command Generator
        gen_frame = ttk.Frame(step2)
        gen_frame.pack(fill="x", pady=5)
        ttk.Label(gen_frame, text="Generate BMC Command for:").pack(side="left")
        self.fru_preset_combo = ttk.Combobox(gen_frame, state="readonly", values=list(self.presets.keys()))
        self.fru_preset_combo.pack(side="left", padx=5)
        self.fru_preset_combo.bind("<<ComboboxSelected>>", self.generate_bmc_cmd)

    def generate_bmc_cmd(self, event):
        name = self.fru_preset_combo.get()
        data = self.presets[name]
        # Assuming standard FRU command structure from email
        cmd = f"\nsudo ./FRU_flash_v2.sh --i2c-bus {self.i2c_bus.get()} --sku {data['sku']} --asmid {data['serial']} --mfg \"{data['manuf']}\"\n"
        self.bmc_text.insert(tk.END, "\n# Command for " + name + ":" + cmd)
        self.bmc_text.see(tk.END)

    def log(self, message):
        self.console.insert(tk.END, message + "\n")
        self.console.see(tk.END)

    def browse_tlv(self):
        filename = filedialog.askopenfilename(title="Select TLVwriter.py", filetypes=[("Python Files", "*.py")])
        if filename:
            self.tlv_path.set(filename)

    def load_preset(self, event):
        selection = self.preset_var.get()
        if selection in self.presets:
            data = self.presets[selection]
            for key, value in data.items():
                self.entries[key].set(value)
            self.log(f"Loaded preset for: {selection}")

    def test_bus(self):
        bus = self.i2c_bus.get()
        cmd = ["sudo", "i2cdump", "-y", bus, "0x50"]
        self.log(f"Running: {' '.join(cmd)}")
        threading.Thread(target=self.run_command, args=(cmd,), daemon=True).start()

    def flash_dmi(self):
        # Validation
        if not os.path.exists(self.tlv_path.get()):
            messagebox.showerror("Error", "TLVwriter.py not found at specified path.")
            return

        cmd = [
            "sudo", "python3", self.tlv_path.get(), "--yes", self.i2c_bus.get(), "0x50",
            "TLV_CODE_FAMILY", self.entries['family'].get(),
            "TLV_CODE_PLATFORM_NAME", self.entries['platform'].get(),
            "TLV_CODE_MANUF_NAME", self.entries['manuf'].get(),
            "TLV_CODE_VENDOR_NAME", self.entries['vendor'].get(),
            "TLV_CODE_SYS_NAME", self.entries['sys_name'].get(),
            "TLV_CODE_SYS_SKU", self.entries['sku'].get(),
            "TLV_CODE_SYS_SERIAL_NUMBER", self.entries['serial'].get()
        ]

        if messagebox.askyesno("Confirm Flash", f"Are you sure you want to flash Bus {self.i2c_bus.get()} with Serial {self.entries['serial'].get()}?"):
            self.log("-" * 40)
            self.log("STARTING DMI FLASH...")
            threading.Thread(target=self.run_command, args=(cmd,), daemon=True).start()

    def run_command(self, cmd):
        try:
            # Using Popen to capture output in real-time if possible, 
            # but for simplicity in GUI, running and capturing after is safer to avoid hangs.
            process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.log(process.stdout)
            if process.returncode == 0:
                self.log("SUCCESS: Command completed successfully.")
            else:
                self.log(f"FAILURE: Command exited with code {process.returncode}.")
        except Exception as e:
            self.log(f"ERROR: Could not execute command: {e}")

    def toggle_http_server(self):
        if self.http_server_process is None:
            # Start server
             # Note: Port 80 requires sudo. If the GUI isn't run with sudo, this fails.
             # We'll try a high port if 80 fails, or assume user ran GUI with sudo.
            try:
                handler = http.server.SimpleHTTPRequestHandler
                self.httpd = socketserver.TCPServer(("", 80), handler)
                self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
                self.server_thread.start()
                self.http_server_process = True
                self.server_btn_var.set("STOP HTTP Server")
                self.log("HTTP Server started on Port 80.")
            except PermissionError:
                 messagebox.showerror("Permission Error", "Cannot bind to Port 80. Please run this GUI with 'sudo' to use the HTTP feature, or use a different port manually.")
            except Exception as e:
                 self.log(f"Error starting HTTP server: {e}")
        else:
            # Stop server
            self.httpd.shutdown()
            self.httpd.server_close()
            self.http_server_process = None
            self.server_btn_var.set("Start HTTP Server (Port 80)")
            self.log("HTTP Server stopped.")

if __name__ == "__main__":
    # Optional: Check for sudo. If not root, some features (i2cdump, port 80) might fail.
    if os.geteuid() != 0:
       print("WARNING: Not running as root. I2C flashing and Port 80 server might fail.")

    import socket # needed for IP detection in FRU tab
    app = SNUCFlasher()
    app.mainloop()