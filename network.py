import serial
import asyncio
import threading 
from http.server import SimpleHTTPRequestHandler, HTTPServer
import os 
import socket
import subprocess
import time

import psutil

from utils import read_serial_data

# Grabs the current ip address of the bmc
async def grab_ip(callback_output, serial_device):
    ser = serial.Serial(serial_device, 115200, timeout=1)
    command = "/sbin/ifconfig eth0 | grep 'inet addr' | cut -d: -f2 | awk '{print $1}'\n"

    try:
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)

        lines = response.split('\n')
        for line in lines:
            if '.' in line:
                ipaddress = line
                callback_output(ipaddress)
                return ipaddress
    except Exception as e:
        callback_output(f"Error: {e}")
        return None
    finally:
        ser.close()

# Sets a temporary ip address to the bmc through serial 
async def set_ip(bmc_ip, callback_progress, callback_output, serial_device, bmc_user=None, bmc_pass=None):
    """Sets the IP address of the BMC with flexible root prompt detection."""
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    
    callback_progress(0.10)
    callback_output("Starting IP setup...")
    
    try:
        # Clear input buffer
        ser.reset_input_buffer()
        
        # Try to ensure we're at a command prompt
        for _ in range(1):
            ser.write(b"\n")
            await asyncio.sleep(0.5)
        
        # Read response
        initial_response = ser.read_all().decode('utf-8', errors='ignore')

        # If we're actually sitting at an unauthenticated login prompt (not
        # a shell), the "ifconfig..." command below would just get typed
        # in as the *next login attempt's username* - which is exactly
        # what was happening before this fix (see the screenshot: ifconfig/
        # obmcutil/curl/rm commands all showing up as rejected login
        # attempts). So check for that and log in first when needed,
        # rather than assuming a shell is already there.
        if "login:" in initial_response.lower() and bmc_user and bmc_pass:
            callback_output("Not logged in yet - logging in first...")
            ser.write(f"{bmc_user}\n".encode('utf-8'))
            await asyncio.sleep(1)
            ser.write(f"{bmc_pass}\n".encode('utf-8'))
            await asyncio.sleep(1.5)
            ser.read_all()  # drain the login response, not needed here
        
        # Proceed regardless of prompt detection
        callback_output("Setting IP address...")
        callback_progress(0.25)
        
        # Send command to set IP
        command = f"ifconfig eth0 up {bmc_ip}\n"
        ser.write(command.encode('utf-8'))
        await asyncio.sleep(1.5)
        
        # Optional verification - try to check if command ran
        verify_cmd = "ifconfig eth0\n"
        ser.write(verify_cmd.encode('utf-8'))
        await asyncio.sleep(1)
        verify_response = ser.read_all().decode('utf-8', errors='ignore')
        
        # Look for the IP or other success indicators in the response
        if bmc_ip in verify_response:
            callback_output(f"Verified IP address set to {bmc_ip}")
        elif "eth0" in verify_response and "inet" in verify_response:
            callback_output("Network interface configured (IP may have been set)")
        
        callback_progress(1)
        ser.close()
        callback_output(f"IP setup command completed.")
        await asyncio.sleep(5)
        callback_progress(0)
        
    except Exception as e:
        callback_output(f"Error during IP setup: {e}")
        callback_output("Exiting process. IP setup unsuccessful.")
        callback_progress(0)
        ser.close()


# ---------------------------------------------------------------------------
# Port clash handling
# ---------------------------------------------------------------------------

# systemd units we stopped to free a port, so they can be restarted later
# with restore_stopped_services() if desired.
_stopped_services = []


def _systemd_unit_for_pid(pid):
    """Return the systemd .service unit a PID belongs to, or None.

    Matters because killing e.g. apache2/nginx directly just gets it
    respawned by systemd a second later - the unit has to be stopped."""
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            for line in f:
                for part in reversed(line.strip().split("/")):
                    if part.endswith(".service") and not part.startswith("user@"):
                        return part
    except OSError:
        pass
    return None


