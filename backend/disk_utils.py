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

def fetch_with_fallbacks(disk_name, args):
    """Try smartctl with multiple driver types as fallbacks for USB bridges."""
    types = [None, "sat", "scsi"]
    for t in types:
        drv_arg = f"-d {t} " if t else ""
        cmd = f"smartctl {drv_arg}{args} /dev/{disk_name}"
        try:
            output = run_cmd(cmd)
            data = json.loads(output)
            # Check if we got actual drive data. 
            # Looking for attributes or health log or device model
            if "ata_smart_attributes" in data or "nvme_smart_health_information_log" in data or "scsi_error_counter_log" in data:
                return data
            # Also valid if it at least identifies the model better than a generic Vendor
            if t is not None and data.get("model_name"):
                return data
        except:
            continue
    # If all fallbacks failed, return the result of the first (default) call
    try:
        return json.loads(run_cmd(f"smartctl {args} /dev/{disk_name}"))
    except:
        return {}

def get_smart_data(disk_name):
    """Get SMART data for a disk using smartmontools. Supports USB bridges."""
    return fetch_with_fallbacks(disk_name, "-a -j")

def get_temperature(disk_name):
    """Retrieve only the temperature data for a disk (high speed)."""
    data = fetch_with_fallbacks(disk_name, "-A -j")
    
    temps = []
    if 'temperature' in data:
        t = data['temperature']
        if 'current' in t: temps.append(t['current'])
        if 'sensors' in t:
            for s in t['sensors']:
                if s.get('value'): temps.append(s['value'])
    
    # If not in temperature object, check attributes for ID 194 or 190
    if not temps and 'ata_smart_attributes' in data:
        for attr in data['ata_smart_attributes'].get('table', []):
            if attr['id'] in [194, 190]:
                temps.append(attr['raw']['value'])

    # Deduplicate and return
    return sorted(list(set(temps)))

import threading
import signal

active_tests = {}

import re
import time

# Regex for badblocks: "37584 37585 0.84% done, 7:31:08 elapsed. (0/0/527405 errors)"
PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)% done, ([\d:]+) elapsed\. \((\d+)/(\d+)/(\d+) errors\)")

def bg_test_runner(disk_name, tasks):
    """Universal background runner for single or multi-stage tests (FIO, Badblocks, Suite)."""
    active_tests[disk_name] = {
        "status": "running",
        "output": [],
        "progress": 0,
        "speed": "0 MB/s",
        "eta": "Calculating...",
        "errors": {"read": 0, "write": 0, "compare": 0},
        "phase": "Initializing...",
        "process": None
    }
    
    for i, (phase_name, cmd) in enumerate(tasks):
        if active_tests[disk_name]["status"] != "running":
            break
            
        active_tests[disk_name]["phase"] = phase_name
        active_tests[disk_name]["output"].append(f"\n>>> PHASE {i+1}/{len(tasks)}: {phase_name} <<<\n")
        
        start_time = time.time()
        # Track blocks/progress for speed calculation if it's badblocks
        is_badblocks = "badblocks" in cmd
        
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=os.setsid)
        active_tests[disk_name]["process"] = proc
        
        for line in iter(proc.stdout.readline, ''):
            # Parse progress if applicable
            match = PROGRESS_RE.search(line)
            if match:
                prog = float(match.group(1))
                active_tests[disk_name]["progress"] = prog
                active_tests[disk_name]["errors"] = {
                    "read": int(match.group(3)),
                    "write": int(match.group(4)),
                    "compare": int(match.group(5))
                }
                # Calculate simple ETA/Speed placeholder (can be expanded)
                elapsed = time.time() - start_time
                if prog > 0:
                    total_est = (elapsed / prog) * 100
                    rem = total_est - elapsed
                    m, s = divmod(int(rem), 60)
                    h, m = divmod(m, 60)
                    active_tests[disk_name]["eta"] = f"{h:02d}:{m:02d}:{s:02d}"
                
                # For long scans, don't spam the log with every progress update
                if is_badblocks:
                    continue 

            active_tests[disk_name]["output"].append(line)
            if len(active_tests[disk_name]["output"]) > 100:
                active_tests[disk_name]["output"].pop(0)

        proc.stdout.close()
        proc.wait()
        
    if active_tests[disk_name]["status"] == "running":
        active_tests[disk_name]["status"] = "finished" if proc.returncode == 0 else "error"

def start_fio_test(disk_name, test_type="read", test_mode="random", bs="4k", direct=1):
    """Start diagnostic test (FIO, Suite, or Badblocks) in background."""
    if disk_name in active_tests and active_tests[disk_name]["status"] == "running":
        return {"error": "A test is already running on this disk"}

    dev_path = f"/dev/{disk_name}"
    tasks = []

    if test_type == "badblocks":
        # -w: destructive write-mode, -v: verbose, -s: progress
        cmd = f"badblocks -wvs {dev_path}"
        tasks.append(("Badblocks Surface Scan", cmd))
    elif test_type == "suite":
        # Sequential Suite: Read -> Write -> ReadWrite
        tasks.append(("Seq Read", f"fio --name=suite1 --filename={dev_path} --rw=read --bs=1M --direct=1 --ioengine=libaio --iodepth=8 --numjobs=1 --size=1G --runtime=20 --time_based --group_reporting"))
        tasks.append(("Seq Write", f"fio --name=suite2 --filename={dev_path} --rw=write --bs=1M --direct=1 --ioengine=libaio --iodepth=8 --numjobs=1 --size=1G --runtime=20 --time_based --group_reporting"))
        tasks.append(("Seq Mixed", f"fio --name=suite3 --filename={dev_path} --rw=readwrite --bs=1M --direct=1 --ioengine=libaio --iodepth=8 --numjobs=1 --size=1G --runtime=20 --time_based --group_reporting"))
    else:
        # Standard FIO
        if test_mode == "random":
            rw = {"read": "randread", "write": "randwrite", "rw": "randrw"}.get(test_type, "randread")
        else:
            rw = {"read": "read", "write": "write", "rw": "readwrite"}.get(test_type, "read")
        
        iodepth = 64 if test_mode == "random" else 8
        cmd = (f"fio --name=tester --filename={dev_path} --rw={rw} --bs={bs} --direct={direct} "
               f"--ioengine=libaio --iodepth={iodepth} --numjobs=1 --size=1G --runtime=20 "
               f"--time_based --group_reporting --eta=always")
        tasks.append(("Standard Benchmark", cmd))

    t = threading.Thread(target=bg_test_runner, args=(disk_name, tasks))
    t.daemon = True
    t.start()
    return {"status": "started"}

def get_fio_status(disk_name):
    """Retrieve live status and metadata."""
    if disk_name not in active_tests:
        return {"status": "none"}
    
    test = active_tests[disk_name]
    return {
        "status": test["status"],
        "lines": test["output"],
        "progress": test.get("progress", 0),
        "speed": test.get("speed", "0 MB/s"),
        "eta": test.get("eta", "Calculating..."),
        "errors": test.get("errors", {}),
        "phase": test.get("phase", "")
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
