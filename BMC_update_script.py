import requests
import urllib3
import paramiko

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
            




