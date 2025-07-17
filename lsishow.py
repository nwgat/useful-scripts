import re
import subprocess

class Colors:
    """ANSI color codes for terminal output."""
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

def mask_serial(serial_number):
    """Masks the first half of a serial number with asterisks."""
    if serial_number == "N/A" or len(serial_number) < 2:
        return serial_number
    
    half_length = len(serial_number) // 2
    masked_part = "*" * half_length
    visible_part = serial_number[half_length:]
    
    return masked_part + visible_part

def colorize_temp(temp_str, device_type='drive'):
    """
    Applies color to a temperature string based on its value and device type.
    
    Args:
        temp_str (str): The temperature string (e.g., "55°C").
        device_type (str): The type of device, 'drive' or 'controller'.
                           This determines the color thresholds.
    """
    if "°C" not in temp_str:
        return temp_str
    
    try:
        # Extract the numeric part of the temperature
        temp_val = int(re.search(r'(\d+)', temp_str).group(1))
        
        # Define temperature thresholds for different devices
        thresholds = {
            'drive': {'red': 60, 'yellow': 51},
            'controller': {'red': 101, 'yellow': 51} # Red is applied for temps > 100°C
        }
        
        red_threshold = thresholds[device_type]['red']
        yellow_threshold = thresholds[device_type]['yellow']

        if temp_val >= red_threshold:
            return f"{Colors.RED}{temp_str}{Colors.RESET}"
        elif temp_val >= yellow_threshold:
            return f"{Colors.YELLOW}{temp_str}{Colors.RESET}"
        else: # Green for anything below the yellow threshold
            return f"{Colors.GREEN}{temp_str}{Colors.RESET}"
            
    except (ValueError, AttributeError):
        # If parsing fails, return the original string without color
        return temp_str

def get_controller_details():
    """
    Retrieves the LSI controller name and its PCI Express link details.
    Returns a tuple: (controller_name, bus_interface_details).
    """
    try:
        # Get verbose output from lspci
        lspci_output = subprocess.check_output(["lspci", "-vv"], stderr=subprocess.STDOUT, text=True)
        
        # Split the output into blocks, one for each device.
        device_blocks = re.split(r'\n(?=[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])', lspci_output)

        for block in device_blocks:
            # We are looking for the SAS controller
            if "Serial Attached SCSI controller" in block:
                device_name_line = block.split('\n')[0].strip()
                
                # Find the LnkSta line to get speed and width
                lnk_sta_match = re.search(r'LnkSta:.*?Speed ([\d\.]+)GT/s.*?Width (x\d+)', block, re.DOTALL)
                
                if lnk_sta_match:
                    speed_str = lnk_sta_match.group(1)
                    width = lnk_sta_match.group(2)
                    
                    # Determine PCIe version from speed
                    ver = "Unknown"
                    if speed_str.startswith("16"): ver = "4.0"
                    elif speed_str.startswith("8"): ver = "3.0"
                    elif speed_str.startswith("5"): ver = "2.0"
                    elif speed_str.startswith("2.5"): ver = "1.0"
                    
                    bus_interface = f"PCI Express {ver} {width}"
                    return device_name_line, bus_interface

        # Fallback to simple 'lspci' if the detailed parsing fails
        lspci_output_simple = subprocess.check_output(["lspci"], stderr=subprocess.STDOUT, text=True)
        for line in lspci_output_simple.strip().split('\n'):
            if "Serial Attached SCSI controller" in line:
                return line.strip(), None

        return "LSI controller not found.", None
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return f"Error retrieving controller details: {e}", None
    except Exception as e:
        return f"An unexpected error occurred: {e}", None


