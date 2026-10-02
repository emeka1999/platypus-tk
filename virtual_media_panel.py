"""
Virtual Media panel for Platypus.

Mount/unmount an ISO or IMG to the BMC's Virtual Media via Redfish. Two
ways to mount:
  - Paste a URL the BMC can already reach (http/https).
  - Browse to a local file - this spins up a small local HTTPS server to
    host it, uploads a self-signed cert to the BMC's trust store so it'll
    trust that server, and fills in the resulting URL automatically.
"""

import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import socketserver

import customtkinter as ctk

import bmc

try:
    from RangeHTTPServer import RangeRequestHandler
    RANGE_SUPPORT = True
except ImportError:
    import http.server
    RangeRequestHandler = http.server.SimpleHTTPRequestHandler
    RANGE_SUPPORT = False


def _get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class _StreamingHTTPHandler(RangeRequestHandler):
    """Serves the hosted file with byte-range support (needed by some BMCs
    when reading a mounted ISO) and forces sane MIME types for it."""

    def log_message(self, format, *args):
        pass  # keep the terminal quiet

    def guess_type(self, path):
        if path.lower().endswith(".iso"):
            return "application/x-iso9660-image"
        if path.lower().endswith((".img", ".bin")):
            return "application/octet-stream"
        return super().guess_type(path)


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """A plain socketserver.TCPServer only handles one connection at a
    time. BMCs commonly open multiple connections to stream an ISO
    (parallel byte-range reads), so a single-threaded server makes the
    second connection wait - and if the BMC times that out, it resets the
    connection, which looks exactly like intermittent mount/read failures.
    """
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        # The default implementation dumps a full traceback to stderr for
        # every dropped connection, which is extremely common and benign
        # for HTTP clients (range-request probing, early disconnects,
        # etc.) - log one line instead so real problems aren't buried in
        # noise from normal client behavior.
        import sys
        exc = sys.exc_info()[1]
        print(f"[VirtualMedia HTTPS] {client_address}: {exc!r}")


class LocalFileServer:
    """Spins up a local HTTPS server (self-signed cert) serving one
    directory, for hosting a local ISO/IMG so the BMC can fetch it."""

    def __init__(self, directory, local_ip):
        self.directory = directory
        self.local_ip = local_ip

        self.workspace = tempfile.mkdtemp()
        self.cert_path = os.path.join(self.workspace, "cert.pem")
        self.key_path = os.path.join(self.workspace, "key.pem")

        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", self.key_path, "-out", self.cert_path,
                "-days", "1", "-nodes", "-subj", f"/CN={self.local_ip}",
                "-addext", f"subjectAltName=IP:{self.local_ip}",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        handler = lambda *args, **kwargs: _StreamingHTTPHandler(*args, directory=self.directory, **kwargs)

        self.port = 8443
        while self.port < 8500:
            try:
                self.httpd = _ThreadingHTTPServer(("", self.port), handler)
                break
            except OSError:
                self.port += 1

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=self.cert_path, keyfile=self.key_path)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)

        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()


def _pretty(obj):
    try:
        return json.dumps(obj, indent=2)
    except Exception:
        return str(obj)


