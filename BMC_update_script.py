import requests
import urllib3
import serial
import time

# Creates a serial connection with the bmc, waits for it to initalize, writes the command, and prints the command line response

def set_ip(bmc_ip, bmc_user, bmc_pass):
    ser = serial.Serial('/dev/ttyUSB0', 115200)
    user = f"{bmc_user}\n"
    passw = f"{bmc_pass}\n"

    try:
        time.sleep(2)
        # login
        ser.write(user.encode('utf-8'))
        time.sleep(2)
        ser.write(passw.encode('utf-8'))
        time.sleep(5)
        # Sending the command to set the IP
        command = f"ifconfig eth0 up {bmc_ip}\n"
        ser.write(command.encode('utf-8'))

        # Reading the prompt after login 
        ser.read_until(b'$ ')

        # Reading the response from the command
        response = ser.read_until(b'\n')
        print("Response from BMC:", response.decode('utf-8'))

    finally:
        ser.close()
    


# Suppress the warning for unverified HTTPS requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Grabs the authentication token from Redfish API
# A request is made to redfish and the response is then parsed for the token

def get_token(bmc_ip, bmc_user, bmc_pass):
    response = requests.post(f"https://{bmc_ip}/redfish/v1/SessionService/Sessions",
                             headers={"Content-Type": "application/json"},
                             json={"UserName": bmc_user, "Password": bmc_pass},
                             verify=False)  
    response_headers = response.headers
    token = response_headers.get('X-Auth-Token')
    return token

# Updates the BMC firmware
# Reads the firmware file and 

def fw_update(fw_file, bmc_ip, token):
        with open(fw_file, 'rb') as firmware:
            response = requests.post(f"https://{bmc_ip}/redfish/v1/UpdateService/update",
                                     headers={"X-Auth-Token": token, "Content-Type": "application/octet-stream"},
                                     data=firmware,
                                     verify=False)  
            return response.text
            



     