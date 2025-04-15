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

        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)

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
async def mount_virtual_media_openbmc(bmc_user, bmc_pass, bmc_ip, iso_path, your_ip, callback_progress, callback_output):
    """
    Mount virtual media specifically optimized for OpenBMC platforms.
    OpenBMC has unique Redfish implementation that requires special handling.
    
    Args:
        bmc_user: BMC username
        bmc_pass: BMC password
        bmc_ip: BMC IP address
        iso_path: Path to the ISO file
        your_ip: Host IP address
        callback_progress: Function to report progress
        callback_output: Function to report output messages
    """
    callback_output("Initializing OpenBMC virtual media mount...")
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    callback_progress(0.10)
    
    try:
        await asyncio.to_thread(redfish_client.login)
        callback_output("Logged in to BMC via Redfish.")
        callback_progress(0.20)
        
        # Get the version of OpenBMC firmware (helpful for troubleshooting)
        try:
            response = redfish_client.get("/redfish/v1")
            if response.status == 200:
                firmware_version = response.dict.get("RedfishVersion", "Unknown")
                callback_output(f"Redfish Version: {firmware_version}")
                
                # Get more detailed BMC info
                bmcinfo = redfish_client.get("/redfish/v1/Managers/bmc")
                if bmcinfo.status == 200:
                    fw_version = bmcinfo.dict.get("FirmwareVersion", "Unknown")
                    callback_output(f"OpenBMC Firmware Version: {fw_version}")
        except Exception as e:
            callback_output(f"Warning: Could not retrieve BMC version info: {e}")
        
        # For OpenBMC, we need to check the virtual media collection under Managers
        manager_vm_path = "/redfish/v1/Managers/bmc/VirtualMedia"
        vm_collection = redfish_client.get(manager_vm_path)
        
        if vm_collection.status != 200:
            callback_output(f"Failed to get virtual media collection: {vm_collection.status}")
            return False
            
        callback_output("Found Virtual Media collection.")
        callback_progress(0.30)
        
        # Find an available media slot
        vm_slots = []
        for member in vm_collection.dict.get("Members", []):
            member_path = member.get("@odata.id")
            vm_slots.append(member_path)
            
        if not vm_slots:
            callback_output("No virtual media slots found.")
            return False
            
        # For OpenBMC, we may need to check each slot for capabilities
        valid_slot = None
        insert_action = None
        
        for slot_path in vm_slots:
            slot_info = redfish_client.get(slot_path)
            if slot_info.status != 200:
                continue
                
            # Check if this slot supports CD/DVD media
            media_types = slot_info.dict.get("MediaTypes", [])
            if not media_types or any(x in media_types for x in ["CD", "DVD"]):
                valid_slot = slot_path
                
                # Get the InsertMedia action if available
                actions = slot_info.dict.get("Actions", {})
                if "#VirtualMedia.InsertMedia" in actions:
                    insert_action = actions["#VirtualMedia.InsertMedia"]["target"]
                    callback_output(f"Found media slot with InsertMedia action: {slot_path}")
                    break
        
        if not valid_slot:
            callback_output("No suitable virtual media slot found.")
            return False
            
        if not insert_action:
            callback_output("No InsertMedia action found for virtual media.")
            return False
            
        callback_progress(0.40)
            
        # OpenBMC-specific: Try to discover required parameters for InsertMedia
        # Some versions expect different parameters
        required_params = []
        try:
            schema = redfish_client.get("/redfish/v1/JsonSchemas/VirtualMedia")
            if schema.status == 200:
                # Future enhancement: parse schema to get required params
                pass
        except Exception:
            pass
            
        # For OpenBMC, we'll try a clean HTTP server implementation
        # OpenBMC is sensitive to how files are served
        directory = os.path.dirname(iso_path)
        filename = os.path.basename(iso_path)
        
        # Create a specialized HTTP server for OpenBMC
        callback_output("Starting specialized HTTP server for OpenBMC...")
        
        # Create a custom handler that properly serves ISO files
        class OpenBMCHandler(SimpleHTTPRequestHandler):
            def do_GET(self):
                # If the request is for an ISO file, set specific headers
                if self.path.lower().endswith('.iso'):
                    try:
                        # Open the file
                        f = open(os.path.join(directory, filename), 'rb')
                        fs = os.fstat(f.fileno())
                        
                        # Send response
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(fs[6]))
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
                        self.end_headers()
                        
                        # Send the file in chunks to avoid memory issues with large files
                        chunk_size = 8192
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                        f.close()
                        return
                    except Exception as e:
                        callback_output(f"Error serving ISO file: {e}")
                        self.send_error(404, "File not found")
                        return
                
                # For other requests, use the standard handler
                return SimpleHTTPRequestHandler.do_GET(self)
        
        # Start the HTTP server
        try:
            os.chdir(directory)  # Change to the ISO directory
            server_address = ('', 80)
            httpd = HTTPServer(server_address, OpenBMCHandler)
            
            # Start in a separate thread
            server_thread = threading.Thread(target=httpd.serve_forever)
            server_thread.daemon = True
            server_thread.start()
            
            callback_output(f"HTTP server started on port 80 serving {directory}")
        except Exception as e:
            callback_output(f"Failed to start HTTP server: {e}")
            return False
            
        callback_progress(0.50)
        
        # Generate the ISO URL
        iso_url = f"http://{your_ip}:80/{filename}"
        callback_output(f"ISO accessible at {iso_url}")
        
        # OpenBMC Redfish implementation is sensitive to payload format
        # Try different variations that work with different OpenBMC versions
        payloads = [
            # Standard format
            {
                "Image": iso_url,
                "Inserted": True,
                "WriteProtected": True
            },
            # Alternative format for some OpenBMC versions
            {
                "Image": iso_url,
                "Inserted": True
            },
            # Format with no port
            {
                "Image": f"http://{your_ip}/{filename}",
                "Inserted": True
            },
            # Format with explicit transfer method
            {
                "Image": iso_url,
                "TransferMethod": "URI",
                "Inserted": True
            },
            # Attempt with localhost URL (for BMCs that prefer local connections)
            {
                "Image": f"http://localhost:80/{filename}",
                "Inserted": True
            }
        ]
        
        # Try each payload
        success = False
        for i, payload in enumerate(payloads):
            try:
                callback_output(f"Trying payload variation {i+1}...")
                
                # For OpenBMC we may need to use a RAW request
                headers = {"Content-Type": "application/json", "X-Auth-Token": redfish_client.get_session_key()}
                
                # Attempt to insert the media
                response = await asyncio.to_thread(
                    redfish_client.post,
                    insert_action,
                    body=payload
                )
                
                callback_output(f"Response status: {response.status}")
                
                if response.status in [200, 202, 204]:
                    callback_output("Virtual media mounted successfully!")
                    success = True
                    callback_progress(0.90)
                    break
                else:
                    # Log the detailed error for debugging
                    if hasattr(response, 'text'):
                        callback_output(f"InsertMedia failed. Response: {response.text}")
            except Exception as e:
                callback_output(f"Error with payload {i+1}: {e}")
                
        # If standard methods failed, try OpenBMC-specific workarounds
        if not success:
            callback_output("Standard methods failed. Trying OpenBMC-specific methods...")
            
            # Some OpenBMC implementations require a PATCH directly to the virtual media resource
            try:
                callback_output("Trying direct PATCH to virtual media resource...")
                
                patch_payload = {
                    "Image": iso_url,
                    "Inserted": True
                }
                
                patch_response = await asyncio.to_thread(
                    redfish_client.patch,
                    valid_slot,
                    body=patch_payload
                )
                
                if patch_response.status in [200, 202, 204]:
                    callback_output("Virtual media mounted successfully via PATCH!")
                    success = True
                    callback_progress(0.90)
                else:
                    callback_output(f"PATCH method failed: {patch_response.status}")
                    if hasattr(patch_response, 'text'):
                        callback_output(f"Details: {patch_response.text}")
            except Exception as e:
                callback_output(f"Error during PATCH attempt: {e}")
                
        # If all Redfish methods failed, suggest alternative approaches
        if not success:
            callback_output("All standard Redfish methods failed.")
            callback_output("For OpenBMC, you might need to:")
            callback_output("1. Check if your OpenBMC firmware supports virtual media")
            callback_output("2. Try updating the OpenBMC firmware to a newer version")
            callback_output("3. Use the OpenBMC CLI directly via SSH if available")
            callback_output("4. Check the OpenBMC logs for more detailed error information")
            
            # Clean up the HTTP server
            httpd.shutdown()
            httpd.server_close()
            callback_output("HTTP server stopped")
            callback_progress(0)
            return False
            
        callback_output("Virtual media operation successful.")
        callback_output("HTTP server will continue running to serve the ISO.")
        callback_progress(1.0)
        return True
        
    except Exception as e:
        callback_output(f"Error in virtual media operation: {e}")
        return False
    finally:
        await asyncio.to_thread(redfish_client.logout)

