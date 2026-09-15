"""
Embedded console panel for Platypus.

Provides an in-window terminal-like panel that can attach to either:
  - the BMC's local debug/serial UART (via pyserial), or
  - a SOL (serial-over-LAN) session reached by SSHing into the BMC
    (matches the manual workflow: `ssh -p 2200 root@<bmc_ip>`)

Only one backend runs at a time. Switching modes cleanly tears down
whichever backend is active and starts the other.

Output is rendered through a real VT100/ANSI terminal emulator (pyte), not
just appended as text. This matters for anything that redraws itself in
place using cursor-positioning escape codes - BIOS/UEFI setup menus, u-boot
menus, vi, top, etc. Without real cursor tracking, each redrawn "frame"
would just get appended below the last one instead of overwriting it in
place, which is exactly the "everything stacks" symptom this fixes.

The terminal is a fixed-size grid (see SOL_TERM_COLUMNS/SOL_TERM_ROWS)
rather than an infinitely scrolling log - once content scrolls off the top
of that grid it's gone, the same way it would be in a fixed-size real
terminal without a separate scrollback buffer.
"""

import codecs
import queue
import re
import socket
import threading
import tkinter as tk

import customtkinter as ctk
import paramiko
import pyte
import serial

import bmc


# Fixed terminal size used for both SOL (negotiated with the BMC via SSH pty
# request) and the local pyte emulator. Keeping these identical is what
# keeps cursor-addressed output aligned - if the remote thinks the terminal
# is a different size than what we're actually rendering, positioning goes
# wrong.
#
# 100x31 is AMI Aptio's "Extended" console-redirection resolution - one of
# the two standard options AMI's own BIOS setup offers (the other being
# 80x24). This was previously set to the generic VT100 default of 80x25,
# but that was too short for this specific BIOS: giving it fewer rows than
# its own layout assumes pushed the top menu bar out of the visible area,
# since AMI positions its fixed top bar/footer based on its own idea of
# total screen height, not something it renegotiates with us. The wide,
# sidebar-heavy layout seen in practice matches the 100-column Extended
# mode much better than plain 80 columns too.
SOL_TERM_COLUMNS = 100
SOL_TERM_ROWS = 31


# Named-color palette pyte uses for the basic 16 ANSI colors (codes
# 30-37/90-97 fg, 40-47/100-107 bg). 256-color and truecolor codes come back
# from pyte as a literal 6-hex-digit string instead, handled separately.
_NAMED_COLORS = {
    "black": "#000000", "red": "#cd0000", "green": "#00cd00",
    "brown": "#cdcd00", "yellow": "#cdcd00",
    "blue": "#0000ee", "magenta": "#cd00cd", "cyan": "#00cdcd", "white": "#e5e5e5",
    "brightblack": "#7f7f7f", "brightred": "#ff0000", "brightgreen": "#00ff00",
    "brightbrown": "#ffff00", "brightyellow": "#ffff00",
    "brightblue": "#5c5cff", "brightmagenta": "#ff00ff", "brightcyan": "#00ffff",
    "brightwhite": "#ffffff",
}

_HEX_DIGITS = set("0123456789abcdefABCDEF")

# Represents SGR "bold" as a brighter color instead of an actual bold font
# weight. A real bold font face is almost always measurably wider per
# character than its regular weight even at the identical point size
# (including Courier) - mixing the two within a monospace terminal grid
# breaks strict column alignment, which is exactly what produces
# misaligned/overlapping-looking text when a screen mixes bold headers
# with regular text (e.g. BIOS menus). Real terminal emulators commonly
# render bold as brightness for this same reason.
_BOLD_BRIGHTEN = {
    _NAMED_COLORS["black"]: _NAMED_COLORS["brightblack"],
    _NAMED_COLORS["red"]: _NAMED_COLORS["brightred"],
    _NAMED_COLORS["green"]: _NAMED_COLORS["brightgreen"],
    _NAMED_COLORS["brown"]: _NAMED_COLORS["brightyellow"],
    _NAMED_COLORS["blue"]: _NAMED_COLORS["brightblue"],
    _NAMED_COLORS["magenta"]: _NAMED_COLORS["brightmagenta"],
    _NAMED_COLORS["cyan"]: _NAMED_COLORS["brightcyan"],
    _NAMED_COLORS["white"]: _NAMED_COLORS["brightwhite"],
}