class VirtualMediaPanel(ctk.CTkFrame):
    def __init__(self, master, get_bmc_ip, get_username=None, get_password=None,
                 log=None, **kwargs):
        super().__init__(master, **kwargs)
        self.get_bmc_ip = get_bmc_ip
        self.get_username = get_username
        self.get_password = get_password
        self.log = log or (lambda msg: None)

        self.slots = []          # list of slot dicts from bmc.get_virtual_media_slots
        self.local_server = None  # active LocalFileServer, if any

        self._build_ui()

    def _creds(self):
        bmc_ip = self.get_bmc_ip() if self.get_bmc_ip else None
        user = self.get_username() if self.get_username else None
        password = self.get_password() if self.get_password else None
        if not bmc_ip or not user or not password:
            self._status("Set BMC IP, username, and password first.", "#e74c3c")
            return None
        return user, password, bmc_ip

    def _run(self, fn, busy_text, on_done=None):
        self._status(busy_text, "gray")

        def _worker():
            try:
                result = fn()
                self.after(0, lambda: self._on_success(result, on_done))
            except Exception as e:
                # Capture into a plain variable first - see the matching
                # note in inventory_panel.py for why the lambda can't
                # reference `e` directly here.
                error_msg = str(e)
                self.after(0, lambda: self._status(f"Error: {error_msg}", "#e74c3c"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_success(self, result, on_done):
        if on_done:
            on_done(result)
        else:
            self._status(str(result), "#2ecc71")

    def _status(self, text, color="gray"):
        self.status_label.configure(text=text, text_color=color)

    # ---- UI ----

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkLabel(header, text="Virtual Media", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.status_label = ctk.CTkLabel(header, text="Not loaded", text_color="gray")
        self.status_label.pack(side="right", padx=(0, 4))

        # --- Slot discovery/selection ---
        slot_row = ctk.CTkFrame(self, fg_color="transparent")
        slot_row.pack(fill="x", padx=4, pady=2)
        ctk.CTkButton(slot_row, text="Discover Slots", width=110, command=self.discover_slots).pack(side="left", padx=(0, 6))
        self.slot_menu = ctk.CTkOptionMenu(slot_row, values=["No slots found"], command=self._on_slot_selected, width=200)
        self.slot_menu.pack(side="left", padx=(0, 6))
        ctk.CTkButton(slot_row, text="Refresh Status", width=110, command=self.refresh_status).pack(side="left")

        # --- Live slot status ---
        status_frame = ctk.CTkFrame(self, fg_color="transparent")
        status_frame.pack(fill="x", padx=4, pady=2)
        self.lbl_connected = ctk.CTkLabel(status_frame, text="Inserted: -")
        self.lbl_connected.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.lbl_media = ctk.CTkLabel(status_frame, text="Image: -")
        self.lbl_media.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self.lbl_wp = ctk.CTkLabel(status_frame, text="Write Protected: -")
        self.lbl_wp.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(2, 0))
        self.lbl_types = ctk.CTkLabel(status_frame, text="Media Types: -")
        self.lbl_types.grid(row=1, column=1, sticky="w", pady=(2, 0))

        # --- Image URL + browse/mount/unmount ---
        url_row = ctk.CTkFrame(self, fg_color="transparent")
        url_row.pack(fill="x", padx=4, pady=(6, 2))
        ctk.CTkLabel(url_row, text="Image URL:").pack(side="left", padx=(0, 6))
        self.image_url_entry = ctk.CTkEntry(url_row, placeholder_text="https://... (or Browse a local file)")
        self.image_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(url_row, text="Browse...", width=90, command=self.browse_and_host).pack(side="left")

        action_row = ctk.CTkFrame(self, fg_color="transparent")
        action_row.pack(fill="x", padx=4, pady=(2, 4))
        ctk.CTkButton(
            action_row, text="Mount", width=90, command=self.mount,
            fg_color="#2e7d32", hover_color="#388e3c",
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            action_row, text="Unmount", width=90, command=self.unmount,
            fg_color="#a94442", hover_color="#c9302c",
        ).pack(side="left")

        if not RANGE_SUPPORT:
            ctk.CTkLabel(
                self,
                text="Note: RangeHTTPServer not installed - locally hosted images won't support byte-range "
                     "reads some BMCs need. pip install RangeHTTPServer",
                text_color="gray", wraplength=520, justify="left",
            ).pack(fill="x", padx=4, pady=(0, 4))

    # ---- slot discovery/status ----

    def discover_slots(self):
        creds = self._creds()
        if creds is None:
            return
        user, password, bmc_ip = creds
        self._run(
            lambda: bmc.get_virtual_media_slots(user, password, bmc_ip),
            "Discovering Virtual Media slots...",
            on_done=self._on_slots_discovered,
        )

    def _on_slots_discovered(self, slots):
        self.slots = slots
        if slots:
            names = [s["name"] for s in slots]
            self.slot_menu.configure(values=names)
            self.slot_menu.set(names[0])
            self._status(f"Found {len(slots)} slot(s).", "#2ecc71")
            self._render_status(slots[0])
        else:
            self.slot_menu.configure(values=["No slots found"])
            self.slot_menu.set("No slots found")
            self._status("No Virtual Media slots found.", "#e74c3c")

    def _current_slot(self):
        if not self.slots:
            return None
        name = self.slot_menu.get()
        return next((s for s in self.slots if s["name"] == name), None)

    def _on_slot_selected(self, _value):
        self.refresh_status()

    def refresh_status(self):
        slot = self._current_slot()
        if not slot:
            self._status("Discover slots first.", "#e74c3c")
            return
        creds = self._creds()
        if creds is None:
            return
        user, password, bmc_ip = creds
        self._run(
            lambda: bmc.get_virtual_media_status(user, password, bmc_ip, slot["endpoint"]),
            "Refreshing status...",
            on_done=self._render_status,
        )

    def _render_status(self, status):
        # Keep the slot list's own record in sync too.
        for i, s in enumerate(self.slots):
            if s["endpoint"] == status.get("endpoint"):
                self.slots[i] = status
                break

        self.lbl_connected.configure(text=f"Inserted: {status.get('inserted')}")
        self.lbl_media.configure(text=f"Image: {status.get('image') or 'None'}")
        self.lbl_wp.configure(text=f"Write Protected: {status.get('write_protected')}")
        types = status.get("media_types") or []
        self.lbl_types.configure(text=f"Media Types: {', '.join(types) if types else '-'}")
        self._status("Status updated.", "#2ecc71")

    # ---- mount/unmount ----

    def browse_and_host(self):
        from tkinter import filedialog

        if not RANGE_SUPPORT:
            self._status("RangeHTTPServer not installed - hosting anyway, but some BMCs may need it.", "gray")

        file_path = filedialog.askopenfilename(
            title="Select Virtual Media Disk Image",
            filetypes=[("Disk Images", "*.iso *.img"), ("All Files", "*.*")],
        )
        if not file_path:
            return

        if self.local_server:
            self.local_server.stop()
            self.local_server = None

        directory = os.path.dirname(file_path)
        filename = os.path.basename(file_path)
        local_ip = _get_local_ip()

        self.local_server = LocalFileServer(directory, local_ip)
        auto_url = f"https://{local_ip}:{self.local_server.port}/{filename}"
        self.image_url_entry.delete(0, "end")
        self.image_url_entry.insert(0, auto_url)

        creds = self._creds()
        if creds is None:
            return
        user, password, bmc_ip = creds
        with open(self.local_server.cert_path, "r") as f:
            cert_pem = f.read()
        self._run(
            lambda: bmc.upload_bmc_certificate(user, password, bmc_ip, cert_pem),
            "Uploading HTTPS certificate to BMC so it trusts this host...",
        )

    def mount(self):
        slot = self._current_slot()
        if not slot:
            self._status("Discover and select a slot first.", "#e74c3c")
            return
        image_url = self.image_url_entry.get().strip()
        if not image_url:
            self._status("Enter an image URL or Browse a local file first.", "#e74c3c")
            return
        creds = self._creds()
        if creds is None:
            return
        user, password, bmc_ip = creds
        self._run(
            lambda: bmc.insert_virtual_media(user, password, bmc_ip, slot["endpoint"], image_url),
            f"Mounting {image_url}...",
            on_done=lambda msg: (self._status(msg, "#2ecc71"), self.refresh_status()),
        )

    def unmount(self):
        slot = self._current_slot()
        if not slot:
            self._status("Discover and select a slot first.", "#e74c3c")
            return
        creds = self._creds()
        if creds is None:
            return
        user, password, bmc_ip = creds
        self._run(
            lambda: bmc.eject_virtual_media(user, password, bmc_ip, slot["endpoint"]),
            "Unmounting...",
            on_done=lambda msg: (self._status(msg, "#2ecc71"), self.refresh_status()),
        )