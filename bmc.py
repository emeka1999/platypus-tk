import urllib3
import serial
import redfish
import requests
import threading
import os
from http.server import SimpleHTTPRequestHandler, HTTPServer
import asyncio
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
loggedin = False

# Reads serial response for a particular command
def read_serial_data(ser, command, delay):
    try:
        time.sleep(delay)
        ser.write(command.encode('utf-8'))
        time.sleep(2)  
        response = ser.read_all().decode('utf-8')
        return response
    except Exception as e:
        print(f"Error reading serial data: {e}")
        return ""

# Updates the BMC firmware through redfish 
async def bmc_update(bmc_user, bmc_pass, bmc_ip, fw_content, callback_progress, callback_output, serial_device):
    callback_output("Initializing Red Fish client...")
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    callback_progress(0.25)
    
    try:
        await asyncio.to_thread(redfish_client.login)
        update_service = redfish_client.get("/redfish/v1/UpdateService")
        if update_service.status != 200:
            callback_output("Failed to find the update service.")
            return

        callback_progress(0.50)
        callback_output("Logged in.")

        update_service_url = update_service.dict["@odata.id"]

        headers = {"Content-Type": "application/octet-stream"}
        callback_output("Sending update request...")
        response = await asyncio.to_thread(redfish_client.post, f"{update_service_url}/update", body=fw_content, headers=headers)
        callback_progress(0.75)

        if response.status in [200, 202]:
            callback_output(f"Update initiated successfully: {response.text}")
            task_url = response.dict["@odata.id"]
            await monitor_task(redfish_client, task_url, callback_output, callback_progress)
        else:
            callback_output(f"Failed to initiate firmware update. Response code: {response.status}")
    except Exception as e:
        callback_output(f"Error: {e}")
    finally:
        await asyncio.to_thread(redfish_client.logout)
    
    await asyncio.sleep(5)
    callback_progress(0)

# Continuously grabs that status of a redfish task 
async def monitor_task(redfish_client, task_url, callback_output, callback_progress):
    while True:
        task_response = await asyncio.to_thread(redfish_client.get, task_url)
        if task_response.status != 200:
            callback_output("Failed to get task status.")
            break

        task_status = task_response.dict["TaskState"]
        callback_output(f"Task status: {task_status}")

        if task_status in ["Completed", "Exception", "Killed"]:
            if task_status == 'Completed':
                callback_progress(1)
            callback_output(f"Task completed with status: {task_status}")
            break

        await asyncio.sleep(5)

# Grabs various information regarding the bmc through redfish 
def bmc_info(bmc_user, bmc_pass, bmc_ip, callback_out):
    try:
        redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
        redfish_client.login(auth="session")
        response = redfish_client.get("/redfish/v1/Managers/bmc")
        if response.status == 200:
            bmc_info = response.dict
            return(bmc_info)
        else:
            callback_out(f"Failed to fetch BMC information. Status code: {response.status}")
            return None
    except Exception as e:
        callback_out(f"An error occurred: {str(e)}")
        return None
    finally:
        redfish_client.logout()