def _bold_color(hex_color, default_fg):
    if hex_color == default_fg:
        return "#ffffff"
    return _BOLD_BRIGHTEN.get(hex_color, hex_color)


def _pyte_color_to_hex(value, default):
    """Convert a pyte Char.fg/bg value ('default', a named color like 'red',
    or a bare 6-hex-digit string for 256-color/truecolor) to a #rrggbb
    string, falling back to `default` when there's no explicit color."""
    if not value or value == "default":
        return default
    if len(value) == 6 and all(c in _HEX_DIGITS for c in value):
        return f"#{value}"
    return _NAMED_COLORS.get(value, default)


def _is_light(hex_color):
    """Rough perceived-luminance check, used only to decide which way to
    nudge a foreground/background color collision (see _tag_for)."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) > 140


class _ReplyScreen(pyte.Screen):
    """
    pyte.Screen already parses terminal query sequences correctly (Device
    Attributes "ESC[c", Cursor Position Report "ESC[6n", etc.) and computes
    the right reply - it just doesn't have any channel to actually send
    that reply anywhere by default (write_process_input() is a no-op).
    Some interactive full-screen programs query the terminal this way and
    can hang or fall back to a degraded rendering mode without a reply, so
    this routes it back out to whatever backend is currently connected.
    """

    def __init__(self, *args, on_reply=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_reply = on_reply

    def write_process_input(self, data):
        if self._on_reply:
            self._on_reply(data)


class TerminalView:
    """
    Renders a fixed-size VT100 terminal (via pyte) into a CTkTextbox.

    Feeds raw output (including cursor movement, clear-screen, colors, etc.)
    into a pyte Screen/Stream, then redraws only the rows pyte marks dirty
    into the Tk widget - so full-screen redraws (BIOS menus, vi, top) land
    in the right place instead of piling up underneath the previous frame.

    Escape sequences can be split across separate feed() calls (e.g. when
    they straddle a serial read boundary); pyte's Stream already buffers
    partial sequences internally, so no extra handling is needed here.
    """

    DEFAULT_FG = "#e5e5e5"
    FONT_SIZE = 15

    def __init__(self, textbox, columns=SOL_TERM_COLUMNS, rows=SOL_TERM_ROWS,
                 widget_bg="#1e1e1e", on_reply=None):
        self.textbox = textbox
        # Tags, cursor add/remove, and line indexing all need to operate on
        # the real underlying tkinter.Text widget, not the CTkTextbox
        # wrapper (CTkTextbox.insert() forwards here too, so addressing is
        # consistent either way).
        self._raw_textbox = getattr(textbox, "_textbox", textbox)
        self.columns = columns
        self.rows = rows
        # The widget's actual current background, used only to catch a
        # foreground/background color collision that would otherwise render
        # genuinely invisible text (see _tag_for). Kept in sync via set_bg()
        # whenever the panel switches modes/background color.
        self.widget_bg = widget_bg
        self.screen = _ReplyScreen(columns, rows, on_reply=on_reply)
        self.stream = pyte.Stream(self.screen)
        self._known_tags = set()
        # A solid block, always readable regardless of the surrounding
        # color scheme (dark Serial background or blue "BIOS" SOL
        # background) - a classic reverse-video terminal cursor look.
        self._raw_textbox.tag_configure("cursor", foreground="#000000", background="#ffffff")
        self._last_cursor_index = None
        self._redraw(force=True)
        self._update_cursor()

    def set_bg(self, color):
        """Update the known widget background and force a full redraw, so
        the invisible-text safeguard in _tag_for stays accurate after a
        mode switch changes the console's background color."""
        self.widget_bg = color
        self._redraw(force=True)

    def reset(self):
        """Clear the terminal (used by the panel's Clear button and on a
        fresh connection) so leftover content/state doesn't bleed into the
        next session."""
        self.screen.reset()
        self._raw_textbox.delete("1.0", "end")
        self._last_cursor_index = None
        self._redraw(force=True)

    def feed(self, data: str):
        self.stream.feed(data)
        self._redraw()
        # Cursor position can change (arrow keys, Home/End, etc.) without
        # any character content changing, so pyte won't mark a row dirty
        # for that - the cursor has to be tracked independently of the
        # dirty-row content redraw, every feed, not just when something
        # was actually drawn.
        self._update_cursor()

    def _update_cursor(self):
        if self._last_cursor_index is not None:
            old_row, old_col = self._last_cursor_index
            try:
                self._raw_textbox.tag_remove("cursor", f"{old_row + 1}.{old_col}", f"{old_row + 1}.{old_col + 1}")
            except Exception:
                pass
            self._last_cursor_index = None

        cursor = self.screen.cursor
        if cursor.hidden:
            return  # Many BIOS/UEFI menus hide the blinking cursor and draw
                     # their own row highlight instead - respect that.
        row, col = cursor.y, cursor.x
        if 0 <= row < self.rows and 0 <= col < self.columns:
            try:
                self._raw_textbox.tag_add("cursor", f"{row + 1}.{col}", f"{row + 1}.{col + 1}")
                self._raw_textbox.tag_raise("cursor")
                self._last_cursor_index = (row, col)
            except Exception:
                pass

    def _tag_for(self, char):
        fg = _pyte_color_to_hex(char.fg, self.DEFAULT_FG)
        bg = _pyte_color_to_hex(char.bg, None)
        if char.reverse:
            fg, bg = (bg or "#000000"), (fg)

        if char.bold:
            fg = _bold_color(fg, self.DEFAULT_FG)

        # Minimum-contrast safeguard: some remote consoles produce a
        # degenerate same-color state for a "highlighted" cell (e.g. only
        # changing the background and leaving foreground as whatever it
        # already was), which would otherwise render completely invisible
        # text instead of a visible highlight. Nudge the foreground to
        # guarantee it's never literally the same color as what it sits on.
        # Checked last so it also catches a collision introduced by the
        # bold-brighten step above.
        effective_bg = bg if bg is not None else self.widget_bg
        if fg.lower() == effective_bg.lower():
            fg = "#000000" if _is_light(effective_bg) else "#ffffff"

        # Font is never varied per-character (no bold font weight, ever) -
        # every character uses the exact same font at the exact same size,
        # set once on the widget itself. This is what keeps the monospace
        # column grid strictly aligned; see _BOLD_BRIGHTEN above for why.
        name = f"pt_{fg}_{bg}_{int(char.underscore)}"
        if name not in self._known_tags:
            opts = {"foreground": fg}
            if bg:
                opts["background"] = bg
            if char.underscore:
                opts["underline"] = True
            self._raw_textbox.tag_configure(name, **opts)
            self._known_tags.add(name)
        return name

    def _ensure_line_count(self, target_lines):
        """Tk Text widgets always have at least one line; pad with blank
        lines until the widget has at least `target_lines` of them so
        row-indexed addressing ("N.0", "N.end") stays valid."""
        total = int(self._raw_textbox.index("end-1c").split(".")[0])
        if total < target_lines:
            self._raw_textbox.insert("end", "\n" * (target_lines - total))

    def _redraw(self, force=False):
        dirty = set(range(self.rows)) if force else self.screen.dirty
        if not dirty:
            return
        self._ensure_line_count(self.rows)

        buf = self.screen.buffer
        for row in sorted(dirty):
            line_no = row + 1  # Tk Text lines are 1-indexed
            self._raw_textbox.delete(f"{line_no}.0", f"{line_no}.end")

            line = buf[row]
            run_text, run_tag = "", None
            for col in range(self.columns):
                ch = line[col]
                tag = self._tag_for(ch)
                data = ch.data or " "
                if tag != run_tag and run_text:
                    self._raw_textbox.insert(f"{line_no}.end", run_text, run_tag)
                    run_text = ""
                run_tag = tag
                run_text += data
            if run_text:
                self._raw_textbox.insert(f"{line_no}.end", run_text, run_tag)

        self.screen.dirty.clear()
        # Pin the view to the top, not the bottom: this is a fixed N-row
        # terminal grid, not a growing scrollback log, so there's no "end"
        # to scroll toward in the first place. Calling see("end") here (as
        # if this were a log) scrolled the viewport to the last row of the
        # fixed grid on every redraw, which - if the panel isn't tall
        # enough at the current font size to show all rows at once - cut
        # the top of the screen out of view instead of the bottom.
        self.textbox.see("1.0")


