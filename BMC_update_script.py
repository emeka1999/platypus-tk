import requests
import urllib3
import serial
import time

# Creates a serial connection with the bmc, waits for it to initalize, writes the command, and prints the command line response

ser = serial.Serial('/dev/ttyUSB0', 115200)

try:
    time.sleep(2)
    command = "ifconfig eth0 up 10.1.2.20\n"
    ser.write(command.encode('utf-8'))
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
            

# We want a function that will set the BMC IP through a serial connection as the BMC doesn't already have an IP
# Example of setting BMC IP though command line: (anything * may change)
# sudo screen /dev/ttyUSB0 115200
# sudo ifconfig eno1* up 10.1.2.4*


     