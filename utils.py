import time
import asyncio
import serial
import redfish
import threading
import weakref


# Global set to track serial connections - initialized properly
_serial_connections = set()


def register_serial_connection(ser):
    """Register a serial connection for cleanup tracking"""
    global _serial_connections
    _serial_connections.add(ser)

def cleanup_all_serial_connections():
    """Clean up all registered serial connections"""
    global _serial_connections
    connections_cleaned = 0
    for ser in list(_serial_connections):
        try:
            if hasattr(ser, 'is_open') and ser.is_open:
                ser.close()
                connections_cleaned += 1
        except:
            pass
    _serial_connections.clear()  # Clear the set after cleanup
    return connections_cleaned


def read_serial_data(ser, command, delay):
    """Improved serial data reading with better error handling and resource management"""
    global _serial_connections
    
    try:
        # Register this connection for tracking
        register_serial_connection(ser)
        
        # Clear input buffer before sending command
        if hasattr(ser, 'reset_input_buffer'):
            ser.reset_input_buffer()
        
        time.sleep(delay)
        ser.write(command.encode('utf-8'))
        time.sleep(2)  
        
        # Use read_all() with timeout handling
        start_time = time.time()
        response = b""
        timeout = 10  # 10 second timeout
        
        while time.time() - start_time < timeout:
            if ser.in_waiting:
                chunk = ser.read(ser.in_waiting)
                if chunk:
                    response += chunk
                else:
                    break
            time.sleep(0.1)
        
        return response.decode('utf-8', errors='ignore')
        
    except serial.SerialTimeoutException:
        print("Serial timeout occurred")
        return ""
    except serial.SerialException as e:
        print(f"Serial error in read_serial_data: {e}")
        return ""
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
        await asyncio.sleep(1)

        # Send password
        callback_output("Sending password...")
        ser.write(passw.encode("utf-8"))
        await asyncio.sleep(1)

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
    
def create_serial_connection(device, baudrate=115200, timeout=5):
    """Create a serial connection with proper error handling and registration"""
    try:
        ser = serial.Serial(device, baudrate=baudrate, timeout=timeout)
        ser.dtr = True
        register_serial_connection(ser)
        return ser
    except serial.SerialException as e:
        print(f"Failed to create serial connection to {device}: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error creating serial connection: {e}")
        return None

# Context manager for serial connections
class ManagedSerialConnection:
    """Context manager for serial connections that ensures proper cleanup"""
    
    def __init__(self, device, baudrate=115200, timeout=5):
        self.device = device
        self.baudrate = baudrate
        self.timeout = timeout
        self.connection = None
    
    def __enter__(self):
        self.connection = create_serial_connection(self.device, self.baudrate, self.timeout)
        return self.connection
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.connection and hasattr(self.connection, 'is_open'):
            try:
                if self.connection.is_open:
                    self.connection.close()
            except:
                pass
    
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