class SerialBackend:
    """Reads/writes a local serial device (the BMC's debug UART)."""

    def __init__(self, device, baudrate=115200):
        self.device = device
        self.baudrate = baudrate
        self.ser = None
        self._stop = threading.Event()
        self._thread = None
        # Incremental, not one-shot-per-chunk: a multi-byte UTF-8 character
        # can easily land split across two separate reads (bytes trickle in
        # over serial rather than arriving as a whole), and decoding each
        # chunk independently would corrupt exactly those characters - which
        # is what BIOS/UEFI box-drawing borders are made of. An incremental
        # decoder buffers a trailing partial sequence until the rest arrives.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def start(self, on_data, on_error):
        if not hasattr(serial, "Serial"):
            # Common gotcha: PyPI has two different packages that both
            # import as `serial` - "pyserial" (what this app needs) and an
            # unrelated package literally called "serial". If the wrong one
            # got installed, `serial.Serial` doesn't exist and every open
            # fails with a confusing AttributeError instead of a clear
            # "wrong package" message.
            on_error(
                "The installed 'serial' package is not pyserial (it has no "
                "Serial class). Fix with:\n"
                "  pip uninstall serial\n"
                "  pip install pyserial"
            )
            return False
        try:
            self.ser = serial.Serial(self.device, baudrate=self.baudrate, timeout=0.2)
            self.ser.dtr = True
        except Exception as e:
            on_error(f"Failed to open {self.device}: {e}")
            return False

        def _reader():
            while not self._stop.is_set():
                try:
                    if self.ser.in_waiting:
                        chunk = self.ser.read(self.ser.in_waiting)
                        if chunk:
                            on_data(self._decoder.decode(chunk))
                except Exception as e:
                    on_error(f"Serial read error: {e}")
                    return
                self._stop.wait(0.05)

        self._thread = threading.Thread(target=_reader, daemon=True)
        self._thread.start()
        return True

    def write(self, data: str):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(data.encode('utf-8'))
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self.ser:
            try:
                if self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass


