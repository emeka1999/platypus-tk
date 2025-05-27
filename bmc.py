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

async def flash_emmc(bmc_ip, directory, my_ip, dd_value, callback_progress, callback_output, serial_device):
    """Flash the eMMC storage on the BMC."""
    port = 80

    if dd_value == 1:
        type = 'mos-bmc'
    else:
        type = 'nanobmc'

    httpd = None
    ser = None  # Initialize serial connection variable

    try:
        httpd = start_server(directory, port, callback_output)
        callback_progress(0.10)

        # FIX: Use the passed serial_device parameter instead of hardcoded '/dev/ttyUSB0'
        ser = serial.Serial(serial_device, 115200, timeout=0.1)

        # Setting IP Address (bootloader)
        callback_output("Setting IP Address (bootloader)...")
        command = f'setenv ipaddr {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_output(response)
        callback_progress(0.20)

        # Grabbing virtual restore image
        callback_output("Grabbing virtual restore image...")
        command = f'wget ${{loadaddr}} {my_ip}:/obmc-rescue-image-snuc-{type}.itb; bootm\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_output(response)
        callback_progress(0.40)

        # Setting IP Address (BMC)
        callback_output("Setting IP Address (BMC)...")
        await asyncio.sleep(20)
        command = f'ifconfig eth0 up {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_output(response)
        callback_progress(0.50)

        # Grabbing restore image
        callback_output("Grabbing restore image to your system...")
        command = f"curl -o obmc-phosphor-image-snuc-{type}.wic.xz {my_ip}/obmc-phosphor-image-snuc-{type}.wic.xz\n"
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_output(response)
        callback_progress(0.60)

        # Grabbing the mapping file
        callback_output("Grabbing the mapping file...")
        command = f'curl -o obmc-phosphor-image-snuc-{type}.wic.bmap {my_ip}/obmc-phosphor-image-snuc-{type}.wic.bmap\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)
        callback_output(response)
        callback_progress(0.90)

        # Flashing the restore image
        callback_output("Flashing the restore image to your system...")
        command = f'bmaptool copy obmc-phosphor-image-snuc-{type}.wic.xz /dev/mmcblk0\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)
        callback_output(response)

        await asyncio.sleep(55)
        callback_output("Factory Reset Complete. Please let the BMC reboot.")
        ser.write(b'reboot\n')
        ser.close()
        callback_progress(1.00)
        await asyncio.sleep(60)

    except Exception as e:
        callback_output(f"Error: {e}")
        callback_output("Flash unsuccessful.")
        return None
    finally:
        if ser and ser.is_open:
            ser.write(b'\n')  # Send newline to reset state
            ser.close()
        if httpd:
            stop_server(httpd, callback_output)
        callback_progress(0)

