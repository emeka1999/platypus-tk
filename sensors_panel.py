"""
Fans & Sensors panel for Platypus.

Shows fan speeds, temperatures, voltages, and power supply status pulled
via Redfish (Chassis Thermal/Power resources). Sits below the log section.

Auto-refreshes shortly after the BMC IP field changes (debounced, same as
the Inventory panel), and once at startup if the IP is already set.
"""

import threading

import customtkinter as ctk

import bmc


class SensorsPanel(ctk.CTkFrame):
    def __init__(self, master, get_bmc_ip, get_username=None, get_password=None,
                 log=None, bmc_ip_var=None, on_ready=None, **kwargs):
        super().__init__(master, **kwargs)
        self.get_bmc_ip = get_bmc_ip
        self.get_username = get_username
        self.get_password = get_password
        self.log = log or (lambda msg: None)
        self.on_ready = on_ready or (lambda ready: None)

        self._debounce_job = None

        self._build_ui()

        if bmc_ip_var is not None:
            bmc_ip_var.trace_add("write", self._on_bmc_ip_changed)

        # Try once at startup too - the trace above only fires on *future*
        # changes, so if the IP was already populated before this panel
        # was built (e.g. loaded from a saved config), it needs this to
        # ever auto-load at all.
        self._auto_refresh()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 2))

        ctk.CTkLabel(
            header, text="Fans & Sensors", font=ctk.CTkFont(size=13, weight="bold"),
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
        complex, deeply-nested app like this one, so this binds directly
        instead. Needs to be reapplied after every rebuild of the body's
        contents, since new widgets don't inherit the old bindings."""
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
            self.body, text="Click Refresh to query fan, temperature, and power sensors.",
            text_color="gray",
        ).pack(anchor="w", padx=4, pady=8)
        self._bind_mousewheel(self.body)

    def _status(self, text, color="gray"):
        self.status_label.configure(text=text, text_color=color)

    def _on_bmc_ip_changed(self, *args):
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
        if bmc_ip and user and password:
            self.refresh()

    def refresh(self):
        bmc_ip = self.get_bmc_ip() if self.get_bmc_ip else None
        user = self.get_username() if self.get_username else None
        password = self.get_password() if self.get_password else None
        if not bmc_ip or not user or not password:
            self._status("Set BMC IP, username, and password first.", "#e74c3c")
            return

        self._status("Querying sensors...", "gray")

        def _worker():
            try:
                data = bmc.get_sensors(user, password, bmc_ip)
                self.after(0, lambda: self._on_loaded(data))
            except ConnectionError as e:
                error_msg = str(e)
                self.after(0, lambda: self._status(error_msg, "#e74c3c"))
                self.after(0, lambda: self.on_ready(False))
            except Exception as e:
                error_msg = str(e)
                self.after(0, lambda: self._status(f"Sensor query failed: {error_msg}", "#e74c3c"))
                self.after(0, lambda: self.on_ready(False))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_loaded(self, data):
        self.log("Sensors refreshed.")
        self._status("Updated", "#2ecc71")
        self.on_ready(True)
        for w in self.body.winfo_children():
            w.destroy()

        self._render_table(
            "Fans", data.get("fans") or [],
            ["Name", "Reading", "Units", "Health"],
            lambda f: [f.get("name") or "-", f.get("reading"), f.get("units") or "-", f.get("health") or "-"],
        )
        self._render_table(
            "Temperatures", data.get("temperatures") or [],
            ["Name", "\u00b0C", "Health"],
            lambda t: [t.get("name") or "-", t.get("reading_c"), t.get("health") or "-"],
        )
        self._render_table(
            "Voltages", data.get("voltages") or [],
            ["Name", "Volts", "Health"],
            lambda v: [v.get("name") or "-", v.get("reading_volts"), v.get("health") or "-"],
        )
        self._render_table(
            "Power Supplies", data.get("power_supplies") or [],
            ["Name", "Input Watts", "Health"],
            lambda p: [p.get("name") or "-", p.get("input_watts"), p.get("status_health") or "-"],
        )

        self._bind_mousewheel(self.body)

    def _render_table(self, title, rows, headers, row_fn):
        ctk.CTkLabel(self.body, text=title, font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(6, 2))
        if not rows:
            ctk.CTkLabel(self.body, text=f"No {title.lower()} reported.", text_color="gray").pack(anchor="w", padx=4)
            return

        table = ctk.CTkFrame(self.body, fg_color="transparent")
        table.pack(fill="x")
        for c, h in enumerate(headers):
            ctk.CTkLabel(table, text=h, font=ctk.CTkFont(weight="bold"), text_color="gray").grid(
                row=0, column=c, sticky="w", padx=4,
            )
        health_col = len(headers) - 1
        for r, item in enumerate(rows, start=1):
            values = row_fn(item)
            for c, v in enumerate(values):
                text = "-" if v is None else str(v)
                label = ctk.CTkLabel(table, text=text, anchor="w")
                if c == health_col:
                    if text == "OK":
                        label.configure(text_color="#2ecc71")
                    elif text not in ("-", "OK"):
                        label.configure(text_color="#e74c3c")
                label.grid(row=r, column=c, sticky="w", padx=4, pady=1)