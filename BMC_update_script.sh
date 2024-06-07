#!/bin/bash

BMC_IP="10.1.2.3"
BMC_USER="root"
BMC_PASS="0penBmc"
fw_file="/home/intern/snuc-nanobmc/obmc-phosphor-image-snuc-nanobmc-update.tar.gz"


get_token() {
	token=$(curl -k -D - -X POST https://${BMC_IP}/redfish/v1/SessionService/Sessions -H "Content-Type: application/json" -d "{\"UserName\": \"${BMC_USER}\", \"Password\": \"${BMC_PASS}\" }" | grep -i "X-Auth-Token" | awk '{print $2}' | tr -d '\r')
}


get_token

# Updates the firmware of the bmc

curl -k -H "X-Auth-Token: $token" -H "Content-Type: application/octet-stream" \
	-X POST -T "$fw_file" "https://${BMC_IP}/redfish/v1/UpdateService/update"
