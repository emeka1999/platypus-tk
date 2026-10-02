from http.server import SimpleHTTPRequestHandler, HTTPServer
import urllib3
import asyncio
import time
import redfish
import redfish.rest.v1 as redfish_v1
import serial 
import os 
import socket
import threading 


from utils import monitor_task, read_serial_data, login
from network import stop_server, start_server, set_ip

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Boot override / power cycle via Redfish (ComputerSystem) ---
# Mirrors the logic used in the standalone Redfish control panel's Power tab
# (discover the first Systems member, then PATCH Boot.* or POST
# Actions/ComputerSystem.Reset against it).

def _discover_system_endpoint(redfish_client):
    resp = redfish_client.get("/redfish/v1/Systems")
    if resp.status != 200:
        raise RuntimeError(f"Failed to read /redfish/v1/Systems (HTTP {resp.status})")
    members = resp.dict.get("Members", [])
    if not members:
        raise RuntimeError("No ComputerSystem members found under /redfish/v1/Systems.")
    return members[0]["@odata.id"]


def set_boot_override(bmc_user, bmc_pass, bmc_ip, target, enabled="Once"):
    """Set the next-boot override target (e.g. 'Pxe', 'Cd', 'BiosSetup') via
    Redfish. Blocking/synchronous - call from a background thread."""
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    redfish_client.login()
    try:
        system_uri = _discover_system_endpoint(redfish_client)
        body = {"Boot": {"BootSourceOverrideTarget": target, "BootSourceOverrideEnabled": enabled}}
        resp = redfish_client.patch(system_uri, body=body)
        if resp.status not in (200, 202, 204):
            raise RuntimeError(f"Boot override PATCH failed (HTTP {resp.status}): {resp.text}")
        return f"Next boot set to {target} ({enabled})."
    finally:
        redfish_client.logout()


def _system_reset(bmc_user, bmc_pass, bmc_ip, reset_type, success_message):
    """Shared implementation for the power-action helpers below. Blocking/
    synchronous - call from a background thread."""
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    redfish_client.login()
    try:
        system_uri = _discover_system_endpoint(redfish_client)
        resp = redfish_client.post(f"{system_uri}/Actions/ComputerSystem.Reset", body={"ResetType": reset_type})
        if resp.status not in (200, 202, 204):
            raise RuntimeError(f"{reset_type} request failed (HTTP {resp.status}): {resp.text}")
        return success_message
    finally:
        redfish_client.logout()


def power_on_host(bmc_user, bmc_pass, bmc_ip):
    """Power on the host via Redfish ComputerSystem.Reset (ResetType=On)."""
    return _system_reset(bmc_user, bmc_pass, bmc_ip, "On", "Power on command sent.")


def power_off_host(bmc_user, bmc_pass, bmc_ip):
    """Force power off the host via Redfish ComputerSystem.Reset
    (ResetType=ForceOff)."""
    return _system_reset(bmc_user, bmc_pass, bmc_ip, "ForceOff", "Power off command sent.")


def reboot_host(bmc_user, bmc_pass, bmc_ip):
    """Force-restart the host via Redfish ComputerSystem.Reset
    (ResetType=ForceRestart)."""
    return _system_reset(bmc_user, bmc_pass, bmc_ip, "ForceRestart", "Reboot command sent.")


# --- System inventory (BIOS/BMC versions, NICs, drives) via Redfish ---

def _get_json(redfish_client, uri):
    """GET a Redfish resource and return its JSON dict, or None if the
    request failed - inventory gathering should degrade gracefully (show
    what's available) rather than fail entirely because one sub-resource
    a particular BMC doesn't implement returned an error."""
    try:
        resp = redfish_client.get(uri)
        if resp.status == 200:
            return resp.dict
    except Exception:
        pass
    return None


def _bytes_to_gb(num_bytes):
    if not isinstance(num_bytes, (int, float)):
        return None
    return round(num_bytes / (1000 ** 3), 1)


