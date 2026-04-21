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
    """Get SMART data for a disk using smartmontools."""
    # smartctl -a -j /dev/{disk_name}
    cmd = f"smartctl -a -j /dev/{disk_name}"
    output = run_cmd(cmd)
    try:
        # Ignore error code because smartctl uses error bits for various non-critical flags
        data = json.loads(output)
        return data
    except Exception as e:
        return {"error": str(e), "raw": output}

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

def start_fio_test(disk_name, test_type="read"):
    """Start fio test in background."""
    if disk_name in active_tests and active_tests[disk_name]["status"] == "running":
        return {"error": "A test is already running on this disk"}

    dev_path = f"/dev/{disk_name}"
    
    if test_type == "read":
        rw_mode = "randread"
    elif test_type == "write":
        rw_mode = "randwrite"
    else:
        rw_mode = "randrw"

    cmd = f"fio --name=tester --filename={dev_path} --rw={rw_mode} --bs=4k --ioengine=libaio --iodepth=64 --numjobs=1 --size=1G --runtime=15 --time_based --group_reporting --eta=always"
    
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
