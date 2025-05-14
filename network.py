import serial
import asyncio
import threading 
from http.server import SimpleHTTPRequestHandler, HTTPServer
import os 

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
async def set_ip(bmc_ip, callback_progress, callback_output, serial_device):
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


# Function to start an HTTP server for serving files
def start_server(directory, port, callback_output):
    os.chdir(directory)
    handler = SimpleHTTPRequestHandler
    httpd = HTTPServer(('0.0.0.0', port), handler)
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