async def eject_virtual_media_openbmc(bmc_user, bmc_pass, bmc_ip, callback_progress, callback_output):
    """
    Eject any mounted virtual media, optimized for OpenBMC.
    
    Args:
        bmc_user: BMC username
        bmc_pass: BMC password
        bmc_ip: BMC IP address
        callback_progress: Function to report progress
        callback_output: Function to report output messages
    """
    callback_output("Initializing Redfish client for Virtual Media ejection...")
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    callback_progress(0.20)
    
    try:
        await asyncio.to_thread(redfish_client.login)
        callback_output("Logged in to BMC via Redfish.")
        callback_progress(0.40)
        
        # In OpenBMC, virtual media is typically under the Manager resource
        vm_collection = redfish_client.get("/redfish/v1/Managers/bmc/VirtualMedia")
        
        if vm_collection.status != 200:
            callback_output(f"Failed to get virtual media collection: {vm_collection.status}")
            return False
            
        # Find all available media slots
        media_found = False
        for member in vm_collection.dict.get("Members", []):
            member_path = member.get("@odata.id")
            member_info = redfish_client.get(member_path)
            
            if member_info.status != 200:
                continue
                
            # Check if media is inserted
            if member_info.dict.get("Inserted", False):
                media_found = True
                callback_output(f"Found inserted media at {member_path}")
                
                # Try OpenBMC's preferred method first - EjectMedia action
                actions = member_info.dict.get("Actions", {})
                if "#VirtualMedia.EjectMedia" in actions:
                    eject_action = actions["#VirtualMedia.EjectMedia"]["target"]
                    callback_output("Using EjectMedia action...")
                    
                    eject_response = await asyncio.to_thread(
                        redfish_client.post,
                        eject_action,
                        body={}
                    )
                    
                    if eject_response.status in [200, 202, 204]:
                        callback_output(f"Successfully ejected media from {member_path}")
                    else:
                        callback_output(f"Failed to eject media using action. Status: {eject_response.status}")
                        
                        # Log detailed error
                        if hasattr(eject_response, 'text'):
                            callback_output(f"Details: {eject_response.text}")
                            
                        # If the action failed, try the PATCH method
                        callback_output("Trying PATCH method...")
                        patch_payload = {"Inserted": False}
                        
                        patch_response = await asyncio.to_thread(
                            redfish_client.patch,
                            member_path,
                            body=patch_payload
                        )
                        
                        if patch_response.status in [200, 202, 204]:
                            callback_output(f"Successfully ejected media using PATCH")
                        else:
                            callback_output(f"Failed to eject media using PATCH. Status: {patch_response.status}")
                            
                            # Log detailed error
                            if hasattr(patch_response, 'text'):
                                callback_output(f"Details: {patch_response.text}")
                else:
                    # If no EjectMedia action, use PATCH
                    callback_output("No EjectMedia action found. Using PATCH method...")
                    patch_payload = {"Inserted": False}
                    
                    patch_response = await asyncio.to_thread(
                        redfish_client.patch,
                        member_path,
                        body=patch_payload
                    )
                    
                    if patch_response.status in [200, 202, 204]:
                        callback_output(f"Successfully ejected media using PATCH")
                    else:
                        callback_output(f"Failed to eject media using PATCH. Status: {patch_response.status}")
                        
                        # Log detailed error
                        if hasattr(patch_response, 'text'):
                            callback_output(f"Details: {patch_response.text}")
        
        callback_progress(0.90)
        
        if not media_found:
            callback_output("No mounted virtual media found.")
        else:
            callback_output("Completed virtual media ejection operations.")
            
        # For OpenBMC, we should also reset any boot override settings
        try:
            systems = redfish_client.get("/redfish/v1/Systems")
            if systems.status == 200:
                for member in systems.dict.get("Members", []):
                    system_path = member.get("@odata.id")
                    
                    boot_payload = {
                        "Boot": {
                            "BootSourceOverrideEnabled": "Disabled"
                        }
                    }
                    
                    boot_response = await asyncio.to_thread(
                        redfish_client.patch,
                        system_path,
                        body=boot_payload
                    )
                    
                    if boot_response.status in [200, 202, 204]:
                        callback_output("Reset boot override settings.")
        except Exception as e:
            callback_output(f"Note: Error resetting boot override: {e}")
        
        callback_progress(1.0)
        return True
        
    except Exception as e:
        callback_output(f"Error ejecting virtual media: {e}")
        return False
    finally:
        await asyncio.to_thread(redfish_client.logout)
        await asyncio.sleep(2)
        callback_progress(0)