class SolBackend:
    """
    Opens a SOL session to the BMC over SSH (port 2200) using paramiko,
    matching the manual `ssh -p 2200 root@<bmc_ip>` workflow but
    authenticating with the real SSH password auth exchange instead of
    screen-scraping for a "password:" prompt.

    Requests a fixed-size vt100 shell (see SOL_TERM_COLUMNS/SOL_TERM_ROWS)
    so the remote's idea of the terminal size matches what's actually
    displayed here, avoiding wrapped/distorted output.
    """

    def __init__(self, bmc_ip, port=2200, user="root", get_password=None):
        self.bmc_ip = bmc_ip
        self.port = port
        self.user = user
        self.get_password = get_password

        self._stop = threading.Event()
        self._thread = None
        self._outgoing = queue.Queue(maxsize=256)

        self._client = None
        self._channel = None

    def start(self, on_data, on_error):
        if not self.bmc_ip:
            on_error("No BMC IP set - cannot start SOL session.")
            return False

        password = self.get_password() if self.get_password else ""

        def _run():
            try:
                self._client = paramiko.SSHClient()
                # BMC units get re-flashed/re-imaged frequently and commonly
                # reuse IPs across different physical boards, so their host
                # key won't be known (or will keep changing) on this
                # machine - accept it automatically rather than prompting.
                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                self._client.connect(
                    hostname=self.bmc_ip,
                    port=self.port,
                    username=self.user,
                    password=password,
                    timeout=10,
                    banner_timeout=10,
                    auth_timeout=10,
                    look_for_keys=False,
                    allow_agent=False,
                )

                # Fixed terminal size so the remote's output matches what
                # the console panel actually displays (no unexpected
                # wrapping/reflow from a mismatched column count).
                #
                # "xterm-256color", not "vt100": our renderer already fully
                # supports 256-color, truecolor, bold, and underline (see
                # TerminalView) - claiming plain vt100 tells the remote
                # shell/ncurses apps to degrade to monochrome, throwing away
                # capability we actually have.
                self._channel = self._client.invoke_shell(
                    term="xterm-256color",
                    width=SOL_TERM_COLUMNS,
                    height=SOL_TERM_ROWS,
                )
                self._channel.settimeout(0.1)

                # Incremental UTF-8: this BIOS/BMC's console genuinely
                # emits real UTF-8 box-drawing characters (confirmed by
                # testing - decoding those bytes as cp437 instead produces
                # exactly the mojibake seen when that was tried), and
                # decoding incrementally (not per-chunk) avoids corrupting
                # a multi-byte character that lands split across two reads.
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

                while not self._stop.is_set():
                    # Flush any queued keyboard input first.
                    for _ in range(32):
                        try:
                            payload = self._outgoing.get_nowait()
                        except queue.Empty:
                            break
                        self._channel.sendall(payload.encode("utf-8"))

                    if self._channel.recv_ready():
                        data = self._channel.recv(65536)
                        if not data:
                            break
                        text = decoder.decode(data)
                        if text:
                            on_data(text)
                    else:
                        self._stop.wait(0.02)

            except (paramiko.AuthenticationException, paramiko.SSHException,
                    socket.timeout, OSError) as e:
                on_error(f"SOL connection failed for {self.bmc_ip}: {e}")
            except Exception as e:
                on_error(f"SOL session error: {e}")
            finally:
                if self._channel is not None:
                    try:
                        self._channel.close()
                    except OSError:
                        pass
                if self._client is not None:
                    try:
                        self._client.close()
                    except OSError:
                        pass
                self._channel = None
                self._client = None

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return True

    def write(self, data: str):
        try:
            self._outgoing.put_nowait(data)
        except queue.Full:
            pass

    def stop(self):
        self._stop.set()
        if self._channel is not None:
            try:
                self._channel.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)