def get_drive_temperatures():
    """
    Retrieves drive temperatures using hdsentinel.
    Returns a dictionary mapping serial numbers to temperatures.
    """
    temps = {}
    try:
        hdsentinel_output = subprocess.check_output(["sudo", "hdsentinel"], stderr=subprocess.STDOUT, text=True)
        device_blocks = hdsentinel_output.split("HDD Device")
        for block in device_blocks:
            if not block.strip() or "HDD Model ID" not in block:
                continue
            sn_match = re.search(r"HDD Serial No:\s*(.*)", block)
            temp_match = re.search(r"Temperature\s*:\s*(\d+)\s*°C", block)
            if sn_match and temp_match:
                serial_no = sn_match.group(1).strip()
                temperature = temp_match.group(1).strip()
                temps[serial_no] = temperature
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    except Exception:
        pass
    return temps

def get_drive_details():
    """
    Retrieves drive serial numbers and link speeds using storcli.
    Returns a dictionary mapping Slot IDs to a dict of details.
    """
    details = {}
    try:
        storcli_output = subprocess.check_output(
            ["sudo", "./storcli64", "/c0/sALL", "show", "all"],
            stderr=subprocess.STDOUT,
            text=True
        )
        drive_blocks = re.split(r"Drive /c0/s\d+ Device attributes :", storcli_output)
        slot_ids_found = re.findall(r"Drive /c0/s(\d+) Device attributes :", storcli_output)
        for i, block in enumerate(drive_blocks[1:]):
            slot_id = slot_ids_found[i]
            sn_match = re.search(r"SN\s*=\s*([\w-]+)", block)
            link_speed_match = re.search(r"Link Speed\s*=\s*([\d\.]+\w+/s)", block)
            if sn_match and link_speed_match:
                details[slot_id] = {
                    'sn': sn_match.group(1).strip(),
                    'link_speed': link_speed_match.group(1).strip()
                }
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    except Exception:
        pass
    return details