def get_system_inventory(bmc_user, bmc_pass, bmc_ip):
    """
    Gather a snapshot of host/BMC identity, firmware versions, network
    interfaces, and storage drives via Redfish. Blocking/synchronous - call
    from a background thread. Returns a dict:

        {
            "manufacturer": str, "model": str, "serial_number": str,
            "part_number": str, "bios_version": str, "bmc_version": str,
            "bmc_model": str, "cpu": str, "memory_gb": float,
            "nics": [ {"name","mac","link_status","speed_mbps","ipv4"} ],
            "drives": [ {"name","model","capacity_gb","media_type",
                         "protocol","health"} ],
        }

    Any field that couldn't be read (BMC doesn't implement that resource,
    a sub-request failed, etc.) is left as None/empty rather than raising,
    so a partial inventory is still useful.

    Raises ConnectionError (with a message naming the IP) if the BMC can't
    be reached at all - callers can catch that specifically to tell the
    user the IP looks wrong, as opposed to a credentials or Redfish-schema
    problem once a connection was actually established.
    """
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    try:
        redfish_client.login()
    except redfish_v1.InvalidCredentialsError as e:
        raise RuntimeError(f"BMC at {bmc_ip} rejected the username/password.") from e
    except (redfish_v1.ServerDownOrUnreachableError, redfish_v1.RetriesExhaustedError,
            socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as e:
        raise ConnectionError(f"Could not reach {bmc_ip} - check that the BMC IP is correct.") from e

    try:
        info = {
            "manufacturer": None, "model": None, "serial_number": None,
            "part_number": None, "bios_version": None, "bmc_version": None,
            "bmc_model": None, "cpu": None, "memory_gb": None,
            "nics": [], "drives": [],
        }

        system_uri = _discover_system_endpoint(redfish_client)
        system = _get_json(redfish_client, system_uri) or {}

        info["manufacturer"] = system.get("Manufacturer")
        info["model"] = system.get("Model")
        info["serial_number"] = system.get("SerialNumber")
        info["part_number"] = system.get("PartNumber")
        info["bios_version"] = system.get("BiosVersion")

        proc_summary = system.get("ProcessorSummary", {}) or {}
        cpu_model = proc_summary.get("Model")
        cpu_count = proc_summary.get("Count")
        if cpu_model:
            info["cpu"] = f"{cpu_count}x {cpu_model}" if cpu_count else cpu_model

        mem_summary = system.get("MemorySummary", {}) or {}
        info["memory_gb"] = mem_summary.get("TotalSystemMemoryGiB")

        # BMC firmware version/model
        managers = _get_json(redfish_client, "/redfish/v1/Managers") or {}
        manager_members = managers.get("Members", [])
        if manager_members:
            manager = _get_json(redfish_client, manager_members[0]["@odata.id"]) or {}
            info["bmc_version"] = manager.get("FirmwareVersion")
            info["bmc_model"] = manager.get("Model")

        # NICs
        eth_uri = (system.get("EthernetInterfaces") or {}).get("@odata.id")
        if not eth_uri:
            eth_uri = f"{system_uri}/EthernetInterfaces"
        eth_collection = _get_json(redfish_client, eth_uri) or {}
        for member in eth_collection.get("Members", []):
            nic = _get_json(redfish_client, member["@odata.id"])
            if not nic:
                continue
            ipv4_list = nic.get("IPv4Addresses") or []
            ipv4 = ipv4_list[0].get("Address") if ipv4_list else None
            info["nics"].append({
                "name": nic.get("Id") or nic.get("Name"),
                "mac": nic.get("MACAddress"),
                "link_status": nic.get("LinkStatus"),
                "speed_mbps": nic.get("SpeedMbps"),
                "ipv4": ipv4,
            })

        # Drives (via each Storage controller's Drives collection)
        storage_uri = (system.get("Storage") or {}).get("@odata.id")
        if not storage_uri:
            storage_uri = f"{system_uri}/Storage"
        storage_collection = _get_json(redfish_client, storage_uri) or {}
        for storage_member in storage_collection.get("Members", []):
            controller = _get_json(redfish_client, storage_member["@odata.id"])
            if not controller:
                continue
            for drive_link in controller.get("Drives", []):
                drive = _get_json(redfish_client, drive_link["@odata.id"])
                if not drive:
                    continue
                status = drive.get("Status", {}) or {}
                info["drives"].append({
                    "name": drive.get("Name") or drive.get("Id"),
                    "model": drive.get("Model"),
                    "capacity_gb": _bytes_to_gb(drive.get("CapacityBytes")),
                    "media_type": drive.get("MediaType"),
                    "protocol": drive.get("Protocol"),
                    "health": status.get("Health"),
                })

        return info
    finally:
        redfish_client.logout()


# --- Fans / thermal / power sensors via Redfish ---

def get_sensors(bmc_user, bmc_pass, bmc_ip):
    """
    Gather fan, temperature, voltage, and power-supply readings from every
    Chassis via Redfish (Thermal/Power sub-resources). Blocking/synchronous
    - call from a background thread. Returns:

        {
            "fans": [ {"name","reading","units","health"} ],
            "temperatures": [ {"name","reading_c","health"} ],
            "voltages": [ {"name","reading_volts","health"} ],
            "power_supplies": [ {"name","status_health","input_watts"} ],
        }

    Each Chassis that doesn't implement a given sub-resource just
    contributes nothing for it rather than raising, so a partial system
    (e.g. no PSU telemetry) still shows what is available. Raises
    ConnectionError specifically if the BMC can't be reached at all.
    """
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    try:
        redfish_client.login()
    except redfish_v1.InvalidCredentialsError as e:
        raise RuntimeError(f"BMC at {bmc_ip} rejected the username/password.") from e
    except (redfish_v1.ServerDownOrUnreachableError, redfish_v1.RetriesExhaustedError,
            socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as e:
        raise ConnectionError(f"Could not reach {bmc_ip} - check that the BMC IP is correct.") from e

    try:
        result = {"fans": [], "temperatures": [], "voltages": [], "power_supplies": []}

        chassis_collection = _get_json(redfish_client, "/redfish/v1/Chassis") or {}
        for member in chassis_collection.get("Members", []):
            chassis_uri = member["@odata.id"]
            fans_before, temps_before, volts_before = len(result["fans"]), len(result["temperatures"]), len(result["voltages"])

            thermal = _get_json(redfish_client, f"{chassis_uri}/Thermal")
            if thermal:
                for fan in thermal.get("Fans", []):
                    status = fan.get("Status", {}) or {}
                    result["fans"].append({
                        "name": fan.get("Name") or fan.get("FanName") or fan.get("MemberId"),
                        "reading": fan.get("Reading"),
                        "units": fan.get("ReadingUnits"),
                        "health": status.get("Health"),
                    })
                for temp in thermal.get("Temperatures", []):
                    status = temp.get("Status", {}) or {}
                    result["temperatures"].append({
                        "name": temp.get("Name"),
                        "reading_c": temp.get("ReadingCelsius"),
                        "health": status.get("Health"),
                    })

            power = _get_json(redfish_client, f"{chassis_uri}/Power")
            if power:
                for voltage in power.get("Voltages", []):
                    status = voltage.get("Status", {}) or {}
                    result["voltages"].append({
                        "name": voltage.get("Name"),
                        "reading_volts": voltage.get("ReadingVolts"),
                        "health": status.get("Health"),
                    })
                for psu in power.get("PowerSupplies", []):
                    status = psu.get("Status", {}) or {}
                    result["power_supplies"].append({
                        "name": psu.get("Name") or psu.get("MemberId"),
                        "status_health": status.get("Health"),
                        "input_watts": psu.get("PowerInputWatts") or psu.get("LastPowerOutputWatts"),
                    })

            # Newer Redfish schemas moved sensor readings out of the
            # legacy Thermal/Power singleton resources and into a flat
            # Chassis/{id}/Sensors collection of individual Sensor
            # resources instead - some BMCs populate one, some the other,
            # some a mix (e.g. Fans still under Thermal but Temperatures
            # only under Sensors). So this only asks Sensors for whichever
            # categories THIS chassis didn't contribute above, rather than
            # assuming one schema or the other (checked per-chassis, not
            # globally, so a later chassis with no legacy data still gets
            # its own fallback even if an earlier chassis already did).
            need_fans = len(result["fans"]) == fans_before
            need_temps = len(result["temperatures"]) == temps_before
            need_volts = len(result["voltages"]) == volts_before
            if need_fans or need_temps or need_volts:
                sensors_collection = _get_json(redfish_client, f"{chassis_uri}/Sensors") or {}
                for sensor_member in sensors_collection.get("Members", []):
                    sensor = _get_json(redfish_client, sensor_member["@odata.id"])
                    if not sensor:
                        continue
                    reading_type = (sensor.get("ReadingType") or "").lower()
                    status = sensor.get("Status", {}) or {}
                    name = sensor.get("Name")
                    reading = sensor.get("Reading")
                    health = status.get("Health")

                    if need_temps and reading_type == "temperature":
                        result["temperatures"].append({
                            "name": name, "reading_c": reading, "health": health,
                        })
                    elif need_fans and reading_type in ("rotational", "fan", "fanspeed"):
                        result["fans"].append({
                            "name": name, "reading": reading,
                            "units": sensor.get("ReadingUnits") or "RPM", "health": health,
                        })
                    elif need_volts and reading_type == "voltage":
                        result["voltages"].append({
                            "name": name, "reading_volts": reading, "health": health,
                        })

        return result
    finally:
        redfish_client.logout()



# --- Event log (clear via Redfish LogService.ClearLog) ---

def _login_client(bmc_user, bmc_pass, bmc_ip):
    """Shared login step (with the same connection/credential error
    translation used elsewhere in this module) for standalone helpers that
    don't already have their own copy of this logic."""
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    try:
        redfish_client.login()
    except redfish_v1.InvalidCredentialsError as e:
        raise RuntimeError(f"BMC at {bmc_ip} rejected the username/password.") from e
    except (redfish_v1.ServerDownOrUnreachableError, redfish_v1.RetriesExhaustedError,
            socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as e:
        raise ConnectionError(f"Could not reach {bmc_ip} - check that the BMC IP is correct.") from e
    return redfish_client


def _discover_log_services(redfish_client, base_uri):
    """List LogService URIs under a Systems/Managers member (e.g.
    '/redfish/v1/Systems/1'). Returns [] if that resource has no
    LogServices collection (or doesn't support one)."""
    collection = _get_json(redfish_client, f"{base_uri}/LogServices")
    if not collection:
        return []
    return [m["@odata.id"] for m in collection.get("Members", [])]


def clear_event_log(bmc_user, bmc_pass, bmc_ip):
    """
    Clear the BMC's event log(s) via the standard Redfish
    LogService.ClearLog action. Looks under both the System and the
    Manager for LogServices collections (BMC vendors differ on where they
    put EventLog - OpenBMC/bmcweb typically uses
    /redfish/v1/Systems/{id}/LogServices/EventLog), and clears every log
    service found there, not just ones literally named "EventLog" (some
    BMCs use different naming, e.g. "SEL"). Returns a summary string of
    what was actually cleared.
    """
    redfish_client = _login_client(bmc_user, bmc_pass, bmc_ip)
    try:
        log_uris = []

        try:
            system_uri = _discover_system_endpoint(redfish_client)
            log_uris.extend(_discover_log_services(redfish_client, system_uri))
        except Exception:
            pass

        managers = _get_json(redfish_client, "/redfish/v1/Managers") or {}
        manager_members = managers.get("Members", [])
        if manager_members:
            log_uris.extend(_discover_log_services(redfish_client, manager_members[0]["@odata.id"]))

        if not log_uris:
            raise RuntimeError("No LogServices found on this BMC (checked under both Systems and Managers).")

        cleared, failed = [], []
        for log_uri in log_uris:
            try:
                resp = redfish_client.post(f"{log_uri}/Actions/LogService.ClearLog", body={})
                if resp.status in (200, 202, 204):
                    cleared.append(log_uri)
                else:
                    failed.append(f"{log_uri} (HTTP {resp.status})")
            except Exception as e:
                failed.append(f"{log_uri} ({e})")

        if not cleared:
            raise RuntimeError(f"Failed to clear any log service: {'; '.join(failed) or 'unknown error'}")

        names = ", ".join(u.rsplit("/", 1)[-1] for u in cleared)
        summary = f"Cleared {len(cleared)} log service(s): {names}."
        if failed:
            summary += f" ({len(failed)} failed: {'; '.join(failed)})"
        return summary
    finally:
        redfish_client.logout()


# --- Virtual Media (mount/unmount an ISO/IMG) via Redfish ---

def _discover_virtual_media_slots(redfish_client):
    """Find VirtualMedia slot endpoints. Usually lives under the Manager,
    but some BMCs expose it under Systems instead - try both."""
    candidates = []

    managers = _get_json(redfish_client, "/redfish/v1/Managers") or {}
    manager_members = managers.get("Members", [])
    if manager_members:
        manager_uri = manager_members[0]["@odata.id"]
        manager = _get_json(redfish_client, manager_uri) or {}
        vm_link = (manager.get("VirtualMedia") or {}).get("@odata.id")
        candidates.append(vm_link or f"{manager_uri}/VirtualMedia")

    try:
        system_uri = _discover_system_endpoint(redfish_client)
        candidates.append(f"{system_uri}/VirtualMedia")
    except Exception:
        pass

    for uri in candidates:
        collection = _get_json(redfish_client, uri)
        if collection and collection.get("Members"):
            return [m["@odata.id"] for m in collection["Members"]]
    return []


def _virtual_media_status(data, endpoint):
    return {
        "endpoint": endpoint,
        "name": data.get("Id") or data.get("Name") or endpoint.rsplit("/", 1)[-1],
        "inserted": data.get("Inserted"),
        "image": data.get("Image"),
        "write_protected": data.get("WriteProtected"),
        "media_types": data.get("MediaTypes") or [],
    }


def get_virtual_media_slots(bmc_user, bmc_pass, bmc_ip):
    """List available VirtualMedia slots with their current status."""
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    redfish_client.login()
    try:
        endpoints = _discover_virtual_media_slots(redfish_client)
        slots = []
        for ep in endpoints:
            data = _get_json(redfish_client, ep) or {}
            slots.append(_virtual_media_status(data, ep))
        return slots
    finally:
        redfish_client.logout()


def get_virtual_media_status(bmc_user, bmc_pass, bmc_ip, endpoint):
    """Refresh a single VirtualMedia slot's status."""
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    redfish_client.login()
    try:
        data = _get_json(redfish_client, endpoint) or {}
        return _virtual_media_status(data, endpoint)
    finally:
        redfish_client.logout()


def insert_virtual_media(bmc_user, bmc_pass, bmc_ip, endpoint, image_url,
                          username=None, password=None, write_protected=True):
    """Mount an image (ISO/IMG) to a VirtualMedia slot via Redfish
    InsertMedia. `image_url` must be reachable BY THE BMC over the network
    (http/https, typically) - a local filesystem path won't work unless
    it's hosted somewhere the BMC can reach first."""
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    redfish_client.login()
    try:
        body = {"Image": image_url, "Inserted": True, "WriteProtected": write_protected}
        if username:
            body["UserName"] = username
        if password:
            body["Password"] = password
        resp = redfish_client.post(f"{endpoint}/Actions/VirtualMedia.InsertMedia", body=body)
        if resp.status not in (200, 202, 204):
            raise RuntimeError(f"InsertMedia failed (HTTP {resp.status}): {resp.text}")
        return f"Mounted {image_url}."
    finally:
        redfish_client.logout()


def eject_virtual_media(bmc_user, bmc_pass, bmc_ip, endpoint):
    """Unmount whatever is currently attached to a VirtualMedia slot.

    BMC Redfish implementations vary here: most support the
    VirtualMedia.EjectMedia action, but some only implement unmount as a
    plain PATCH clearing Inserted/Image on the resource itself, or accept
    the EjectMedia POST but don't actually act on it. So this tries
    EjectMedia first, falls back to PATCH on failure, and - either way -
    re-reads the resource afterward to confirm it's actually ejected
    rather than trusting a bare "200 OK" that didn't really do anything.
    """
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    redfish_client.login()
    try:
        errors = []

        resp = redfish_client.post(f"{endpoint}/Actions/VirtualMedia.EjectMedia", body={})
        if resp.status not in (200, 202, 204):
            errors.append(f"EjectMedia action: HTTP {resp.status}: {resp.text}")

            patch_resp = redfish_client.patch(endpoint, body={"Inserted": False, "Image": None})
            if patch_resp.status not in (200, 202, 204):
                errors.append(f"PATCH fallback: HTTP {patch_resp.status}: {patch_resp.text}")
                raise RuntimeError("EjectMedia failed via both action and PATCH fallback: " + " | ".join(errors))

        # Confirm it actually ejected rather than trusting a 200 that
        # didn't really do anything - some minimal implementations accept
        # the request without acting on it.
        after = _get_json(redfish_client, endpoint) or {}
        if after.get("Inserted"):
            raise RuntimeError(
                f"BMC reported success but the slot still shows Inserted=True "
                f"(Image={after.get('Image')!r}) - this BMC may not fully "
                f"support VirtualMedia.EjectMedia."
            )

        return "Unmounted."
    finally:
        redfish_client.logout()


def upload_bmc_certificate(bmc_user, bmc_pass, bmc_ip, cert_pem):
    """Upload a PEM certificate to the BMC's trust store, so it'll trust a
    locally-hosted HTTPS server serving a virtual media image (self-signed
    certs otherwise get rejected when the BMC fetches the image)."""
    redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
    redfish_client.login()
    try:
        managers = _get_json(redfish_client, "/redfish/v1/Managers") or {}
        manager_members = managers.get("Members", [])
        if not manager_members:
            raise RuntimeError("No Manager found to upload the certificate to.")
        manager_uri = manager_members[0]["@odata.id"]
        body = {"CertificateString": cert_pem, "CertificateType": "PEM"}
        resp = redfish_client.post(f"{manager_uri}/Truststore/Certificates", body=body)
        if resp.status not in (200, 201, 202, 204):
            raise RuntimeError(f"Certificate upload failed (HTTP {resp.status}): {resp.text}")
        return "Certificate uploaded."
    finally:
        redfish_client.logout()


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
async def power_host(callback_output, serial_device, bmc_user=None, bmc_pass=None):
    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True
    command = f"obmcutil poweron\n"

    callback_output("Running...")

    try:
        # Same issue as set_ip: sending this straight into an
        # unauthenticated login prompt just types it in as the next
        # username attempt (see the screenshot - "obmcutil poweron"
        # showing up as a rejected login). Check first and log in if
        # needed.
        ser.write(b"\n")
        await asyncio.sleep(0.5)
        initial_response = ser.read_all().decode('utf-8', errors='ignore')
        if "login:" in initial_response.lower() and bmc_user and bmc_pass:
            callback_output("Not logged in yet - logging in first...")
            ser.write(f"{bmc_user}\n".encode('utf-8'))
            await asyncio.sleep(1)
            ser.write(f"{bmc_pass}\n".encode('utf-8'))
            await asyncio.sleep(1.5)
            ser.read_all()  # drain the login response, not needed here

        ser.write(command.encode('utf-8'))

        response = ser.read_until(b'\n')

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
async def flash_eeprom(flash_file, my_ip, callback_progress, callback_output, serial_device, bmc_user=None, bmc_pass=None):
    directory = os.path.dirname(flash_file)
    file_name = os.path.basename(flash_file)
    port = 80

    # Start HTTP server
    httpd = start_server(directory, port, callback_output)
    callback_progress(0.2)

    ser = serial.Serial(serial_device, 115200, timeout=1)
    ser.dtr = True

    try:
        # Same issue as set_ip/power_host: every command below just gets
        # typed as the next login attempt's username if we're not
        # actually authenticated yet (see the screenshot - curl/rm
        # commands showing up as rejected login attempts, one after
        # another). Check first and log in if needed.
        ser.write(b"\n")
        await asyncio.sleep(0.5)
        initial_response = ser.read_all().decode('utf-8', errors='ignore')
        if "login:" in initial_response.lower() and bmc_user and bmc_pass:
            callback_output("Not logged in yet - logging in first...")
            ser.write(f"{bmc_user}\n".encode('utf-8'))
            await asyncio.sleep(1)
            ser.write(f"{bmc_pass}\n".encode('utf-8'))
            await asyncio.sleep(1.5)
            ser.read_all()  # drain the login response, not needed here

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

        ser = serial.Serial(serial_device, 115200, timeout=0.1)

        # Setting IP Address (bootloader)
        callback_output("Setting IP Address (bootloader)...")
        command = f'setenv ipaddr {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.20)

        # Grabbing virtual restore image
        callback_output("Grabbing virtual restore image...")
        command = f'wget ${{loadaddr}} {my_ip}:/obmc-rescue-image-snuc-{type}.itb; bootm\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.40)

        # Setting IP Address (BMC)
        callback_output("Setting IP Address (BMC)...")
        await asyncio.sleep(20)
        command = f'ifconfig eth0 up {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.50)

        # Grabbing restore image
        callback_output("Grabbing restore image to your system...")
        command = f"curl -o obmc-phosphor-image-snuc-{type}.wic.xz {my_ip}/obmc-phosphor-image-snuc-{type}.wic.xz\n"
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.60)

        # Grabbing the mapping file
        callback_output("Grabbing the mapping file...")
        command = f'curl -o obmc-phosphor-image-snuc-{type}.wic.bmap {my_ip}/obmc-phosphor-image-snuc-{type}.wic.bmap\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)
        callback_progress(0.90)

        # Flashing the restore image
        callback_output("Flashing the restore image to your system...")
        command = f'bmaptool copy obmc-phosphor-image-snuc-{type}.wic.xz /dev/mmcblk0\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)

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

async def flash_emmc_post_password(bmc_ip, directory, my_ip, dd_value, callback_progress, callback_output, serial_device, bmc_user="root", default_password="0penBmc123", new_password=""):
    """Flash the eMMC storage on the BMC, then after reboot automatically
    log in and complete the mandatory first-login password change.
    Identical to flash_emmc except for the post-reboot password sequence
    appended at the end."""
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

        ser = serial.Serial(serial_device, 115200, timeout=0.1)

        # Setting IP Address (bootloader)
        callback_output("Setting IP Address (bootloader)...")
        command = f'setenv ipaddr {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.20)

        # Grabbing virtual restore image
        callback_output("Grabbing virtual restore image...")
        command = f'wget ${{loadaddr}} {my_ip}:/obmc-rescue-image-snuc-{type}.itb; bootm\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.40)

        # Setting IP Address (BMC)
        callback_output("Setting IP Address (BMC)...")
        await asyncio.sleep(20)
        command = f'ifconfig eth0 up {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.50)

        # Grabbing restore image
        callback_output("Grabbing restore image to your system...")
        command = f"curl -o obmc-phosphor-image-snuc-{type}.wic.xz {my_ip}/obmc-phosphor-image-snuc-{type}.wic.xz\n"
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.60)

        # Grabbing the mapping file
        callback_output("Grabbing the mapping file...")
        command = f'curl -o obmc-phosphor-image-snuc-{type}.wic.bmap {my_ip}/obmc-phosphor-image-snuc-{type}.wic.bmap\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)
        callback_progress(0.90)

        # Flashing the restore image
        callback_output("Flashing the restore image to your system...")
        command = f'bmaptool copy obmc-phosphor-image-snuc-{type}.wic.xz /dev/mmcblk0\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)

        await asyncio.sleep(55)
        callback_output("Factory Reset Complete. Rebooting BMC...")
        ser.write(b'reboot\n')
        callback_progress(1.00)

        # Wait for the BMC to boot into the new firmware
        callback_output("Waiting 75 seconds for system to boot...")
        await asyncio.sleep(75)

        # Post-reboot login + mandatory password change sequence.
        # Uses the same serial connection (no reopen) to avoid DTR
        # toggling which would kill the getty session mid-login.
        callback_output("Sending username...")
        ser.write((bmc_user + "\n").encode())
        await asyncio.sleep(1.5)

        callback_output("Sending password...")
        ser.write((default_password + "\n").encode())

        await asyncio.sleep(2.5)
        callback_output("Re-entering password...")
        ser.write((default_password + "\n").encode())

        await asyncio.sleep(2.5)
        callback_output("Entering new (auto-set) password...")
        ser.write((new_password + "\n").encode())

        await asyncio.sleep(2.5)
        callback_output("Confirming new (auto-set) password...")
        ser.write((new_password + "\n").encode())

        callback_output("Login and password setup sequence completed.")

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


async def flash_emmc2(bmc_ip, directory, my_ip, dd_value, callback_progress, callback_output, serial_device):
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

        ser = serial.Serial(serial_device, 115200, timeout=0.1)

        # Setting IP Address (bootloader)
        callback_output("Setting IP Address (bootloader)...")
        command = f'setenv ipaddr {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.20)

        # Grabbing virtual restore image
        callback_output("Grabbing virtual restore image...")
        command = f'wget ${{loadaddr}} {my_ip}:/obmc-rescue-image-snuc-{type}.itb; bootm\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.40)

        # Setting IP Address (BMC)
        callback_output("Setting IP Address (BMC)...")
        await asyncio.sleep(20)
        command = f'ifconfig eth0 up {bmc_ip}\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.50)

        # Grabbing restore image
        callback_output("Grabbing restore image to your system...")
        command = f"curl -o obmc-phosphor-image-snuc-{type}.wic.xz {my_ip}/obmc-phosphor-image-snuc-{type}.wic.xz\n"
        response = await asyncio.to_thread(read_serial_data, ser, command, 2)
        callback_progress(0.60)

        # Grabbing the mapping file
        callback_output("Grabbing the mapping file...")
        command = f'curl -o obmc-phosphor-image-snuc-{type}.wic.bmap {my_ip}/obmc-phosphor-image-snuc-{type}.wic.bmap\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)
        callback_progress(0.90)

        # Flashing the restore image
        callback_output("Flashing the restore image to your system...")
        command = f'bmaptool copy obmc-phosphor-image-snuc-{type}.wic.xz /dev/mmcblk0\n'
        response = await asyncio.to_thread(read_serial_data, ser, command, 5)

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


def _expect_serial(ser, patterns, timeout):
    """Read from an open serial connection until one of the substrings in
    `patterns` shows up in the accumulated output, or `timeout` seconds
    elapse. Synchronous (pyserial isn't async-native) - run via
    asyncio.to_thread from the caller. Returns (matched_pattern_or_None,
    accumulated_text)."""
    start = time.time()
    buffer = ""
    while time.time() - start < timeout:
        try:
            if ser.in_waiting:
                chunk = ser.read(ser.in_waiting)
                buffer += chunk.decode("utf-8", errors="ignore")
                for pattern in patterns:
                    if pattern in buffer:
                        return pattern, buffer
        except Exception:
            pass
        time.sleep(0.2)
    return None, buffer


async def post_flash_password_setup(serial_device, default_password, new_password, callback_output, bmc_user="root"):
    """
    Fresh OpenBMC firmware enforces a mandatory password change on first
    login with the default credentials (root/<default_password>) - a
    compliance policy, not optional. This logs into the just-flashed BMC
    over the serial console and walks through that prompt sequence
    automatically:

        <hostname> login: root
        Password: <default_password>
        You are required to change your password immediately (administrator enforced).
        Current password: <default_password>
        New password: <new_password>
        Retype new password: <new_password>

    setting the BMC's password to `new_password` (the password already
    configured in Platypus' own Connection Settings), so the freshly
    flashed BMC is immediately usable afterward without a manual console
    session. Returns True on success, False if anything didn't match what
    was expected (logged via callback_output either way, never raises).
    """
    callback_output("Waiting for the BMC login prompt to set up the new password...")
    ser = None
    try:
        ser = serial.Serial(serial_device, 115200, timeout=0.1)
        ser.reset_input_buffer()
        ser.write(b"\n")  # nudge it in case the prompt already printed before we opened the port

        matched, _ = await asyncio.to_thread(_expect_serial, ser, ["login:"], 120)
        if not matched:
            callback_output("Timed out waiting for the login prompt - skipping automatic password setup.")
            return False

        # The very first login attempt right after a fresh boot sometimes
        # gets rejected once (getty/PAM not fully settled yet) even with
        # the correct default credentials, then succeeds immediately on a
        # second try at the same prompt - so this retries the login
        # handshake itself once before concluding the password is
        # actually wrong.
        matched = None
        buffer = ""
        for attempt in range(2):
            ser.write((bmc_user + "\n").encode())
            login_matched, _ = await asyncio.to_thread(_expect_serial, ser, ["Password:"], 30)
            if not login_matched:
                callback_output("Timed out waiting for the password prompt.")
                return False

            ser.write((default_password + "\n").encode())
            matched, buffer = await asyncio.to_thread(
                _expect_serial, ser, ["Current password:", "Login incorrect", "#", "$"], 30,
            )
            if matched != "Login incorrect":
                break
            if attempt == 0:
                callback_output("First login attempt was rejected - retrying once...")
                await asyncio.to_thread(_expect_serial, ser, ["login:"], 15)

        if matched == "Login incorrect":
            callback_output(
                "Default password was rejected (Login incorrect) - the BMC may already have a "
                "different password set. Skipping automatic password setup."
            )
            return False
        if matched != "Current password:":
            callback_output(
                "BMC did not prompt for a mandatory password change - it may already be configured. "
                "Skipping automatic password setup."
            )
            return False

        callback_output("Default login accepted - setting the new password...")
        ser.write((default_password + "\n").encode())

        matched, _ = await asyncio.to_thread(_expect_serial, ser, ["New password:"], 15)
        if not matched:
            callback_output("Did not see the 'New password' prompt - aborting automatic setup.")
            return False
        ser.write((new_password + "\n").encode())

        matched, _ = await asyncio.to_thread(_expect_serial, ser, ["Retype new password:"], 15)
        if not matched:
            callback_output("Did not see the 'Retype new password' prompt - aborting automatic setup.")
            return False
        ser.write((new_password + "\n").encode())

        matched, buffer = await asyncio.to_thread(
            _expect_serial, ser, ["#", "$", "BAD PASSWORD", "authentication token"], 15,
        )
        if matched in ("BAD PASSWORD", "authentication token"):
            callback_output(f"Password change was rejected by the BMC: {buffer.strip()[-200:]}")
            return False

        callback_output("BMC password successfully updated to match Connection Settings.")
        return True
    except Exception as e:
        callback_output(f"Error during automatic password setup: {e}")
        return False
    finally:
        if ser and ser.is_open:
            try:
                ser.close()
            except Exception:
                pass


async def post_flash_login_and_password_sequence(bmc_user, default_password, new_password, serial_device, callback_output):
    """
    Fixed-delay login + mandatory first-login password-change sequence for
    a freshly-flashed BMC, used specifically in the Flash All + FRU flow
    (as opposed to post_flash_password_setup's prompt-matching approach).
    Sends each step after a fixed pause rather than waiting for a specific
    prompt string, matching the timing style already used elsewhere in
    this flashing sequence (login(), set_ip(), etc.):

        1. Send the username, then the default password (login)
        2. Wait 2.5s, re-send the default password (answers "Current password:")
        3. Wait 2.5s, send the new password (answers "New password:")
        4. Wait 2.5s, send the new password again (answers "Retype new password:")

    Deliberately does NOT reuse the separate utils.login() helper here and
    then open a second connection for the rest of the sequence - reopening
    a serial port typically toggles DTR, which on a lot of BMC serial-
    console setups causes the in-progress login/getty session to hang up
    and reset right as it's happening, landing the next step back at a
    fresh, unauthenticated prompt (surfacing as "password is wrong" even
    though the password was correct). Everything runs over one
    persistent connection instead.
    """
    ser = None
    try:
        ser = serial.Serial(serial_device, 115200, timeout=0.1)

        callback_output("Sending username...")
        ser.write((bmc_user + "\n").encode())
        await asyncio.sleep(1.5)

        callback_output("Sending password...")
        ser.write((default_password + "\n").encode())

        await asyncio.sleep(2.5)
        callback_output("Re-entering password...")
        ser.write((default_password + "\n").encode())

        await asyncio.sleep(2.5)
        callback_output("Entering new (auto-set) password...")
        ser.write((new_password + "\n").encode())
        
        await asyncio.sleep(2.5)
        callback_output("Confirming new (auto-set) password...")
        ser.write((new_password + "\n").encode())

        callback_output("Login and password setup sequence completed.")
    except Exception as e:
        callback_output(f"Error during login/password sequence: {e}")
    finally:
        if ser and ser.is_open:
            try:
                ser.close()
            except Exception:
                pass


async def flash_emmc_fru_checked(bmc_user, connection_password, default_password, bmc_ip, eeprom_file, my_ip, callback_progress, callback_output, serial_device):
    """
    Runs after eMMC + U-Boot flashing and a BMC reboot, when both
    "Flash FRU (EEPROM)" and "Auto Set Password Compliance" are checked in
    the Flash All window:

        1. Wait 75s for the BMC to finish rebooting.
        2. Run the auto-set-password sequence (same direction as the
           standalone "Auto Set Password" button: log in with the current
           Connection Settings password, then reset the BMC's password to
           the configured default password).
        3. Wait 2.5s, then set the BMC's IP address.
        4. Wait 2.5s, then flash the EEPROM (FRU data).
    """
    callback_output("Waiting 75 seconds for system to boot...")
    await asyncio.sleep(75)

    callback_output("Running auto-set password sequence...")
    await post_flash_login_and_password_sequence(
        bmc_user, connection_password, default_password, serial_device, callback_output,
    )

    await asyncio.sleep(2.5)
    callback_output("Setting BMC IP...")
    await set_ip(
        bmc_ip, lambda p: None, callback_output, serial_device, bmc_user, default_password,
    )

    await asyncio.sleep(2.5)
    callback_output("Flashing EEPROM...")
    await flash_eeprom(
        eeprom_file, my_ip, callback_progress, callback_output, serial_device, bmc_user, default_password,
    )


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
            
            # Wait a moment for the response to settle
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
            
            # Wait a moment for the response to settle
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

        ser.close()
        callback_output("Reset to U-Boot completed.")
    except serial.SerialException as e:
        callback_output(f"Serial error: {e}")
    except Exception as e:
        callback_output(f"Error during reset: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()