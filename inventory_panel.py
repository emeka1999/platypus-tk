"""
System inventory panel for Platypus.

Shows a snapshot of host/BMC identity, firmware versions, network
interfaces, and storage drives, pulled via Redfish. Meant to sit below the
Serial/SOL console panel on the right side of the window.
"""

import threading

import customtkinter as ctk

import bmc


class InventoryPanel(ctk.CTkFrame):
    def __init__(self, master, get_bmc_ip, get_username=None, get_password=None,
                 log=None, **kwargs):
        super().__init__(master, **kwargs)
        self.get_bmc_ip = get_bmc_ip
        self.get_username = get_username
        self.get_password = get_password
        self.log = log or (lambda msg: None)

        self._build_ui()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 2))

        ctk.CTkLabel(
            header, text="System Inventory", font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(header, text="Refresh", width=80, command=self.refresh).pack(side="right")
        self.status_label = ctk.CTkLabel(header, text="Not loaded", text_color="gray")
        self.status_label.pack(side="right", padx=(0, 10))

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._render_placeholder()

    def _render_placeholder(self):
        for w in self.body.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self.body,
            text="Click Refresh to query BIOS/BMC version, NICs, and drives.",
            text_color="gray",
        ).pack(anchor="w", padx=4, pady=8)

    def _status(self, text, color="gray"):
        self.status_label.configure(text=text, text_color=color)

    def refresh(self):
        bmc_ip = self.get_bmc_ip() if self.get_bmc_ip else None
        user = self.get_username() if self.get_username else None
        password = self.get_password() if self.get_password else None
        if not bmc_ip or not user or not password:
            self._status("Set BMC IP, username, and password first.", "#e74c3c")
            return

        self._status("Querying inventory...", "gray")

        def _worker():
            try:
                info = bmc.get_system_inventory(user, password, bmc_ip)
                self.after(0, lambda: self._on_loaded(info))
            except Exception as e:
                self.after(0, lambda: self._status(f"Inventory query failed: {e}", "#e74c3c"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_loaded(self, info):
        self.log("System inventory refreshed.")
        self._status("Updated", "#2ecc71")
        for w in self.body.winfo_children():
            w.destroy()

        # --- Summary ---
        summary_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        summary_frame.pack(fill="x", pady=(0, 8))

        bmc_version = " ".join(filter(None, [info.get("bmc_model"), info.get("bmc_version")])) or "-"
        rows = [
            ("Manufacturer / Model", " / ".join(filter(None, [info.get("manufacturer"), info.get("model")])) or "-"),
            ("Serial / Part Number", " / ".join(filter(None, [info.get("serial_number"), info.get("part_number")])) or "-"),
            ("BIOS Version", info.get("bios_version") or "-"),
            ("BMC Version", bmc_version),
            ("CPU", info.get("cpu") or "-"),
            ("Memory", f"{info['memory_gb']} GB" if info.get("memory_gb") else "-"),
        ]
        for i, (label, value) in enumerate(rows):
            ctk.CTkLabel(
                summary_frame, text=f"{label}:", anchor="w", font=ctk.CTkFont(weight="bold"),
            ).grid(row=i, column=0, sticky="w", padx=(4, 8), pady=1)
            ctk.CTkLabel(summary_frame, text=value, anchor="w").grid(row=i, column=1, sticky="w", pady=1)
        summary_frame.grid_columnconfigure(1, weight=1)

        # --- Network interfaces ---
        ctk.CTkLabel(
            self.body, text="Network Interfaces", font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", pady=(6, 2))
        nics = info.get("nics") or []
        if nics:
            nic_frame = ctk.CTkFrame(self.body, fg_color="transparent")
            nic_frame.pack(fill="x")
            for c, h in enumerate(["Interface", "MAC", "Link", "Speed", "IPv4"]):
                ctk.CTkLabel(
                    nic_frame, text=h, font=ctk.CTkFont(weight="bold"), text_color="gray",
                ).grid(row=0, column=c, sticky="w", padx=4)
            for r, nic in enumerate(nics, start=1):
                speed = f"{nic['speed_mbps']} Mbps" if nic.get("speed_mbps") else "-"
                values = [
                    nic.get("name") or "-", nic.get("mac") or "-",
                    nic.get("link_status") or "-", speed, nic.get("ipv4") or "-",
                ]
                for c, v in enumerate(values):
                    ctk.CTkLabel(nic_frame, text=v, anchor="w").grid(row=r, column=c, sticky="w", padx=4, pady=1)
        else:
            ctk.CTkLabel(self.body, text="No network interfaces found.", text_color="gray").pack(anchor="w", padx=4)

        # --- Drives ---
        ctk.CTkLabel(self.body, text="Drives", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(10, 2))
        drives = info.get("drives") or []
        if drives:
            drive_frame = ctk.CTkFrame(self.body, fg_color="transparent")
            drive_frame.pack(fill="x")
            for c, h in enumerate(["Name", "Model", "Capacity", "Media", "Protocol", "Health"]):
                ctk.CTkLabel(
                    drive_frame, text=h, font=ctk.CTkFont(weight="bold"), text_color="gray",
                ).grid(row=0, column=c, sticky="w", padx=4)
            for r, drive in enumerate(drives, start=1):
                cap = f"{drive['capacity_gb']} GB" if drive.get("capacity_gb") else "-"
                health = drive.get("health") or "-"
                values = [
                    drive.get("name") or "-", drive.get("model") or "-", cap,
                    drive.get("media_type") or "-", drive.get("protocol") or "-", health,
                ]
                for c, v in enumerate(values):
                    label = ctk.CTkLabel(drive_frame, text=v, anchor="w")
                    if c == 5:  # Health column
                        if v == "OK":
                            label.configure(text_color="#2ecc71")
                        elif v != "-":
                            label.configure(text_color="#e74c3c")
                    label.grid(row=r, column=c, sticky="w", padx=4, pady=1)
        else:
            ctk.CTkLabel(self.body, text="No drives found.", text_color="gray").pack(anchor="w", padx=4)
