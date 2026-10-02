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
import inspect
import queue
import re
import socket
import socketserver
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

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


# Color palettes for the 16 basic ANSI colors (codes 30-37/90-97 fg,
# 40-47/100-107 bg). 256-color and truecolor codes come back from pyte as a
# literal 6-hex-digit string instead, handled separately.
#
# XTERM is the familiar Linux terminal look - used for Serial (shells,
# u-boot, kernel logs). VGA is the exact 16-color CGA/VGA text-mode palette
# that BIOS/UEFI setup screens (AMI Aptio etc.) are designed against -
# light-gray-on-blue, cyan highlights - so SOL screens look like the real
# monitor output instead of a garish xterm approximation.
_XTERM_COLORS = {
    "black": "#000000", "red": "#cd0000", "green": "#00cd00",
    "brown": "#cdcd00", "yellow": "#cdcd00",
    "blue": "#3b78ff", "magenta": "#cd00cd", "cyan": "#00cdcd", "white": "#e5e5e5",
    "brightblack": "#7f7f7f", "brightred": "#ff5555", "brightgreen": "#55ff55",
    "brightbrown": "#ffff55", "brightyellow": "#ffff55",
    "brightblue": "#7c9cff", "brightmagenta": "#ff55ff", "brightcyan": "#55ffff",
    "brightwhite": "#ffffff",
}

_VGA_COLORS = {
    "black": "#000000", "red": "#aa0000", "green": "#00aa00",
    "brown": "#aa5500", "yellow": "#aa5500",
    "blue": "#0000aa", "magenta": "#aa00aa", "cyan": "#00aaaa", "white": "#aaaaaa",
    "brightblack": "#555555", "brightred": "#ff5555", "brightgreen": "#55ff55",
    "brightbrown": "#ffff55", "brightyellow": "#ffff55",
    "brightblue": "#5555ff", "brightmagenta": "#ff55ff", "brightcyan": "#55ffff",
    "brightwhite": "#ffffff",
}

# Kept for anything external that imported the old name.
_NAMED_COLORS = _XTERM_COLORS

_HEX_DIGITS = set("0123456789abcdefABCDEF")

# SGR "bold" is shown as the bright variant of the color rather than a bold
# font weight. A bold face is usually measurably wider per character even
# at the same point size, which breaks strict column alignment in a
# monospace grid (BIOS menus mix bold headers with regular text all the
# time). Real terminal emulators commonly render bold as brightness for
# this same reason. Done on color *names* so it works for any palette.
_BRIGHT_NAME = {
    "black": "brightblack", "red": "brightred", "green": "brightgreen",
    "brown": "brightyellow", "yellow": "brightyellow", "blue": "brightblue",
    "magenta": "brightmagenta", "cyan": "brightcyan", "white": "brightwhite",
}


def _pyte_color_to_hex(value, default, palette=_XTERM_COLORS):
    """Convert a pyte Char.fg/bg value ('default', a named color like 'red',
    or a bare 6-hex-digit string for 256-color/truecolor) to a #rrggbb
    string, falling back to `default` when there's no explicit color."""
    if not value or value == "default":
        return default
    if len(value) == 6 and all(c in _HEX_DIGITS for c in value):
        return f"#{value}"
    return palette.get(value, default)


# Monospace fonts in order of preference. "Courier" was the old choice, but
# on most Linux desktops it resolves to Nimbus Mono or a bitmap Courier -
# thin, fuzzy strokes and tall line gaps that break up box-drawing borders.
# These all have hinted outlines, full box-drawing/block glyph coverage,
# and tight line height so BIOS frames join up into solid lines.
_MONO_FONT_PREFERENCE = (
    "DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono", "Noto Sans Mono",
    "Noto Mono", "Hack", "Fira Mono", "Source Code Pro", "Cascadia Mono",
    "Consolas", "Menlo", "Monaco", "Courier New",
)