class EmbeddedConsole(ctk.CTkFrame):
    """
    Right-hand console panel with a Serial/SOL switch. Only one backend is
    ever active; flipping the switch tears down the old one and (if you were
    already connected) reconnects using the new backend.
    """

    MODE_SERIAL = "Serial"
    MODE_SOL = "SOL"

    # Classic "BIOS blue" for SOL, since that's exactly the kind of screen
    # (BIOS/UEFI setup, u-boot) SOL usually shows; Serial keeps the plain
    # dark terminal look.
    _BG_FOR_MODE = {
        MODE_SERIAL: "#1e1e1e",
        MODE_SOL: "#0000aa",
    }

    # Boot targets offered via Redfish BootSourceOverrideTarget, matching
    # the standalone Redfish control panel's Power tab.
    BOOT_TARGETS = ["None", "Pxe", "Cd", "Usb", "Hdd", "BiosSetup", "Utilities", "Diags", "SDCard"]

    def __init__(self, master, get_serial_device, get_bmc_ip, log=None,
                 sol_port=2200, sol_user="root", get_password=None,
                 get_username=None, **kwargs):
        super().__init__(master, **kwargs)
        self.get_serial_device = get_serial_device
        self.get_bmc_ip = get_bmc_ip
        self.get_password = get_password
        self.get_username = get_username
        self.log = log or (lambda msg: None)
        self.sol_port = sol_port
        self.sol_user = sol_user

        self.mode = self.MODE_SERIAL
        self.backend = None
        self._queue = queue.Queue()

        self._build_ui()
        self._term = TerminalView(
            self.output, widget_bg=self._BG_FOR_MODE[self.mode],
            on_reply=self._write_to_backend,
        )
        self._poll_queue()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 2))

        self.mode_switch = ctk.CTkSegmentedButton(
            header,
            values=[self.MODE_SERIAL, self.MODE_SOL],
            command=self._on_mode_selected,
        )
        self.mode_switch.set(self.MODE_SERIAL)
        self.mode_switch.pack(side="left", padx=(0, 8))

        self.status_label = ctk.CTkLabel(header, text="Disconnected", text_color="gray")
        self.status_label.pack(side="left", padx=(4, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=4, pady=(0, 2))
        ctk.CTkButton(btn_frame, text="Connect", width=80, command=self.connect).pack(side="left", padx=2)
        ctk.CTkButton(btn_frame, text="Disconnect", width=90, command=self.disconnect).pack(side="left", padx=2)
        ctk.CTkButton(btn_frame, text="Paste", width=60, command=self._paste_clipboard).pack(side="left", padx=2)
        ctk.CTkButton(btn_frame, text="Clear", width=60, command=self.clear).pack(side="left", padx=2)

        # Redfish boot-order/power controls - only meaningful (and only
        # enabled) once a SOL session is actually connected, since that's
        # when you'd want to watch the BIOS/OS respond to a boot-target
        # change or power action.
        self.boot_target_menu = ctk.CTkOptionMenu(
            btn_frame, values=self.BOOT_TARGETS, width=110,
            command=self._on_boot_target_selected, state="disabled",
        )
        self.boot_target_menu.set("Next Boot")
        self.boot_target_menu.pack(side="left", padx=(10, 2))

        self.power_on_btn = ctk.CTkButton(
            btn_frame, text="Power On", width=85,
            command=self._on_power_on_click, state="disabled",
            fg_color="#2e7d32", hover_color="#388e3c",
        )
        self.power_on_btn.pack(side="left", padx=2)

        self.power_off_btn = ctk.CTkButton(
            btn_frame, text="Power Off", width=85,
            command=self._on_power_off_click, state="disabled",
            fg_color="#a94442", hover_color="#c9302c",
        )
        self.power_off_btn.pack(side="left", padx=2)

        self.reboot_btn = ctk.CTkButton(
            btn_frame, text="Reboot", width=75,
            command=self._on_reboot_click, state="disabled",
            fg_color="#a94442", hover_color="#c9302c",
        )
        self.reboot_btn.pack(side="left", padx=2)

        # Container so the output textbox and its horizontal scrollbar
        # stack correctly; wrap="none" is intentional and important - a
        # fixed 100-column terminal grid must never be word-wrapped by the
        # widget itself (that would scramble the alignment the remote
        # already computed for that exact column count), so instead it
        # scrolls horizontally when the panel isn't wide enough to show
        # every column at once.
        output_container = ctk.CTkFrame(self, fg_color="transparent")
        output_container.pack(fill="both", expand=True, padx=4, pady=(2, 0))

        self.output = ctk.CTkTextbox(
            output_container, wrap="none", font=("Courier", TerminalView.FONT_SIZE),
            fg_color=self._BG_FOR_MODE[self.mode], text_color="#e5e5e5",
        )
        self.output.pack(fill="both", expand=True)

        raw_output = getattr(self.output, "_textbox", self.output)
        h_scroll = tk.Scrollbar(output_container, orient="horizontal", command=raw_output.xview)
        h_scroll.pack(fill="x")
        raw_output.configure(xscrollcommand=h_scroll.set)

        # Keyboard is captured directly on the output pane once connected -
        # click into it and just type, like a real terminal. No separate
        # input line or Send button needed. Ctrl+V / Shift+Insert / middle
        # click all paste clipboard text straight into the session.
        self.output.bind("<Key>", self._on_keypress)
        self.output.bind("<Button-2>", self._on_middle_click)

        self.hint_label = ctk.CTkLabel(
            self,
            text="Click in the console and type directly, or paste with Ctrl+V.",
            text_color="gray",
            font=("Courier", 10),
        )
        self.hint_label.pack(fill="x", padx=4, pady=(0, 4))

    # ---- keyboard passthrough ----

    # Keysyms that map to a fixed escape/control sequence rather than a
    # printable character. Paste (Ctrl+V / Shift+Insert) is handled
    # separately, before this table is consulted.
    _SPECIAL_KEYSYMS = {
        "Return": "\r",
        "KP_Enter": "\r",
        "BackSpace": "\x7f",
        "Tab": "\t",
        "ISO_Left_Tab": "\x1b[Z",   # Shift+Tab on most Linux layouts
        "Escape": "\x1b",
        "Up": "\x1b[A",
        "Down": "\x1b[B",
        "Right": "\x1b[C",
        "Left": "\x1b[D",
        "Home": "\x1b[H",
        "End": "\x1b[F",
        "Prior": "\x1b[5~",         # Page Up
        "Next": "\x1b[6~",          # Page Down
        "Delete": "\x1b[3~",
        "Insert": "\x1b[2~",
    }

    _BARE_MODIFIERS = {
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Caps_Lock", "Num_Lock", "Super_L", "Super_R", "Menu",
    }

    def _on_keypress(self, event):
        """Forward every keystroke typed into the console straight to the
        active backend (serial or SOL), instead of requiring an input box
        and a Send button. Always returns 'break' so the Textbox itself
        never inserts characters locally - what you see typed is the
        remote echoing it back, exactly like a real terminal."""
        if self.backend is None:
            return "break"

        keysym = event.keysym
        is_ctrl = bool(event.state & 0x4)
        is_shift = bool(event.state & 0x1)

        # Paste: Ctrl+V or Shift+Insert (common on Linux terminals)
        if (is_ctrl and keysym.lower() == "v") or (keysym == "Insert" and is_shift):
            self._paste_clipboard()
            return "break"

        if keysym in self._BARE_MODIFIERS:
            return "break"

        data = self._SPECIAL_KEYSYMS.get(keysym)
        if data is None:
            # Covers normal typing as well as most Ctrl+<letter> combos -
            # X11 already resolves those to the right control byte
            # (e.g. Ctrl+C -> event.char == '\x03').
            data = event.char or None

        if data:
            self.backend.write(data)

        return "break"

    def _on_middle_click(self, event):
        """X11 convention: middle-click pastes the current PRIMARY selection."""
        if self.backend is not None:
            try:
                text = self.output.selection_get(selection="PRIMARY")
            except Exception:
                text = ""
            if text:
                self.backend.write(text)
        return "break"

    def _paste_clipboard(self):
        if self.backend is None:
            self._status("Not connected.", "#e74c3c")
            return
        try:
            text = self.clipboard_get()
        except Exception:
            return
        if text:
            self.backend.write(text)

    # ---- mode / lifecycle ----

    def _on_mode_selected(self, value):
        if value == self.mode:
            return
        was_connected = self.backend is not None
        self.disconnect()
        self.mode = value
        self.output.configure(fg_color=self._BG_FOR_MODE[value])
        self._term.set_bg(self._BG_FOR_MODE[value])
        if was_connected:
            self.connect()
        else:
            self._status(f"Switched to {value} - click Connect", "gray")

    def connect(self):
        self.disconnect()  # ensure a clean slate before (re)connecting

        if self.mode == self.MODE_SERIAL:
            device = self.get_serial_device()
            if not device:
                self._status("No serial device selected.", "#e74c3c")
                return
            self.backend = SerialBackend(device)
            ok = self.backend.start(self._on_data, self._on_error)
            label = f"Serial: {device}"
        else:
            bmc_ip = self.get_bmc_ip()
            if not bmc_ip:
                self._status("No BMC IP set.", "#e74c3c")
                return
            self.backend = SolBackend(
                bmc_ip, port=self.sol_port, user=self.sol_user,
                get_password=self.get_password,
            )
            ok = self.backend.start(self._on_data, self._on_error)
            label = f"SOL: {self.sol_user}@{bmc_ip}:{self.sol_port}"

        if ok:
            self._term.reset()
            self._status(f"Connected - {label}", "#2ecc71")
            self.log(f"Console connected: {label}")
            self.output.focus_set()
            self._set_redfish_controls_enabled(self.mode == self.MODE_SOL)
        else:
            self.backend = None
            self._status("Connection failed", "#e74c3c")
            self._set_redfish_controls_enabled(False)

    def disconnect(self):
        if self.backend:
            self.backend.stop()
            self.backend = None
            self._status("Disconnected", "gray")
        self._set_redfish_controls_enabled(False)

    def _write_to_backend(self, data):
        """Stable target for TerminalView's terminal-query replies (Device
        Attributes, Cursor Position Report, etc.) - always dispatches to
        whichever backend is currently connected, so TerminalView doesn't
        need updating every time the backend object itself changes."""
        if self.backend:
            self.backend.write(data)

    def _set_redfish_controls_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.boot_target_menu.configure(state=state)
        self.power_on_btn.configure(state=state)
        self.power_off_btn.configure(state=state)
        self.reboot_btn.configure(state=state)

    # ---- Redfish boot-order / power actions ----

    def _redfish_credentials(self):
        bmc_ip = self.get_bmc_ip() if self.get_bmc_ip else None
        user = self.get_username() if self.get_username else None
        password = self.get_password() if self.get_password else None
        if not bmc_ip or not user or not password:
            self._status("Set BMC IP, username, and password first.", "#e74c3c")
            return None
        return user, password, bmc_ip

    def _run_redfish_action(self, fn, busy_text):
        """Runs a blocking Redfish call (fn takes no args, returns a status
        string, or raises) on a background thread so the GUI never blocks,
        then reports the result via the status label."""
        self._status(busy_text, "gray")

        def _worker():
            try:
                message = fn()
                self.after(0, lambda: self._status(message, "#2ecc71"))
            except Exception as e:
                self.after(0, lambda: self._status(f"Redfish error: {e}", "#e74c3c"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_boot_target_selected(self, target):
        creds = self._redfish_credentials()
        if creds is None:
            return
        user, password, bmc_ip = creds
        enabled_mode = "Disabled" if target == "None" else "Once"
        self._run_redfish_action(
            lambda: bmc.set_boot_override(user, password, bmc_ip, target, enabled_mode),
            f"Setting next boot to {target}...",
        )

    def _on_power_on_click(self):
        creds = self._redfish_credentials()
        if creds is None:
            return
        user, password, bmc_ip = creds
        self._run_redfish_action(
            lambda: bmc.power_on_host(user, password, bmc_ip),
            "Sending power on command...",
        )

    def _on_power_off_click(self):
        creds = self._redfish_credentials()
        if creds is None:
            return
        user, password, bmc_ip = creds
        self._run_redfish_action(
            lambda: bmc.power_off_host(user, password, bmc_ip),
            "Sending power off command...",
        )

    def _on_reboot_click(self):
        creds = self._redfish_credentials()
        if creds is None:
            return
        user, password, bmc_ip = creds
        self._run_redfish_action(
            lambda: bmc.reboot_host(user, password, bmc_ip),
            "Sending reboot command...",
        )

    def clear(self):
        self._term.reset()

    # ---- I/O ----

    def _on_data(self, text):
        self._queue.put(("data", text))

    def _on_error(self, text):
        self._queue.put(("error", text))

    def _poll_queue(self):
        try:
            while True:
                kind, text = self._queue.get_nowait()
                try:
                    if kind == "data":
                        self._term.feed(text)
                    else:
                        # Out-of-band status (e.g. "SOL session ended") -
                        # goes to the status label, not into the terminal
                        # grid, since that grid is row-indexed and only
                        # meant to mirror the remote's actual screen.
                        self._status(text, "#e74c3c")
                except Exception as e:
                    self._status(f"Render error: {e}", "#e74c3c")
        except queue.Empty:
            pass
        # Always reschedule, even if something above went wrong.
        try:
            self.after(75, self._poll_queue)
        except Exception:
            pass

    def _status(self, text, color="gray"):
        self.status_label.configure(text=text, text_color=color)