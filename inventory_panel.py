"""
System inventory panel for Platypus.

Shows a snapshot of host/BMC identity, firmware versions, network
interfaces, and storage drives, pulled via Redfish. Meant to sit below the
Serial/SOL console panel on the right side of the window.

Auto-refreshes shortly after the BMC IP field changes (debounced, so it
doesn't fire on every keystroke), rather than requiring a manual click.
"""

import threading

import customtkinter as ctk

import bmc


class InventoryPanel(ctk.CTkFrame):
    def __init__(self, master, get_bmc_ip, get_username=None, get_password=None,
                 log=None, bmc_ip_var=None, on_ready=None, **kwargs):
        super().__init__(master, **kwargs)
        self.get_bmc_ip = get_bmc_ip
        self.get_username = get_username
        self.get_password = get_password
        self.log = log or (lambda msg: None)
        # Called with True/False when a load succeeds/fails, so other
        # panels (e.g. the console's Next Boot/Power controls) can also
        # unlock once THIS panel confirms Redfish actually works, not just
        # when a SOL/SOL2 text console happens to be connected.
        self.on_ready = on_ready or (lambda ready: None)

        self._debounce_job = None

        self._build_ui()

        # Auto-fetch once the BMC IP is set, instead of requiring a manual
        # Refresh click. Needs the actual StringVar (not just a getter) to
        # hook a change notification; falls back to manual-only if not
        # given one.
        if bmc_ip_var is not None:
            bmc_ip_var.trace_add("write", self._on_bmc_ip_changed)

        # Also try right away at startup: if the BMC IP/credentials were
        # already populated before this panel was built (e.g. loaded from
        # a saved config), the trace above never fires for that - it only
        # catches *future* changes - so nothing would ever auto-load
        # without this.
        self._auto_refresh()

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

    def _bind_mousewheel(self, widget):
        """Explicitly wire mouse-wheel scrolling into the scrollable body's
        canvas, on this widget and every descendant. CTkScrollableFrame's
        own built-in global mousewheel binding can be unreliable in a
        complex, deeply-nested app like this one (other widgets elsewhere
        with their own bindings/focus can end up shadowing it), so this
        binds directly and explicitly instead. Needs to be reapplied after
        every rebuild of the body's contents, since new widgets don't
        inherit the old bindings."""
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _on_mousewheel(self, event):
        canvas = getattr(self.body, "_parent_canvas", None)
        if canvas is None:
            return
        if event.num == 4:
            canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            canvas.yview_scroll(1, "units")
        else:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _render_placeholder(self):
        for w in self.body.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self.body,
            text="Click Refresh to query BIOS/BMC version, NICs, and drives.",
            text_color="gray",
        ).pack(anchor="w", padx=4, pady=8)
        self._bind_mousewheel(self.body)

    def _status(self, text, color="gray"):
        self.status_label.configure(text=text, text_color=color)

    def _on_bmc_ip_changed(self, *args):
        # Debounced: the StringVar fires on every keystroke while someone's
        # typing an IP, and querying Redfish after each one would be both
        # wasteful and produce a flurry of misleading "can't connect"
        # errors for a still-incomplete address. Wait for a pause instead.
        if self._debounce_job is not None:
            try:
                self.after_cancel(self._debounce_job)
            except Exception:
                pass
        self._debounce_job = self.after(900, self._auto_refresh)

    def _auto_refresh(self):
        self._debounce_job = None
        bmc_ip = self.get_bmc_ip() if self.get_bmc_ip else None
        user = self.get_username() if self.get_username else None
        password = self.get_password() if self.get_password else None
        # Only auto-fire once there's actually something to try - an empty
        # or credential-less field isn't a connection failure, it's just
        # not filled in yet, so stay quiet rather than showing an error.
        if bmc_ip and user and password:
            self.refresh()

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
            except ConnectionError as e:
                # bmc.get_system_inventory raises this specifically when
                # the BMC couldn't be reached at all (as opposed to
                # reachable-but-rejected-credentials or a Redfish-schema
                # issue) - its message already names the IP and says to
                # check it, so just surface it directly.
                error_msg = str(e)
                self.after(0, lambda: self._status(error_msg, "#e74c3c"))
                self.after(0, lambda: self.on_ready(False))
            except Exception as e:
                # Capture the message into a plain variable before the
                # lambda: `except ... as e` is auto-deleted by Python once
                # this block ends, and self.after() runs the lambda later,
                # after that deletion - referencing `e` directly here would
                # raise NameError the moment an error actually occurs.
                error_msg = str(e)
                self.after(0, lambda: self._status(f"Inventory query failed: {error_msg}", "#e74c3c"))
                self.after(0, lambda: self.on_ready(False))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_loaded(self, info):
        self.log("System inventory refreshed.")
        self._status("Updated", "#2ecc71")
        self.on_ready(True)
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

        self._bind_mousewheel(self.body)