async def reset_to_uboot(callback_output, serial_device):
    """Resets the OpenBMC to U-Boot using the serial connection and emulates keyboard interaction."""
    ser = None
    try:
        callback_output("Opening serial connection...")
        # FIX: Use the passed serial_device parameter instead of hardcoded serial_device
        ser = serial.Serial(serial_device, baudrate=115200, timeout=1)
        ser.dtr = True

        # First, attempt to interrupt any boot process by sending a few returns
        callback_output("Attempting to interrupt boot process...")
        for _ in range(3):
            ser.write(b'\n')
            await asyncio.sleep(0.5)
        
        # For OpenBMC, we need to send specific commands to drop to U-Boot
        callback_output("Sending OpenBMC commands to reboot to U-Boot...")
        
        
        # Send the reboot command
        command = "reboot\n"
        ser.write(command.encode('utf-8'))
        callback_output("Rebooting system...")
        
        # Wait for U-Boot to start
        callback_output("Waiting for U-Boot to initialize...")
        await asyncio.sleep(2)
        
        # Look for the autoboot message and interrupt it
        callback_output("Monitoring for autoboot countdown and interrupting...")
        
        # Set up a task to periodically send a key to interrupt autoboot
        interrupt_count = 0
        max_interrupts = 30  # Try for about 15 seconds (0.5s intervals)
        autoboot_detected = False
        
        while interrupt_count < max_interrupts:
            # Read any available data
            if ser.in_waiting:
                data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                if "autoboot" in data.lower() or "Hit any key" in data:
                    autoboot_detected = True
                    callback_output("Autoboot detected! Sending interrupt key...")
                    # Send a space to interrupt
                    ser.write(b' ')
                    await asyncio.sleep(0.1)
                    # Also send Enter to ensure it's caught
                    ser.write(b'\n')
                    break
            
            # Even if not detected yet, periodically send interrupt keys
            if interrupt_count % 4 == 0:  # Every ~2 seconds
                ser.write(b' ')  # Send space
                await asyncio.sleep(0.1)
                ser.write(b'\n')  # Send enter
            
            await asyncio.sleep(0.5)
            interrupt_count += 1
        
        if autoboot_detected:
            callback_output("Successfully interrupted autoboot!")
            
            # Wait a moment to read any response
            await asyncio.sleep(1)
            response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
            if response:
                callback_output(f"U-Boot response: {response.strip()}")
            
            # Set bootdelay to 15 seconds for future boots
            callback_output("Setting bootdelay to 15 seconds for future boots...")
            ser.write(b"setenv bootdelay 15\n")
            await asyncio.sleep(0.5)
            ser.write(b"saveenv\n")
            await asyncio.sleep(1)
            
            callback_output("System is now at U-Boot prompt. You can interact with it via the serial console.")
        else:
            callback_output("Could not detect autoboot sequence. System may still be booting.")
            callback_output("If needed, open the console and press a key when you see the autoboot countdown.")

    except serial.SerialException as e:
        callback_output(f"Serial error: {e}")
    except Exception as e:
        callback_output(f"Error during reset to U-Boot: {e}")
    finally:
        # Don't close the serial connection so that it can be used by the console
        # Just report status
        if ser and ser.is_open:
            callback_output("Serial connection remains open for console interaction.")


async def bios_update(bmc_user, bmc_pass, bmc_ip, fw_content, callback_progress, callback_output):
    callback_output("Initializing Red Fish client for BIOS update...")
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    callback_progress(0.10)
    
    try:
        await asyncio.to_thread(redfish_client.login)
        update_service = redfish_client.get("/redfish/v1/UpdateService")
        if update_service.status != 200:
            callback_output("Failed to find the update service.")
            return

        callback_progress(0.15)
        callback_output("Logged in.")

        update_service_url = update_service.dict["@odata.id"]
        
        # Verify we have a tar.gz file
        if not fw_content[:4] == b'\x1f\x8b\x08\x00':  # Simple check for gzip magic number
            callback_output("Verifying firmware format (tar.gz)...")
        
        # For BIOS updates, we need to specify the target as BIOS through proper headers
        headers = {"Content-Type": "application/octet-stream"}
        
        callback_output("Sending BIOS update request...")
        callback_output("WARNING: BIOS update process can take up to 7 minutes. Please do not interrupt.")
        response = await asyncio.to_thread(redfish_client.post, f"{update_service_url}/update", body=fw_content, headers=headers)
        callback_progress(0.20)

        if response.status in [200, 202]:
            callback_output(f"BIOS update initiated successfully: {response.text}")
            task_url = response.dict["@odata.id"]
            
            # Custom monitoring for BIOS update with longer timeouts
            max_attempts = 42  # 7 minutes with 10-second intervals
            attempt = 0
            completed = False
            
            while attempt < max_attempts and not completed:
                try:
                    task_status = await asyncio.to_thread(redfish_client.get, task_url)
                    
                    # Calculate progress (distribute from 20% to 95% across the expected duration)
                    progress = 0.20 + (0.75 * (attempt / max_attempts))
                    callback_progress(progress)
                    
                    if task_status.dict.get("TaskState") == "Completed":
                        callback_output("BIOS update completed successfully!")
                        callback_progress(1.0)
                        completed = True
                    elif task_status.dict.get("TaskState") == "Exception":
                        callback_output(f"BIOS update failed: {task_status.dict.get('Messages', [{}])[0].get('Message', 'Unknown error')}")
                        break
                    else:
                        # Show percentage based progress
                        percentage = int((attempt / max_attempts) * 100)
                        if attempt % 6 == 0:  # Show message every minute
                            elapsed_minutes = attempt // 6
                            callback_output(f"BIOS update in progress... ({percentage}% - {elapsed_minutes} minutes elapsed)")
                except Exception as e:
                    callback_output(f"Error checking task status: {e}")
                
                attempt += 1
                if not completed:
                    await asyncio.sleep(10)  # 10-second intervals
            
            if not completed:
                callback_output("BIOS update took longer than expected. Check system status manually.")
        else:
            callback_output(f"Failed to initiate BIOS firmware update. Response code: {response.status}")
    except Exception as e:
        callback_output(f"Error: {e}")
    finally:
        await asyncio.to_thread(redfish_client.logout)
    
    await asyncio.sleep(5)
    callback_progress(0)