def _find_listener_pids(port):
    """PIDs with a socket LISTENING on the given TCP port.

    Only listeners are matched - an outbound connection *to* some
    remote :80 (e.g. a browser tab) is not a clash and must not be
    killed, which `lsof -i :80` would do."""
    pids = set()
    unknown_owner = False
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                if conn.pid:
                    pids.add(conn.pid)
                else:
                    unknown_owner = True
    except psutil.AccessDenied:
        unknown_owner = True

    # Without root, psutil can't see other users' PIDs; ask ss as a fallback.
    if unknown_owner:
        try:
            out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"],
                                 capture_output=True, text=True, timeout=5).stdout
            for token in out.replace(",", " ").split():
                if token.startswith("pid="):
                    pid = token[4:]
                    if pid.isdigit():
                        pids.add(int(pid))
        except (OSError, subprocess.SubprocessError):
            pass
    return pids, unknown_owner and not pids


def port_is_free(port, host="0.0.0.0"):
    """True if we can bind the port right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def free_port(port=80, callback_output=print, timeout=5.0):
    """Close any *other* service listening on `port` so we can bind it.

    - systemd-managed services (apache2, nginx, lighttpd, ...) are stopped
      via systemctl so they don't respawn
    - other processes get SIGTERM, then SIGKILL if still alive after `timeout`
    - our own process is never touched (a server started earlier in this
      app shares our PID)

    Returns True if the port is free afterwards."""
    if port_is_free(port):
        return True

    callback_output(f"Port {port} is in use - closing the conflicting service(s)...")
    my_pid = os.getpid()
    pids, owner_hidden = _find_listener_pids(port)

    if owner_hidden:
        callback_output(f"Can't see which process owns port {port} - run Platypus with sudo.")
        return False

    if my_pid in pids:
        pids.discard(my_pid)
        callback_output(f"Note: port {port} is held by a server already running in Platypus "
                        f"- stop that one first (not killing ourselves).")

    to_kill = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            name = proc.name()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            name = "?"

        unit = _systemd_unit_for_pid(pid)
        if unit:
            callback_output(f"Stopping service {unit} ({name}, PID {pid}) using port {port}")
            try:
                r = subprocess.run(["systemctl", "stop", unit],
                                   capture_output=True, text=True, timeout=20)
                if r.returncode == 0:
                    if unit not in _stopped_services:
                        _stopped_services.append(unit)
                    continue
                callback_output(f"systemctl stop {unit} failed: {r.stderr.strip()} - killing directly")
            except (OSError, subprocess.SubprocessError) as e:
                callback_output(f"systemctl unavailable ({e}) - killing directly")

        callback_output(f"Terminating {name} (PID {pid}) using port {port}")
        try:
            proc.terminate()
            to_kill.append(proc)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            callback_output(f"Permission denied killing PID {pid} - run Platypus with sudo.")

    if to_kill:
        _, alive = psutil.wait_procs(to_kill, timeout=timeout)
        for proc in alive:
            callback_output(f"PID {proc.pid} ignored SIGTERM - force killing")
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(alive, timeout=2)

    # Give the kernel a moment to release the socket.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_is_free(port):
            callback_output(f"Port {port} is now free.")
            return True
        time.sleep(0.25)

    callback_output(f"Port {port} is still in use after cleanup.")
    return False


def restore_stopped_services(callback_output=print):
    """Restart any systemd services free_port() stopped (optional)."""
    while _stopped_services:
        unit = _stopped_services.pop()
        try:
            subprocess.run(["systemctl", "start", unit], timeout=20)
            callback_output(f"Restarted service {unit}")
        except (OSError, subprocess.SubprocessError) as e:
            callback_output(f"Couldn't restart {unit}: {e}")


class _ReusableHTTPServer(HTTPServer):
    # Lets us rebind straight away when the previous socket is in TIME_WAIT.
    allow_reuse_address = True


# Function to start an HTTP server for serving files
def start_server(directory, port, callback_output):
    os.chdir(directory)
    free_port(port, callback_output)
    handler = SimpleHTTPRequestHandler
    httpd = _ReusableHTTPServer(('0.0.0.0', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    callback_output(f"Serving files from {directory} on port {port}")
    return httpd

# Function to stop the HTTP server
def stop_server(httpd, callback_output):
    if httpd:
        httpd.shutdown()
        httpd.server_close()
        callback_output("Server has been stopped.")
    else:
        callback_output("Server instance is None.")