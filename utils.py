import time
import asyncio
import serial
import redfish


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


async def login(bmc_user, bmc_pass, serial_device, callback_output):
    """Logs into the BMC using the serial device."""
    try:
        callback_output(f"Opening serial connection to {serial_device}...")
        ser = serial.Serial(serial_device, baudrate=115200, timeout=1)
        ser.dtr = True

        user = f"{bmc_user}\n"
        passw = f"{bmc_pass}\n"

        # Send username
        callback_output("Sending username...")
        ser.write(user.encode("utf-8"))
        await asyncio.sleep(2)

        # Send password
        callback_output("Sending password...")
        ser.write(passw.encode("utf-8"))
        await asyncio.sleep(2)

        # Read response from the serial device
        response = ser.read(1024).decode("utf-8").strip()
        if response:
            callback_output(f"Response from BMC: {response}")
        else:
            callback_output("No explicit response received from BMC.")

        # Determine login success
        if "login failed" in response.lower():
            ser.close()
            return "Login failed. Check credentials."

        # If no failure detected, assume success
        ser.close()
        return "Login successful."
    except serial.SerialException as e:
        callback_output(f"Serial error: {e}")
        return "Login failed due to serial error."
    except Exception as e:
        callback_output(f"Error during login: {e}")
        return "Login failed due to an unexpected error."
    

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