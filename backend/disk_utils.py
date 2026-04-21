import subprocess
import json
import os

def run_cmd(cmd, timeout=5):
    """Run a shell command and return its output as a string. Includes timeout to prevent hanging."""
    try:
        # Use timeout to prevent frozen processes from hanging the Flask server
        result = subprocess.run(cmd, shell=True, text=True, capture_output=True, check=True, timeout=timeout)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
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
    """Get SMART data for a disk. If busy, returns cached/safe info."""
    # During heavy tests, smartctl can hang. 
    # For now, we still return fetch_with_fallbacks but rely on the run_cmd timeout.
    return fetch_with_fallbacks(disk_name, "-a -j")

def get_temperature(disk_name):
    """Return temperatures from the background cache (instant response)."""
    with temp_cache_lock:
        return temp_cache.get(disk_name, [])

def _fetch_actual_temperature(disk_name):
    """Helper for the background thread to talk to hardware."""
    # Use a shorter timeout specifically for the background poller
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
test_queues = {}     # disk_name -> list of task lists
temp_cache = {}      # disk_name -> [temp1, temp2]
temp_cache_lock = threading.Lock()

import re
import time

# Regex for badblocks: "37584 37585 0.84% done, 7:31:08 elapsed. (0/0/527405 errors)"
PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)% done, ([\d:]+) elapsed\. \((\d+)/(\d+)/(\d+) errors\)")

def bg_test_runner(disk_name):
    """Universal background runner pulling from queues."""
    while disk_name in test_queues and len(test_queues[disk_name]) > 0:
        tasks = test_queues[disk_name].pop(0)
        
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
                
            active_tests[disk_name]["phase"] = f"{phase_name} (Task 1 of Sequence)" if len(tasks) == 1 else f"{phase_name} ({i+1}/{len(tasks)})"
            active_tests[disk_name]["output"].append(f"\n>>> Executing: {phase_name} <<<\n")
            
            start_time = time.time()
            is_badblocks = "badblocks" in cmd
            dev_path = f"/dev/{disk_name}"
            
            # Safety unmount to ensure the device isn't busy
            subprocess.run(f"umount {dev_path}* || true", shell=True)
            
            # Use stdbuf to disable block buffering for C binaries (like badblocks) so output is immediate
            if "badblocks" in cmd:
                cmd = f"stdbuf -o0 -e0 {cmd}"
                
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False, preexec_fn=os.setsid)
            active_tests[disk_name]["process"] = proc
            
            def read_output():
                buffer = ""
                fd = proc.stdout.fileno()
                while True:
                    try:
                        chunk = os.read(fd, 4096)
                    except Exception:
                        break
                    if not chunk:
                        if buffer: yield buffer
                        break
                    text = chunk.decode('utf-8', errors='replace')
                    import re
                    # Split while keeping the delimiters ( \r or \n )
                    parts = re.split(r'([\r\n])', text)
                    for span in parts:
                        if span in ['\r', '\n']:
                            yield buffer + span
                            buffer = ""
                        else:
                            buffer += span

            bad_block_spam_count = 0
            
            from collections import deque
            output_buffer = deque(active_tests[disk_name]["output"], maxlen=100)
            
            for line in read_output():
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
                    elapsed = time.time() - start_time
                    if prog > 0:
                        total_est = (elapsed / prog) * 100
                        rem = total_est - elapsed
                        m, s = divmod(int(rem), 60)
                        h, m = divmod(m, 60)
                        active_tests[disk_name]["eta"] = f"{h:02d}:{m:02d}:{s:02d}"
                        
                    errs = active_tests[disk_name]["errors"]
                    if errs["read"] > 500 or errs["write"] > 500 or errs["compare"] > 500:
                        try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        except: proc.terminate()
                        output_buffer.append("\n[TEST ABORTED: Massive I/O errors detected. Drive is likely locked by Windows/WSL2.]\n")
                        break
                    
                    if is_badblocks: continue 
                
                # Suppress spam from bad block lists to avoid GIL lockups
                if is_badblocks and line.strip().isdigit():
                    bad_block_spam_count += 1
                    if bad_block_spam_count > 500:
                        try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        except: proc.terminate()
                        output_buffer.append("\n[TEST ABORTED: Too many bad blocks! Drive is locked or completely failed.]\n")
                        break
                    continue

                output_buffer.append(line)
                active_tests[disk_name]["output"] = list(output_buffer)

            proc.stdout.close()
            proc.wait()
            
        if active_tests[disk_name]["status"] == "running":
            active_tests[disk_name]["status"] = "finished" if proc.returncode == 0 else "error"
        
        # Keep status as finished/error for a tiny bit if queue is empty, so frontend can see it
        # But honestly the frontend will grab it and if queue is populated, it rolls over.
        time.sleep(2)

    # If we exit the loop, the queue is empty. Clean up if finished so frontend knows we are done.
    # It will remain in active_tests as 'finished'.

