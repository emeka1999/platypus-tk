import urllib3
import serial
import time
import redfish
import requests
import threading
import os
from http.server import SimpleHTTPRequestHandler, HTTPServer
import asyncio



# Suppress the warning for unverified HTTPS requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



async def bmc_update(bmc_user, bmc_pass, bmc_ip, fw_content, callback_progress):
    print("Initialzing Red Fish client...")
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    callback_progress(0.25)
    
    try:
        await asyncio.to_thread(redfish_client.login)
        update_service = redfish_client.get("/redfish/v1/UpdateService")
        if update_service.status != 200:
            print("Failed to find the update service.")
            return

        callback_progress(0.50)
        print("Logged in.")

        update_service_url = update_service.dict["@odata.id"]

        # Firmware update
        headers = {"Content-Type": "application/octet-stream"}
        print("Sending update request...")
        response = await asyncio.to_thread(redfish_client.post, f"{update_service_url}/update", body=fw_content, headers=headers)
        callback_progress(0.75)

        if response.status in [200, 202]:
            print("Update initiated successfully:", response.text)
            callback_progress(1)
        else:
            print("Failed to initiate firmware update. Response code:", response.status)
    except Exception as e:
        print("Error occurred:", e)
    finally:
        await asyncio.to_thread(redfish_client.logout)
    
    await asyncio.sleep(5)
    callback_progress(0)

async def set_ip(bmc_ip, bmc_user, bmc_pass, callback_progress):
    ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"
    command = f"ifconfig eth0 up {bmc_ip}\n"

    callback_progress(0.25)
    print("Running...")

    try:
        # Check if already logged in by looking for the command prompt
        initial_prompt = ser.read_until(b'# ')
            
        if b'#' not in initial_prompt:
            # Not logged in, proceed with login
            ser.write(user.encode('utf-8'))
            await asyncio.sleep(2)
            ser.write(passw.encode('utf-8'))
            await asyncio.sleep(2)
        
        callback_progress(0.5)
        print("Logged in.")

        # Send the command to set the IP
        ser.write(command.encode('utf-8'))

        # Reading the response from the command
        response = ser.read_until(b'\n')
        print(response.decode('utf-8'))

        callback_progress(0.75)
        print("Setting IP...")
    except Exception as e:
        print(f"Error: {e}")
    
    callback_progress(1)
    print("IP set successfully.")
    await asyncio.sleep(5)
    callback_progress(0)



server_running = False

def start_server(directory, port):
    global server_running
    if server_running:
        print("Server is already running.")
        return

    os.chdir(directory)
    handler = SimpleHTTPRequestHandler
    httpd = HTTPServer(('0.0.0.0', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    server_running = True
    print(f"Serving files from {directory} on port {port}")



def flasher(bmc_user, bmc_pass, flash_file, my_ip):
    directory = os.path.dirname(flash_file)
    file_name = os.path.basename(flash_file)
    port = 5000

    start_server(directory, port)

    ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"

    try:
        initial_prompt = ser.read_until(b'# ')
            
        if b'#' not in initial_prompt:
            ser.write(user.encode('utf-8'))
            time.sleep(2)
            ser.write(passw.encode('utf-8'))
            time.sleep(5)
        
        url = f"http://{my_ip}:{port}/{file_name}"
        curl_command = f"curl -o {file_name} {url}\n"
        ser.write(curl_command.encode('utf-8'))
        time.sleep(5)
        print('Curl command sent.')

        command = "echo 0 > /sys/block/mmcblk0boot0/force_ro\n"
        ser.write(command.encode('utf-8'))
        time.sleep(4)
        print('Changed MMC to RW')

        command = f'dd if={file_name} of=/dev/mmcblk0boot0 bs=512 seek=256\n'
        ser.write(command.encode('utf-8'))
        time.sleep(10)
        print("Flashing complete")
    except serial.SerialException as e:
        print(f"Serial Error: {e}")
    finally:
        ser.close()



def reset_ip(bmc_user, bmc_pass, bmc_ip):
    url = f"https://{bmc_ip}/redfish/v1/Managers/bmc/Actions/Manager.ResetToDefaults"
    headers = {"Content-Type": "application/json"}
    payload = {"ResetToDefaultsType": "ResetAll"}

    try:
        response = requests.post(url, json=payload, headers=headers, auth=(bmc_user, bmc_pass), verify=False)
        if response.status_code == 200:
            print("BMC reset to factory defaults successfully.")
        else:
            print("Failed to reset BMC. Response code:", response.status_code)
            print(response.json())
    except Exception as e:
        print("Error occurred:", e)
           


   
