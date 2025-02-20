from http.server import SimpleHTTPRequestHandler, HTTPServer
import urllib3
import asyncio
import redfish
import serial 
import os 
import threading 


from utils import monitor_task, read_serial_data
from network import stop_server, start_server

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Updates the BMC firmware through redfish 
async def bmc_update(bmc_user, bmc_pass, bmc_ip, fw_content, callback_progress, callback_output):
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


# Power on the host through serial
async def power_host(callback_output, serial_device):
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    command = f"obmcutil poweron\n"

    callback_output("Running...")

    try:            
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
async def reboot_bmc(callback_output, serial_device):
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    command = f"reboot\n"

    callback_output("Running...")

    try: 
        ser.write(command.encode('utf-8'))

        response = ser.read_until(b'\n')
        callback_output(response.decode('utf-8'))
            

    except Exception as e:
        if "device reports readiness to read but returned no data" in str(e):
            callback_output(f"Error: {e}")
            callback_output("Rebooting...")
            callback_output("Please give the BMC time to finish rebooting.")
        else: 
            callback_output(f"Error: {e}")
            callback_output("Exiting Process.")
        return 
    callback_output("Rebooting...")
    callback_output("Please give the BMC time to finish rebooting")

# Flashes the U-Boot of the BMC through serial
async def flasher(flash_file, my_ip, callback_progress, callback_output, serial_device):
    directory = os.path.dirname(flash_file)
    file_name = os.path.basename(flash_file)
    port = 80

    httpd = start_server(directory, port, callback_output)
    callback_progress(0.2)

    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True

    try:
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
async def flash_eeprom(flash_file, my_ip, callback_progress, callback_output, serial_device):
    directory = os.path.dirname(flash_file)
    file_name = os.path.basename(flash_file)
    port = 80

    # Start HTTP server
    httpd = start_server(directory, port, callback_output)
    callback_progress(0.2)

    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True

    try:
        # Power on
        callback_output("Powering on...")
        ser.write(b"obmcutil poweron\n")
        await asyncio.sleep(8)
        callback_progress(0.4)

        # Configure EEPROM
        callback_output("Configuring EEPROM...")
        ser.write(b"echo 24c02 0x50 > /sys/class/i2c-adapter/i2c-1/new_device\n")
        await asyncio.sleep(8)
        callback_progress(0.6)

        # Fetch FRU binary
        url = f"http://{my_ip}:{port}/{file_name}"
        curl_command = f"curl -o {file_name} {url}\n"
        ser.write(curl_command.encode('utf-8'))
        await asyncio.sleep(8)
        callback_output(f"Fetching FRU binary from {url}")

        callback_progress(0.8)

        # Flash EEPROM
        callback_output("Flashing EEPROM...")
        flash_command = f"dd if={file_name} of=/sys/bus/i2c/devices/1-0050/eeprom\n"
        ser.write(flash_command.encode('utf-8'))
        await asyncio.sleep(8)
        callback_output("Flashing complete.")
        callback_progress(1.0)

        # Remove FRU binary
        callback_output("Removing FRU binary...")
        remove_command = f"rm -f {file_name}\n"
        ser.write(remove_command.encode('utf-8'))
        await asyncio.sleep(5)
        callback_output("FRU binary removed successfully.")

        # Reboot
        callback_output("Rebooting system...")
        ser.write(b"obmcutil poweroff && reboot\n")
        await asyncio.sleep(5)
        callback_output("System reboot initiated.")
        
    except serial.SerialException as e:
        callback_output(f"Serial Error: {e}")
    finally:
        ser.close()
        stop_server(httpd, callback_output)
        callback_progress(0)

        
        
async def bmc_factory_reset(callback_output, serial_device):
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    command = "bmc_factory_reset manual\n"
    callback_output("Executing factory reset...")

    try:
        ser.write(command.encode('utf-8'))
        await asyncio.sleep(5)
        response = ser.read_all().decode('utf-8')
        callback_output(f"Factory reset response: {response}")
    except Exception as e:
        callback_output(f"Error: {e}")
    finally:
        ser.close()

async def flash_emmc(bmc_ip, directory, my_ip, dd_value, callback_progress, callback_output):
    """Flash the eMMC storage on the BMC with Windows compatibility."""
    port = 80
    
    if dd_value == 1:
        type = 'mos-bmc'
    else:
        type = 'nanobmc'

    httpd = None
    ser = None

    try:
        httpd = start_server(directory, port, callback_output)
        callback_progress(0.10)

        # Get the first available serial port
        if sys.platform == 'win32':
            ports = list(serial.tools.list_ports.comports())
            if not ports:
                raise Exception("No serial ports found")
            ser = serial.Serial(ports[0].device, 115200, timeout=0.1)
        else:
            ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)

        # Rest of the function remains the same
        # ...

    except Exception as e:
        callback_output(f"Error: {e}")
        callback_output("Flash unsuccessful.")
        return None
    finally:
        if ser and ser.is_open:
            ser.write(b'\n')
            ser.close()
        if httpd:
            stop_server(httpd, callback_output)
        callback_progress(0)

async def reset_uboot(callback_output):
    """Resets the BMC to U-Boot using the serial connection."""
    try:
        callback_output("Opening serial connection...")
        ser = serial.Serial('/dev/ttyUSB0', baudrate=115200, timeout=1)
        ser.dtr = True

        callback_output("Sending reset command to U-Boot...")
        command = 'reset\n'
        ser.write(command.encode('utf-8'))
        await asyncio.sleep(2)

        # Read response
        response = ser.read(1024).decode('utf-8').strip()
        callback_output(f"Response: {response}")

        ser.close()
        callback_output("Reset to U-Boot completed.")
    except serial.SerialException as e:
        callback_output(f"Serial error: {e}")
    except Exception as e:
        callback_output(f"Error during reset: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