# Grabs the current ip address of the bmc
async def grab_ip(bmc_user, bmc_pass, callback_output, serial_device):
    global loggedin
    ser = serial.Serial(serial_device, 115200, timeout=1)
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"
    command = "/sbin/ifconfig eth0 | grep 'inet addr' | cut -d: -f2 | awk '{print $1}'\n"

    try:
        if loggedin == False:
            ser.write(b'\n')
            await asyncio.sleep(2)  
            ser.write(user.encode('utf-8'))
            await asyncio.sleep(2)
            ser.write(passw.encode('utf-8'))
            await asyncio.sleep(5)
            loggedin = True
        
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
async def set_ip(bmc_ip, bmc_user, bmc_pass, callback_progress, callback_output, serial_device):
    global loggedin
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"
    command = f"ifconfig eth0 up {bmc_ip}\n"

    callback_progress(0.25)
    callback_output("Running...")

    try:
        if not loggedin:
            ser.flushInput()
            ser.write(b'\n')
            await asyncio.sleep(2)
            ser.write(user.encode('utf-8'))
            await asyncio.sleep(2)
            ser.write(passw.encode('utf-8'))
            await asyncio.sleep(3)
            
            callback_progress(0.5)
            callback_output("Logged in.")

            loggedin = True

        callback_output("Setting IP...")

        ser.write(command.encode('utf-8'))
        await asyncio.sleep(4)

        ip = await grab_ip(bmc_user, bmc_pass, callback_output, serial_device)
        callback_progress(1)

        ser.close()
        callback_output("IP set successfully.")
        await asyncio.sleep(5)
        callback_progress(0)
        return ip
    except Exception as e:
        callback_output(f"Error: {e}")
        callback_output("Exiting process. Set IP unsuccessful.")
        callback_progress(0)
        ser.close()

# Power on the host through serial
async def power_host(bmc_user, bmc_pass, callback_output, serial_device):
    global loggedin
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"
    command = f"obmcutil poweron\n"

    callback_output("Running...")

    try:            
        if loggedin == False:       
            ser.write(b'\n')
            await asyncio.sleep(2)  
            ser.write(user.encode('utf-8'))
            await asyncio.sleep(2)
            ser.write(passw.encode('utf-8'))
            await asyncio.sleep(3)
            
            callback_output("Logged in.")
            loggedin = True

        ser.write(command.encode('utf-8'))

        response = ser.read_until(b'\n')
        callback_output(response.decode('utf-8'))

    except Exception as e:
        if "device reports readiness to read but returned no data" in str(e):
            callback_output(f"Error: {e}")
            callback_output("Host powered on.")
        else: 
            callback_output(f"Error: {e}")
            callback_output("Exiting Process. Host not powered on.")
        return 
    callback_output("Host powered on.")
    await asyncio.sleep(5)

# Reboots the BMC through serial
async def reboot_bmc(bmc_user, bmc_pass, callback_output, serial_device):
    global loggedin
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"
    command = f"reboot\n"

    callback_output("Running...")

    try: 
        if loggedin == False:                  
            ser.write(user.encode('utf-8'))
            await asyncio.sleep(2)
            ser.write(passw.encode('utf-8'))
            await asyncio.sleep(2)
            loggedin = True
            
        callback_output("Logged in.")

        ser.write(command.encode('utf-8'))

        response = ser.read_until(b'\n')
        callback_output(response.decode('utf-8'))
            

    except Exception as e:
        if "device reports readiness to read but returned no data" in str(e):
            callback_output(f"Error: {e}")
            callback_output("Rebooting...")
            loggedin = False
            callback_output("Please give the BMC time to finish rebooting.")
        else: 
            callback_output(f"Error: {e}")
            callback_output("Exiting Process.")
        return 
    callback_output("Rebooting...")
    loggedin = False
    callback_output("Please give the BMC time to finish rebooting")

    await asyncio.sleep(25)

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