def _pick_mono_font(root):
    try:
        available = set(tkfont.families(root))
    except Exception:
        available = set()
    for family in _MONO_FONT_PREFERENCE:
        if family in available:
            return family
    return "TkFixedFont"


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

    Also works around a real pyte limitation: its Stream parser blindly
    unpacks every semicolon-separated CSI parameter as a positional
    argument to whichever Screen method handles that sequence (see
    pyte/streams.py's `csi_dispatch[char](*params)`), with no check that
    the target method actually accepts that many. A non-standard sequence
    with extra parameters (e.g. a cursor-down CSI with 3 parameters
    instead of the expected 1) then crashes with a raw TypeError from deep
    inside pyte, taking the whole feed() call - and everything else in it,
    not just the one bad sequence - down with it. __init__ wraps every
    CSI-dispatched method to silently clamp extra positional arguments
    instead.
    """

    def __init__(self, *args, on_reply=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_reply = on_reply
        self._clamp_csi_dispatch_methods()

    def _clamp_csi_dispatch_methods(self):
        from pyte.streams import Stream

        for name in set(Stream.csi.values()):
            original = getattr(self, name, None)
            if not callable(original):
                continue
            try:
                sig = inspect.signature(original)
                # Handlers declared with *args (select_graphic_rendition -
                # i.e. every color/bold/reverse code) accept any number of
                # parameters. Counting positional params gave 0 for those,
                # so the wrapper silently dropped *all* SGR attributes and
                # every screen rendered as plain default-colored text.
                if any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()):
                    continue
                max_positional = sum(
                    1 for p in sig.parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                )
            except (TypeError, ValueError):
                continue

            def _make_wrapper(fn, limit):
                def _wrapper(*args, **kwargs):
                    return fn(*args[:limit], **kwargs)
                return _wrapper

            setattr(self, name, _make_wrapper(original, max_positional))

    def write_process_input(self, data):
        if self._on_reply:
            self._on_reply(data)


class _TermStream(pyte.Stream):
    """pyte Stream that always honors G0/G1 charset switching.

    In UTF-8 mode pyte ignores ESC ( 0 / ESC ) 0 and SI/SO entirely, so a
    BIOS that draws its frames with the VT100 DEC line-drawing set (very
    common - AMI/Insyde in VT100/VT100+ mode) shows them as runs of the
    letters l q k x m j instead of borders. Our backends already decode the
    byte stream to text before it gets here, so the flag only gates charset
    designation - pinning it off is safe, and also ignores a remote
    ESC % G trying to switch it back on."""

    @property
    def use_utf8(self):
        return False

    @use_utf8.setter
    def use_utf8(self, _value):
        pass


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
    FONT_SIZE = 15       # starting size; the panel auto-fits it to its size
    MIN_FONT_SIZE = 7
    MAX_FONT_SIZE = 28

    def __init__(self, textbox, columns=SOL_TERM_COLUMNS, rows=SOL_TERM_ROWS,
                 widget_bg="#1e1e1e", on_reply=None, palette=None, default_fg=None):
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
        self.palette = palette or _XTERM_COLORS
        self.default_fg = default_fg or self.DEFAULT_FG
        self.screen = _ReplyScreen(columns, rows, on_reply=on_reply)
        self.stream = _TermStream(self.screen)
        self._known_tags = set()
        # A solid block, always readable regardless of the surrounding
        # color scheme (dark Serial background or blue "BIOS" SOL
        # background) - a classic reverse-video terminal cursor look.
        self._raw_textbox.tag_configure("cursor", foreground="#000000", background="#ffffff")
        self._last_cursor_index = None
        self._redraw(force=True)
        self._update_cursor()

    def set_bg(self, color, palette=None, default_fg=None):
        """Update the known widget background (and optionally the color
        palette/default text color) and force a full redraw, so colors and
        the invisible-text safeguard in _tag_for stay accurate after a mode
        switch changes the console's look."""
        self.widget_bg = color
        if palette is not None:
            self.palette = palette
        if default_fg is not None:
            self.default_fg = default_fg
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
        fg_name = char.fg
        if char.bold and fg_name in _BRIGHT_NAME:
            fg_name = _BRIGHT_NAME[fg_name]
        fg = _pyte_color_to_hex(fg_name, self.default_fg, self.palette)
        if char.bold and (not char.fg or char.fg == "default"):
            fg = "#ffffff"
        bg = _pyte_color_to_hex(char.bg, None, self.palette)
        if char.reverse:
            # Reverse video with a default background should show the
            # console's own background color as the text color (e.g. blue
            # text on a gray bar in BIOS), not plain black.
            fg, bg = (bg or self.widget_bg), fg

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
        # column grid strictly aligned; see _BRIGHT_NAME above for why.
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
    Opens a SOL session to the BMC over SSH using paramiko, authenticating
    with the real SSH password auth exchange (not screen-scraping for a
    "password:" prompt).

    Two flavors share this class, distinguished by port/initial_command:
      - SOL: port 2200, no initial command - matches the manual
        `ssh -p 2200 root@<bmc_ip>` workflow, where logging in drops you
        straight into the console.
      - SOL 2: standard port 22, initial_command="obmc-console-client" -
        a normal BMC shell login, then automatically runs the console
        client command instead of requiring it to be typed by hand.

    Requests a fixed-size vt100 shell (see SOL_TERM_COLUMNS/SOL_TERM_ROWS)
    so the remote's idea of the terminal size matches what's actually
    displayed here, avoiding wrapped/distorted output.
    """

    def __init__(self, bmc_ip, port=2200, user="root", get_password=None, initial_command=None):
        self.bmc_ip = bmc_ip
        self.port = port
        self.user = user
        self.get_password = get_password
        self.initial_command = initial_command

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

                if self.initial_command:
                    # Give the shell a brief moment to finish printing its
                    # login banner/prompt before typing, then run it as if
                    # the user had typed it themselves.
                    self._stop.wait(0.5)
                    self._channel.sendall((self.initial_command + "\n").encode("utf-8"))

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
                    except Exception:
                        pass  # best-effort cleanup - see stop() for why
                if self._client is not None:
                    try:
                        self._client.close()
                    except Exception:
                        pass  # best-effort cleanup - see stop() for why
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
            except Exception:
                # Best-effort cleanup only: if the remote already dropped
                # the connection, paramiko's close() tries to send a
                # graceful-close message over a dead transport and raises
                # EOFError (not OSError, so the old except clause here
                # missed it) - either way, the channel is already gone,
                # there's nothing left to clean up.
                pass
        if self._thread:
            self._thread.join(timeout=2)


class _KvmForwardHandler(socketserver.BaseRequestHandler):
    """Relays one local TCP connection through the paramiko SSH transport
    to the BMC's VNC port - the standard paramiko local-port-forward
    pattern (there's no single high-level call for it like the `-L` flag;
    you run a small local server and pipe each connection through a
    'direct-tcpip' channel yourself)."""

    def handle(self):
        try:
            channel = self.server.ssh_transport.open_channel(
                "direct-tcpip",
                (self.server.remote_host, self.server.remote_port),
                self.request.getpeername(),
            )
        except Exception:
            return
        if channel is None:
            return

        def _pump(src, dst):
            try:
                while True:
                    data = src.recv(4096)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                for closeable in (src, dst):
                    try:
                        closeable.close()
                    except Exception:
                        pass

        t1 = threading.Thread(target=_pump, args=(self.request, channel), daemon=True)
        t2 = threading.Thread(target=_pump, args=(channel, self.request), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()


class _KvmForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class EmbeddedConsole(ctk.CTkFrame):
    """
    Right-hand console panel with a Serial/SOL switch. Only one backend is
    ever active; flipping the switch tears down the old one and (if you were
    already connected) reconnects using the new backend.
    """

    MODE_SERIAL = "Serial"
    MODE_SOL = "SOL"
    MODE_SOL2 = "SOL 2"
    MODE_KVM = "KVM"

    # Classic "BIOS blue" for SOL, since that's exactly the kind of screen
    # (BIOS/UEFI setup, u-boot) SOL usually shows; Serial keeps the plain
    # dark terminal look. SOL 2 shows the same kind of console (reached a
    # different way), so it gets the same treatment as SOL. KVM doesn't
    # drive this text area at all (see below), so it just keeps whatever
    # background Serial uses.
    _BG_FOR_MODE = {
        MODE_SERIAL: "#1e1e1e",
        MODE_SOL: "#0000aa",
        MODE_SOL2: "#0000aa",
        MODE_KVM: "#1e1e1e",
    }

    # (palette, default text color) per mode - VGA text-mode colors for the
    # BIOS-style SOL consoles, xterm-style for the Serial shell.
    _PALETTE_FOR_MODE = {
        MODE_SERIAL: (_XTERM_COLORS, "#e5e5e5"),
        MODE_SOL: (_VGA_COLORS, "#aaaaaa"),
        MODE_SOL2: (_VGA_COLORS, "#aaaaaa"),
        MODE_KVM: (_XTERM_COLORS, "#e5e5e5"),
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

        # KVM mode doesn't use the backend/TerminalView machinery at all -
        # it tunnels the BMC's VNC port over SSH (via paramiko, same auth
        # approach as SOL - actual password from Connection Settings, no
        # interactive terminal prompts) and hands off to the real Remmina
        # app, so it tracks its own connection/server state instead.
        self._kvm_ssh_client = None
        self._kvm_forward_server = None
        self._kvm_forward_thread = None
        self._kvm_remmina_process = None
        self.kvm_local_port = 5901
        self.kvm_remote_vnc_port = 5900

        # Next Boot / Power controls enable whenever ANY of these sources
        # has a confirmed-working Redfish connection to the BMC - not just
        # when this panel's own SOL/SOL2 text console happens to be
        # connected. Inventory/Sensors panels register themselves here via
        # set_external_redfish_ready() once they successfully load, so
        # e.g. loading Inventory alone is enough to unlock boot/power
        # actions even before ever opening a SOL session.
        self._redfish_ready_sources = {"sol": False}

        self._fit_after_id = None
        self._font_override = None  # set by Ctrl +/-; None = auto-fit

        self._build_ui()
        palette, default_fg = self._PALETTE_FOR_MODE[self.mode]
        self._term = TerminalView(
            self.output, widget_bg=self._BG_FOR_MODE[self.mode],
            on_reply=self._write_to_backend,
            palette=palette, default_fg=default_fg,
        )
        self._poll_queue()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 2))

        self.mode_switch = ctk.CTkSegmentedButton(
            header,
            values=[self.MODE_SERIAL, self.MODE_SOL, self.MODE_SOL2, self.MODE_KVM],
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

        self._font_family = _pick_mono_font(self)
        self.output = ctk.CTkTextbox(
            output_container, wrap="none", font=(self._font_family, TerminalView.FONT_SIZE),
            fg_color=self._BG_FOR_MODE[self.mode], text_color="#e5e5e5",
        )
        self.output.pack(fill="both", expand=True)

        raw_output = getattr(self.output, "_textbox", self.output)
        # One shared font object on the real Text widget, so resizing it
        # re-lays-out the whole grid in one go. Zero extra line spacing
        # keeps box-drawing borders joined vertically; the tiny requested
        # size stops a bigger font from pushing the window wider/taller
        # (the pane's size is decided by the layout, and the font fits it).
        self._term_font = tkfont.Font(self, family=self._font_family, size=TerminalView.FONT_SIZE)
        raw_output.configure(
            font=self._term_font, spacing1=0, spacing2=0, spacing3=0,
            width=1, height=1,
        )

        self._h_scroll = tk.Scrollbar(output_container, orient="horizontal", command=raw_output.xview)
        raw_output.configure(xscrollcommand=self._h_scroll.set)
        self._h_scroll_visible = False

        # Re-fit the font whenever the pane changes size.
        raw_output.bind("<Configure>", self._schedule_fit, add="+")

        # Keyboard is captured directly on the output pane once connected -
        # click into it and just type, like a real terminal. No separate
        # input line or Send button needed. Ctrl+V / Shift+Insert / middle
        # click all paste clipboard text straight into the session.
        self.output.bind("<Control-plus>", lambda e: self._zoom(+1))
        self.output.bind("<Control-equal>", lambda e: self._zoom(+1))
        self.output.bind("<Control-minus>", lambda e: self._zoom(-1))
        self.output.bind("<Control-0>", lambda e: self._zoom(0))
        self.output.bind("<Key>", self._on_keypress)
        self.output.bind("<Button-2>", self._on_middle_click)
        self.output.bind("<Button-3>", self._show_context_menu)
        self._context_menu = self._build_context_menu()

        self.hint_label = ctk.CTkLabel(
            self,
            text="Click in the console and type directly. Paste: Ctrl+V   Zoom: Ctrl +/-   Auto-fit: Ctrl+0",
            text_color="gray",
            font=("Courier", 10),
        )
        self.hint_label.pack(fill="x", padx=4, pady=(0, 4))

    # ---- sizing ----

    def _schedule_fit(self, _event=None):
        # Debounced: a window drag fires dozens of <Configure> events.
        if self._fit_after_id is not None:
            try:
                self.after_cancel(self._fit_after_id)
            except Exception:
                pass
        self._fit_after_id = self.after(60, self._fit_font)

    def _fit_font(self):
        """Pick the largest font size at which the whole fixed terminal
        grid (columns x rows) fits in the pane, so a full BIOS screen is
        visible at once, as large and sharp as the space allows."""
        self._fit_after_id = None
        raw = getattr(self.output, "_textbox", self.output)
        width, height = raw.winfo_width(), raw.winfo_height()
        if width <= 1 or height <= 1:
            return  # not mapped yet

        try:
            chrome = 2 * (int(raw.cget("borderwidth")) + int(raw.cget("highlightthickness"))
                          + int(raw.cget("padx")))
            chrome_y = 2 * (int(raw.cget("borderwidth")) + int(raw.cget("highlightthickness"))
                            + int(raw.cget("pady")))
        except (tk.TclError, ValueError):
            chrome = chrome_y = 8
        avail_w = width - chrome - 4   # small slack so the last column never clips
        avail_h = height - chrome_y - 2

        cols, rows = self._term.columns, self._term.rows
        if self._font_override is not None:
            size = self._font_override
        else:
            probe = tkfont.Font(self, family=self._font_family, size=TerminalView.MAX_FONT_SIZE)
            size = TerminalView.MIN_FONT_SIZE
            for candidate in range(TerminalView.MAX_FONT_SIZE, TerminalView.MIN_FONT_SIZE - 1, -1):
                probe.configure(size=candidate)
                if (probe.measure("M") * cols <= avail_w
                        and probe.metrics("linespace") * rows <= avail_h):
                    size = candidate
                    break

        if self._term_font.cget("size") != size:
            self._term_font.configure(size=size)

        # Only show the horizontal scrollbar when the grid really doesn't
        # fit (tiny window, or zoomed in past the auto-fit size).
        needs_scroll = self._term_font.measure("M") * cols > avail_w
        if needs_scroll and not self._h_scroll_visible:
            self._h_scroll.pack(fill="x")
            self._h_scroll_visible = True
        elif not needs_scroll and self._h_scroll_visible:
            self._h_scroll.pack_forget()
            self._h_scroll_visible = False

    def _zoom(self, step):
        """Ctrl +/- nudges the font size manually; Ctrl+0 returns to
        auto-fit. Returns 'break' so the keys aren't sent to the remote."""
        if step == 0:
            self._font_override = None
        else:
            current = self._term_font.cget("size")
            self._font_override = max(TerminalView.MIN_FONT_SIZE,
                                       min(TerminalView.MAX_FONT_SIZE, current + step))
        self._fit_font()
        return "break"

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

    # ---- right-click context menu ----

    def _build_context_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_selection)
        menu.add_command(label="Copy All", command=self._copy_all)
        menu.add_command(label="Paste", command=self._paste_clipboard)
        menu.add_separator()
        menu.add_command(label="Select All", command=self._select_all)
        menu.add_separator()
        menu.add_command(label="Clear", command=self.clear)
        # tk_popup()'s own internal grab doesn't reliably close the menu
        # on a click outside it on every platform - explicitly unposting
        # on FocusOut (which fires the moment focus moves away, i.e. the
        # user clicked elsewhere) guarantees it always dismisses, not just
        # when a menu item is actually chosen.
        menu.bind("<FocusOut>", lambda e: menu.unpost())
        return menu

    def _show_context_menu(self, event):
        try:
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()
        return "break"

    def _copy_selection(self):
        try:
            selected = self.output.selection_get()
        except Exception:
            return  # nothing selected - quietly do nothing, like most terminals
        try:
            self.clipboard_clear()
            self.clipboard_append(selected)
        except Exception:
            pass

    def _copy_all(self):
        text = self.output.get("1.0", "end-1c")
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass

    def _select_all(self):
        raw_output = getattr(self.output, "_textbox", self.output)
        raw_output.tag_add("sel", "1.0", "end-1c")

    # ---- mode / lifecycle ----

    def _on_mode_selected(self, value):
        if value == self.mode:
            return
        was_connected = self.backend is not None
        self.disconnect()
        self.mode = value
        self.output.configure(fg_color=self._BG_FOR_MODE[value])
        palette, default_fg = self._PALETTE_FOR_MODE[value]
        self._term.set_bg(self._BG_FOR_MODE[value], palette=palette, default_fg=default_fg)
        # Serial and SOL 2 auto-connect the moment you select them - no
        # need for a separate Connect click. SOL and KVM stay manual (KVM
        # in particular drives its own popup-based flow, not a quiet
        # background reconnect).
        if was_connected or value in (self.MODE_SERIAL, self.MODE_SOL2):
            self.connect()
        else:
            self._status(f"Switched to {value} - click Connect", "gray")

    def connect(self):
        self.disconnect()  # ensure a clean slate before (re)connecting

        if self.mode == self.MODE_KVM:
            self._start_kvm()
            return

        if self.mode == self.MODE_SERIAL:
            device = self.get_serial_device()
            if not device:
                self._status("No serial device selected.", "#e74c3c")
                return
            self.backend = SerialBackend(device)
            ok = self.backend.start(self._on_data, self._on_error)
            label = f"Serial: {device}"
        elif self.mode == self.MODE_SOL:
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
        else:  # MODE_SOL2
            bmc_ip = self.get_bmc_ip()
            if not bmc_ip:
                self._status("No BMC IP set.", "#e74c3c")
                return
            self.backend = SolBackend(
                bmc_ip, port=22, user=self.sol_user,
                get_password=self.get_password,
                initial_command="obmc-console-client",
            )
            ok = self.backend.start(self._on_data, self._on_error)
            label = f"SOL 2: {self.sol_user}@{bmc_ip}:22 (obmc-console-client)"

        if ok:
            self._term.reset()
            self._status(f"Connected - {label}", "#2ecc71")
            self.log(f"Console connected: {label}")
            self.output.focus_set()
            self._redfish_ready_sources["sol"] = self.mode in (self.MODE_SOL, self.MODE_SOL2)
            self._recompute_redfish_controls()
        else:
            self.backend = None
            self._status("Connection failed", "#e74c3c")
            self._redfish_ready_sources["sol"] = False
            self._recompute_redfish_controls()

    def disconnect(self):
        if self.backend:
            self.backend.stop()
            self.backend = None
            self._status("Disconnected", "gray")
        if self._kvm_ssh_client is not None or self._kvm_forward_server is not None:
            self._stop_kvm()
            self._status("Disconnected (SSH tunnel closed)", "gray")
        self._redfish_ready_sources["sol"] = False
        self._recompute_redfish_controls()

    def _start_kvm(self):
        """KVM console: tunnel the BMC's VNC port over SSH to a local port
        using paramiko (same authentication approach as SOL - the actual
        password from Connection Settings, no interactive terminal prompts
        that could otherwise land in whatever terminal launched Platypus),
        then hand off to the real Remmina app. Much simpler and more
        robust than an embedded VNC client (no vncdotool/Twisted/pyOpenSSL
        dependency chain to break), at the cost of the VNC view living in
        its own separate Remmina window rather than inside this panel.
        Runs quietly in the background (status label only, no popup) -
        Remmina's own window appearing is the visible confirmation it
        worked."""
        bmc_ip = self.get_bmc_ip()
        if not bmc_ip:
            self._status("No BMC IP set.", "#e74c3c")
            return

        password = self.get_password() if self.get_password else None
        self._status(f"Connecting to {bmc_ip}...", "gray")

        def _worker():
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=bmc_ip, port=22, username=self.sol_user, password=password,
                    timeout=10, banner_timeout=10, auth_timeout=10,
                    look_for_keys=False, allow_agent=False,
                )
            except paramiko.AuthenticationException:
                self.after(0, lambda: self._status(
                    "KVM authentication failed - check the password in Connection Settings.", "#e74c3c",
                ))
                return
            except Exception as e:
                error_msg = str(e)
                self.after(0, lambda: self._status(f"KVM connection failed: {error_msg}", "#e74c3c"))
                return

            try:
                server = _KvmForwardServer(("127.0.0.1", self.kvm_local_port), _KvmForwardHandler)
                server.ssh_transport = client.get_transport()
                server.remote_host = "localhost"
                server.remote_port = self.kvm_remote_vnc_port
                forward_thread = threading.Thread(target=server.serve_forever, daemon=True)
                forward_thread.start()
            except Exception as e:
                error_msg = str(e)
                self.after(0, lambda: self._status(f"KVM port forward failed: {error_msg}", "#e74c3c"))
                try:
                    client.close()
                except Exception:
                    pass
                return

            self._kvm_ssh_client = client
            self._kvm_forward_server = server
            self._kvm_forward_thread = forward_thread

            try:
                self._kvm_remmina_process = subprocess.Popen(
                    ["remmina", "-c", f"vnc://localhost:{self.kvm_local_port}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                msg = (
                    f"Tunnel is up on localhost:{self.kvm_local_port}, but Remmina isn't installed "
                    f"(sudo apt install remmina) - point any VNC viewer at that address instead."
                )
                self.after(0, lambda: self._status(msg, "#e74c3c"))
                return
            except Exception as e:
                error_msg = str(e)
                self.after(0, lambda: self._status(f"Failed to launch Remmina: {error_msg}", "#e74c3c"))
                return

            label = f"KVM: {bmc_ip}:{self.kvm_remote_vnc_port} -> localhost:{self.kvm_local_port} (Remmina)"
            self.after(0, lambda: self._status(f"Connected - {label}", "#2ecc71"))
            self.after(0, lambda: self.log(f"Console connected: {label}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _stop_kvm(self):
        if self._kvm_forward_server is not None:
            try:
                self._kvm_forward_server.shutdown()
                self._kvm_forward_server.server_close()
            except Exception:
                pass
            self._kvm_forward_server = None
        self._kvm_forward_thread = None
        if self._kvm_ssh_client is not None:
            try:
                self._kvm_ssh_client.close()
            except Exception:
                pass
            self._kvm_ssh_client = None
        # Remmina is a normal GUI app the user may still be actively
        # looking at - closing the tunnel above is enough; this doesn't
        # force-close the Remmina window itself.
        self._kvm_remmina_process = None

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

    def _recompute_redfish_controls(self):
        self._set_redfish_controls_enabled(any(self._redfish_ready_sources.values()))

    def set_external_redfish_ready(self, source, ready):
        """Lets other panels (Inventory, Sensors, ...) also unlock the
        Next Boot/Power controls once they've successfully connected to
        the BMC via Redfish themselves, independent of whether a SOL/SOL2
        text console happens to be open."""
        self._redfish_ready_sources[source] = ready
        self._recompute_redfish_controls()

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
                # Capture into a plain variable first - see the matching
                # note in inventory_panel.py for why the lambda can't
                # reference `e` directly here.
                error_msg = str(e)
                self.after(0, lambda: self._status(f"Redfish error: {error_msg}", "#e74c3c"))

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