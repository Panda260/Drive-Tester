import subprocess
import json
import os

def run_cmd(cmd):
    """Run a shell command and return its output as a string."""
    try:
        result = subprocess.run(cmd, shell=True, text=True, capture_output=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # Check if we should ignore the error (e.g., smartctl might return non-zero exit even if successful)
        return e.stdout.strip() + "\n" + e.stderr.strip()

def get_disks():
    """Get all physical disks on the system."""
    # lsblk -J -d -b returns JSON format with size in bytes
    cmd = "lsblk -J -d -b -o NAME,SIZE,MODEL,TYPE,ROTA,MOUNTPOINT,SERIAL,WWN,VENDOR,REV,TRAN,LOG-SEC,PHY-SEC,HOTPLUG"
    try:
        output = run_cmd(cmd)
        data = json.loads(output)
        disks = [blk for blk in data.get('blockdevices', []) if blk.get('type') == 'disk']
        return disks
    except Exception as e:
        return [{"error": str(e), "raw": output if 'output' in locals() else ""}]

def get_smart_data(disk_name):
    """Get SMART data for a disk using smartmontools. Supports USB bridges."""
    # Try default first
    cmd = f"smartctl -a -j /dev/{disk_name}"
    output = run_cmd(cmd)
    try:
        data = json.loads(output)
        # Check if we got useful data. If not (common for USB), try -d sat
        has_attrs = "ata_smart_attributes" in data or "nvme_smart_health_information_log" in data
        if not has_attrs:
            retry_cmd = f"smartctl -d sat -a -j /dev/{disk_name}"
            retry_output = run_cmd(retry_cmd)
            retry_data = json.loads(retry_output)
            if "ata_smart_attributes" in retry_data:
                return retry_data
        return data
    except Exception as e:
        return {"error": str(e), "raw": output}

def get_temperature(disk_name):
    """Retrieve only the temperature data for a disk (high speed)."""
    # Use -A to fetch only attributes/health for faster response
    cmd = f"smartctl -A -j /dev/{disk_name}"
    try:
        output = run_cmd(cmd)
        data = json.loads(output)
        
        # If no temperature field, it might be a USB device needing -d sat
        if 'temperature' not in data:
            retry_cmd = f"smartctl -d sat -A -j /dev/{disk_name}"
            output = run_cmd(retry_cmd)
            data = json.loads(output)
            
        temps = []
        if 'temperature' in data:
            t = data['temperature']
            if 'current' in t: temps.append(t['current'])
            if 'sensors' in t:
                for s in t['sensors']:
                    if s.get('value'): temps.append(s['value'])
        
        # Deduplicate and return
        return sorted(list(set(temps)))
    except:
        return []

import threading
import signal

active_tests = {}

def bg_fio_runner(disk_name, cmd):
    # Create a process group so we can kill fio and its children
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=os.setsid)
    active_tests[disk_name] = {
        "process": proc,
        "output": [],
        "status": "running"
    }
    
    # Read output line by line
    for line in iter(proc.stdout.readline, ''):
        active_tests[disk_name]["output"].append(line)
        if len(active_tests[disk_name]["output"]) > 50:
            active_tests[disk_name]["output"].pop(0)

    proc.stdout.close()
    proc.wait()
    # Don't overwrite if it was manually aborted
    if active_tests[disk_name]["status"] == "running":
        active_tests[disk_name]["status"] = "finished" if proc.returncode == 0 else "error"

def start_fio_test(disk_name, test_type="read", test_mode="random", bs="4k", direct=1):
    """Start fio test in background with advanced parameters."""
    if disk_name in active_tests and active_tests[disk_name]["status"] == "running":
        return {"error": "A test is already running on this disk"}

    dev_path = f"/dev/{disk_name}"
    
    # Determine rw mode based on type and random/seq mode
    if test_mode == "random":
        if test_type == "read": rw = "randread"
        elif test_type == "write": rw = "randwrite"
        else: rw = "randrw"
    else: # sequential
        if test_type == "read": rw = "read"
        elif test_type == "write": rw = "write"
        else: rw = "readwrite"

    # Default iodepth 64 for random, 1 for sequential (typical for HDD)
    iodepth = 64 if test_mode == "random" else 8

    cmd = (
        f"fio --name=tester --filename={dev_path} --rw={rw} --bs={bs} "
        f"--direct={direct} --ioengine=libaio --iodepth={iodepth} --numjobs=1 "
        f"--size=1G --runtime=20 --time_based --group_reporting --eta=always"
    )
    
    t = threading.Thread(target=bg_fio_runner, args=(disk_name, cmd))
    t.daemon = True
    t.start()
    return {"status": "started"}

def get_fio_status(disk_name):
    """Retrieve live status and lines."""
    if disk_name not in active_tests:
        return {"status": "none"}
    
    test = active_tests[disk_name]
    return {
        "status": test["status"],
        "lines": test["output"]
    }

def stop_fio_test(disk_name):
    """Stop running fio test."""
    if disk_name in active_tests and active_tests[disk_name]["status"] == "running":
        proc = active_tests[disk_name]["process"]
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except:
            pass
        active_tests[disk_name]["status"] = "aborted"
        return {"status": "aborted"}
    return {"error": "No running test found"}

def format_disk(disk_name, fs_type="ext4"):
    """Format a disk with the given filesystem. Extremely dangerous."""
    # Unmount first just in case
    dev_path = f"/dev/{disk_name}"
    run_cmd(f"umount {dev_path}* || true")
    
    # Wipe signatures
    run_cmd(f"wipefs -a {dev_path}")
    
    # Create new GPT partition table
    run_cmd(f"parted -s {dev_path} mklabel gpt")
    
    # Create a single partition using the whole disk
    run_cmd(f"parted -s {dev_path} mkpart primary {fs_type} 0% 100%")
    
    part_path = f"{dev_path}1"
    
    if fs_type == "ext4":
        cmd = f"mkfs.ext4 -F {part_path}"
    elif fs_type == "vfat":
        cmd = f"mkfs.vfat -F 32 {part_path}"
    elif fs_type == "ntfs":
        cmd = f"mkfs.ntfs -f {part_path}"
    else:
        cmd = f"mkfs.ext4 -F {part_path}"
        
    output = run_cmd(cmd)
    return output