def get_lsi_summary(hide_serials):
    """
    Retrieves and summarizes LSI controller and drive information.
    
    Args:
        hide_serials (bool): Whether to mask the serial numbers in the output.
    """
    controller_name, bus_interface = get_controller_details()
    controller_serial = "N/A"
    driver_version = "N/A"
    driver_name = ""
    bios_version = "N/A"
    nvdata_version = "N/A"
    fw_version = "N/A"
    controller_temp = "N/A"
    total_ports = "N/A"
    devices = []

    drive_temps = get_drive_temperatures()
    drive_details_by_slot = get_drive_details()

    try:
        storcli_output_show = subprocess.check_output(["sudo", "./storcli64", "/c0", "show"], stderr=subprocess.STDOUT, text=True)
        
        # Extract controller details from the 'show' command
        sn_match = re.search(r"Serial Number\s*=\s*([\w-]+)", storcli_output_show)
        if sn_match:
            controller_serial = sn_match.group(1).strip()
            
        drv_ver_match = re.search(r"Driver Version\s*=\s*([\d\.]+)", storcli_output_show)
        if drv_ver_match:
            driver_version = drv_ver_match.group(1).strip()
            
        drv_name_match = re.search(r"Driver Name\s*=\s*(\w+)", storcli_output_show)
        if drv_name_match:
            driver_name = drv_name_match.group(1).strip()

        bios_match = re.search(r"BIOS Version\s*=\s*(.*)", storcli_output_show)
        if bios_match:
            bios_version = bios_match.group(1).strip()
            
        nvdata_match = re.search(r"NVDATA Version\s*=\s*(.*)", storcli_output_show)
        if nvdata_match:
            nvdata_version = nvdata_match.group(1).strip()
            
        fw_match = re.search(r"FW Version\s*=\s*(.*)", storcli_output_show)
        if fw_match:
            fw_version = fw_match.group(1).strip()

        # Get total number of supported ports
        total_ports_match = re.search(r"Physical Drives\s*=\s*(\d+)", storcli_output_show)
        if total_ports_match:
            total_ports = total_ports_match.group(1)

        # Parse the device list
        table_header_match = re.search(r"^(EID:Slt\s+DID\s+State.*)$", storcli_output_show, re.MULTILINE)
        if table_header_match:
            header = table_header_match.group(1)
            model_start_index = header.find("Model")
            end_col_start_index = -1
            if model_start_index != -1:
                header_after_model = header[model_start_index + 5:]
                next_col_search = re.search(r'\S', header_after_model)
                if next_col_search:
                    end_col_start_index = model_start_index + 5 + next_col_search.start()
            if model_start_index != -1 and end_col_start_index != -1:
                table_content_match = re.search(r"-{10,}\n(.*?)\n-{10,}", storcli_output_show[table_header_match.end():], re.DOTALL)
                if table_content_match:
                    device_lines = table_content_match.group(1).strip().split('\n')
                    for line in device_lines:
                        if not line.strip(): continue
                        parts_before_model = line[:model_start_index].strip().split()
                        model = line[model_start_index:end_col_start_index].strip()
                        
                        # --- FIX for parsing quirks in storcli output ---
                        if model.startswith("T") and "DM00" in model:
                            model = "S" + model
                        model = model.split(',')[0].rstrip(' -')
                        
                        if len(parts_before_model) >= 2:
                            eid_slt = parts_before_model[0]
                            slot_match = re.search(r'(\d+)', eid_slt)
                            slot_id = slot_match.group(1) if slot_match else None
                            if slot_id:
                                device_info = drive_details_by_slot.get(slot_id, {})
                                serial_num = device_info.get('sn', "N/A")
                                link_speed = device_info.get('link_speed', "N/A")
                                temp = drive_temps.get(serial_num, "N/A")
                                temp_str = f"{temp}°C" if temp != "N/A" else "N/A"
                                colored_temp = colorize_temp(temp_str)
                                
                                sn_to_display = mask_serial(serial_num) if hide_serials else serial_num
                                devices.append(f"  - Port {slot_id} Model: {model}, SN: {sn_to_display}, Speed: {link_speed}, Temp: {colored_temp}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        devices.append("  - Could not execute storcli. Is it in your PATH?")
    except Exception as e:
        devices.append(f"  - An unexpected error occurred: {e}")

    try:
        storcli_output_temp = subprocess.check_output(["sudo", "./storcli64", "/c0", "show", "temperature"], stderr=subprocess.STDOUT, text=True)
        temp_match = re.search(r"ROC temperature\(Degree Celsius\)\s*(\d+)", storcli_output_temp)
        if temp_match:
            controller_temp = temp_match.group(1)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # --- Print Summary ---
    controller_sn_to_display = mask_serial(controller_serial) if hide_serials else controller_serial
    print("LSI Controller Summary")
    print("======================")
    print(f"Controller: {controller_name}")
    if bus_interface:
        print(f"Bus Interface:  {bus_interface}")
    print(f"Serial Number:    {controller_sn_to_display}")
    print(f"Driver Version:  {driver_version} ({driver_name})")
    print(f"BIOS Version = {bios_version}")
    print(f"NVDATA Version = {nvdata_version}")
    print(f"FW Version = {fw_version}")
    controller_temp_str = f"{controller_temp}°C" if controller_temp != "N/A" else "N/A"
    print(f"Controller Temp: {colorize_temp(controller_temp_str, 'controller')}")
    print(f"Ports Connected:  {len(devices)}")
    print("\nConnected Devices:")
    if devices:
        # Sort devices by Port number before printing
        devices.sort(key=lambda x: int(re.search(r'Port (\d+)', x).group(1)))
        for device in devices:
            print(device)
    else:
        print("  - No devices found or could not parse device list.")

if __name__ == "__main__":
    # Ask the user if they want to hide serial numbers
    response = input("Hide serial numbers? (y/n) [y]: ").lower().strip()
    # Default to hiding serials if the user just presses Enter or types 'y'
    should_hide_serials = response != 'n'
    
    get_lsi_summary(should_hide_serials)