# Flashes the U-Boot of the BMC through serial
async def flasher(bmc_user, bmc_pass, flash_file, my_ip, callback_progress, callback_output, serial_device):
    global loggedin
    directory = os.path.dirname(flash_file)
    file_name = os.path.basename(flash_file)
    port = 80

    httpd = start_server(directory, port, callback_output)
    callback_progress(0.2)

    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"

    try:
        if not loggedin:
            ser.write(b'\n')
            await asyncio.sleep(2)
            ser.write(user.encode('utf-8'))
            await asyncio.sleep(2)
            ser.write(passw.encode('utf-8'))
            await asyncio.sleep(3)
            loggedin = True
            callback_output("Logged in.")
            callback_progress(0.4)

        url = f"http://{my_ip}:{port}/{file_name}"
        curl_command = f"curl -o {file_name} {url}\n"
        ser.write(curl_command.encode('utf-8'))
        await asyncio.sleep(5)
        callback_output('Curl command sent.')

        callback_progress(0.6)

        command = "echo 0 > /sys/block/mmcblk0boot0/force_ro\n"
        ser.write(command.encode('utf-8'))
        await asyncio.sleep(4)
        callback_output('Changed MMC to RW')

        callback_progress(0.8)

        command = f'dd if={file_name} of=/dev/mmcblk0boot0 bs=512 seek=256\n'
        ser.write(command.encode('utf-8'))
        await asyncio.sleep(7)
        callback_output("Flashing complete")
        callback_progress(1)

        # Remove fip.bin after flashing
        remove_command = "rm -f fip.bin\n"
        ser.write(remove_command.encode('utf-8'))
        await asyncio.sleep(2)
        callback_output("fip.bin removed successfully.")
    except serial.SerialException as e:
        callback_output(f"Serial Error: {e}")
    finally:
        ser.close()
        stop_server(httpd, callback_output)
        callback_progress(0)

# Flash EEPROM through serial
async def flash_eeprom(bmc_user, bmc_pass, host_ip, callback_progress, callback_output, serial_device):
    global loggedin
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True

    try:
        if not loggedin:
            # Login sequence
            ser.write(b'\n')
            await asyncio.sleep(2)
            ser.write(f"{bmc_user}\n".encode())
            await asyncio.sleep(2)
            ser.write(f"{bmc_pass}\n".encode())
            await asyncio.sleep(3)
            loggedin = True
            callback_output("Logged in.")
            callback_progress(0.2)

        # Start HTTP server
        callback_output("Starting HTTP server...")
        directory = os.path.dirname(__file__)
        os.chdir(directory)
        handler = SimpleHTTPRequestHandler
        with HTTPServer(("", 8080), handler) as httpd:
            callback_output(f"Serving on port 8080")
            threading.Thread(target=httpd.serve_forever, daemon=True).start()

            # Power on
            callback_output("Powering on...")
            ser.write(b"obmcutil poweron\n")
            await asyncio.sleep(5)
            callback_progress(0.4)

            # Configure EEPROM
            callback_output("Configuring EEPROM...")
            ser.write(b"echo 24c02 0x50 > /sys/class/i2c-adapter/i2c-1/new_device\n")
            await asyncio.sleep(5)
            callback_progress(0.6)

            # Fetch FRU binary
            callback_output(f"Fetching FRU binary from http://{host_ip}:8080/fru.bin")
            ser.write(f"curl -o fru.bin http://{host_ip}:8080/fru.bin\n".encode())
            await asyncio.sleep(5)
            callback_progress(0.8)

            # Flash EEPROM
            callback_output("Flashing EEPROM...")
            ser.write(b"dd if=fru.bin of=/sys/bus/i2c/devices/1-0050/eeprom\n")
            await asyncio.sleep(5)
            callback_progress(1.0)
            
            # Remove FRU binary
            callback_output("Removing FRU binary...")
            ser.write(b"rm -f fru.bin\n")
            await asyncio.sleep(1)
            callback_output("FRU binary removed successfully.")

            # Reboot
            callback_output("EEPROM flashing completed successfully.")
    except Exception as e:
        callback_output(f"Error during EEPROM flashing: {str(e)}")
    finally:
        ser.close()
        loggedin = False
        callback_progress(0)
        
        
async def bmc_factory_reset(callback_output, serial_device):
    global loggedin
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    command = "bmc_factory_reset manual\n"

    callback_output("Executing factory reset...")

    try:
        if not loggedin:
            ser.write(b'\n')
            await asyncio.sleep(2)
            callback_output("Please log in before running this command.")
            return

        ser.write(command.encode('utf-8'))
        await asyncio.sleep(5)
        response = ser.read_all().decode('utf-8')
        callback_output(f"Factory reset response: {response}")
    except Exception as e:
        callback_output(f"Error: {e}")
    finally:
        ser.close()