def start_fio_test(disk_name, test_type="read", test_mode="random", bs="4k", direct=1):
    """Start diagnostic test (FIO, Suite, or Badblocks) in background."""
    if disk_name in active_tests and active_tests[disk_name]["status"] == "running":
        return {"error": "A test is already running on this disk"}

    dev_path = f"/dev/{disk_name}"
    tasks = []

    if test_type == "badblocks":
        # -f: force run on mounted partitions
        # -w: destructive write-mode, -v: verbose, -s: progress
        # -b 16384: Use 16KB block size to support drives > 16TB (32-bit block limit fix)
        # -c 65536: Test 64K blocks at once for significantly faster performance on large disks
        cmd = f"badblocks -f -wvs -b 16384 -c 65536 {dev_path}"
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

    # Add to queue
    if disk_name not in test_queues:
        test_queues[disk_name] = []
    test_queues[disk_name].append(tasks)

    # If runner is not active, start it
    if disk_name not in active_tests or active_tests[disk_name]["status"] not in ["running"]:
        t = threading.Thread(target=bg_test_runner, args=(disk_name,))
        t.daemon = True
        t.start()
        return {"status": "queued_and_started", "queue_depth": len(test_queues[disk_name])}

    return {"status": "queued", "queue_depth": len(test_queues[disk_name])}

def get_fio_status(disk_name):
    """Retrieve live status and metadata."""
    if disk_name not in active_tests:
        return {"status": "none"}
    
    test = active_tests[disk_name]
    q_len = len(test_queues.get(disk_name, []))
    
    return {
        "status": test["status"],
        "lines": test["output"],
        "progress": test.get("progress", 0),
        "speed": test.get("speed", "0 MB/s"),
        "eta": test.get("eta", "Calculating..."),
        "errors": test.get("errors", {}),
        "phase": test.get("phase", ""),
        "queue_depth": q_len
    }

def stop_fio_test(disk_name):
    """Stop running fio test AND clear queue."""
    if disk_name in test_queues:
        test_queues[disk_name] = []  # Clear pending tasks
        
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
    run_cmd(f"umount {dev_path}* || true", timeout=10)
    
    # Wipe signatures
    run_cmd(f"wipefs -a {dev_path}", timeout=10)
    
    # Create new GPT partition table
    run_cmd(f"parted -s {dev_path} mklabel gpt", timeout=10)
    
    # Create a single partition using the whole disk
    run_cmd(f"parted -s {dev_path} mkpart primary {fs_type} 0% 100%", timeout=10)
    
    part_path = f"{dev_path}1"
    
    if fs_type == "ext4":
        cmd = f"mkfs.ext4 -F {part_path}"
    elif fs_type == "vfat":
        cmd = f"mkfs.vfat -F 32 {part_path}"
    elif fs_type == "ntfs":
        cmd = f"mkfs.ntfs -f {part_path}"
    else:
        cmd = f"mkfs.ext4 -F {part_path}"
        
    output = run_cmd(cmd, timeout=45)
    return output

def temperature_monitor_loop():
    """Background thread to poll temperatures without blocking Flask."""
    while True:
        try:
            disks = get_disks()
            for d in disks:
                name = d.get('name')
                if not name: continue
                
                # Fetch actual hardware temp
                # Note: fetch_with_fallbacks uses run_cmd with 5s timeout
                data = fetch_with_fallbacks(name, "-A -j")
                
                temps = []
                if 'temperature' in data:
                    t = data['temperature']
                    if 'current' in t: temps.append(t['current'])
                    if 'sensors' in t:
                        for s in t['sensors']:
                            if s.get('value'): temps.append(s['value'])
                
                if not temps and 'ata_smart_attributes' in data:
                    for attr in data['ata_smart_attributes'].get('table', []):
                        if attr['id'] in [194, 190]:
                            temps.append(attr['raw']['value'])
                
                new_temps = sorted(list(set(temps)))
                with temp_cache_lock:
                    temp_cache[name] = new_temps
                    
        except Exception as e:
            print(f"Temp monitor error: {e}")
            
        time.sleep(10)

# Start the background temp monitor
t_mon = threading.Thread(target=temperature_monitor_loop, daemon=True)
t_mon.start()