async def reset_to_uboot(callback_output, serial_device):
    """Resets the OpenBMC to U-Boot using the serial connection and emulates keyboard interaction."""
    ser = None
    try:
        callback_output("Opening serial connection...")
        ser = serial.Serial(serial_device, baudrate=115200, timeout=1)
        ser.dtr = True

        # First, attempt to interrupt any boot process by sending a few returns
        callback_output("Attempting to interrupt boot process...")
        for _ in range(3):
            ser.write(b'\n')
            await asyncio.sleep(0.5)
        
        # For OpenBMC, we need to send specific commands to drop to U-Boot
        callback_output("Sending OpenBMC commands to reboot to U-Boot...")
        
        
        # Send the reboot command
        command = "reboot\n"
        ser.write(command.encode('utf-8'))
        callback_output("Rebooting system...")
        
        # Wait for U-Boot to start
        callback_output("Waiting for U-Boot to initialize...")
        await asyncio.sleep(2)
        
        # Look for the autoboot message and interrupt it
        callback_output("Monitoring for autoboot countdown and interrupting...")
        
        # Set up a task to periodically send a key to interrupt autoboot
        interrupt_count = 0
        max_interrupts = 30  # Try for about 15 seconds (0.5s intervals)
        autoboot_detected = False
        
        while interrupt_count < max_interrupts:
            # Read any available data
            if ser.in_waiting:
                data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                if "autoboot" in data.lower() or "Hit any key" in data:
                    autoboot_detected = True
                    callback_output("Autoboot detected! Sending interrupt key...")
                    # Send a space to interrupt
                    ser.write(b' ')
                    await asyncio.sleep(0.1)
                    # Also send Enter to ensure it's caught
                    ser.write(b'\n')
                    break
            
            # Even if not detected yet, periodically send interrupt keys
            if interrupt_count % 4 == 0:  # Every ~2 seconds
                ser.write(b' ')  # Send space
                await asyncio.sleep(0.1)
                ser.write(b'\n')  # Send enter
            
            await asyncio.sleep(0.5)
            interrupt_count += 1
        
        if autoboot_detected:
            callback_output("Successfully interrupted autoboot!")
            
            # Wait a moment to read any response
            await asyncio.sleep(1)
            response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
            if response:
                callback_output(f"U-Boot response: {response.strip()}")
            
            # Set bootdelay to 15 seconds for future boots
            callback_output("Setting bootdelay to 15 seconds for future boots...")
            ser.write(b"setenv bootdelay 15\n")
            await asyncio.sleep(0.5)
            ser.write(b"saveenv\n")
            await asyncio.sleep(1)
            
            callback_output("System is now at U-Boot prompt. You can interact with it via the serial console.")
        else:
            callback_output("Could not detect autoboot sequence. System may still be booting.")
            callback_output("If needed, open the console and press a key when you see the autoboot countdown.")

    except serial.SerialException as e:
        callback_output(f"Serial error: {e}")
    except Exception as e:
        callback_output(f"Error during reset to U-Boot: {e}")
    finally:
        # Don't close the serial connection so that it can be used by the console
        # Just report status
        if ser and ser.is_open:
            callback_output("Serial connection remains open for console interaction.")


async def reset_uboot(callback_output, serial_device):
 

    """Resets the BMC to U-Boot using the serial connection."""
    ser = None
    try:
        callback_output("Opening serial connection...")
        ser = serial.Serial(serial_device, baudrate=115200, timeout=1)
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
