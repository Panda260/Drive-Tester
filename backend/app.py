from flask import Flask, jsonify, request, send_from_directory, Response
import os
import disk_utils
import database
import pyudev
import threading
from queue import Queue

# Handle live event distribution
subscribers = []
subscribers_lock = threading.Lock()

def udev_monitor_task():
    """Background task to watch for disk insertion/removal."""
    try:
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='block', device_type='disk')
        
        for device in iter(monitor.poll, None):
            if device.action in ('add', 'remove'):
                with subscribers_lock:
                    for q in subscribers:
                        q.put("reload")
    except Exception as e:
        print(f"udev monitor error: {e}")

# Start udev monitor thread
threading.Thread(target=udev_monitor_task, daemon=True).start()

app = Flask(__name__, static_folder='../frontend', static_url_path='')

@app.before_request
def setup():
    database.init_db()

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/disks', methods=['GET'])
def get_disks():
    disks = disk_utils.get_disks()
    return jsonify({"disks": disks})

@app.route('/api/smart/<disk_name>', methods=['GET'])
def get_smart(disk_name):
    # Security check: basic validation of disk_name to prevent injection
    if not disk_name.isalnum():
        return jsonify({"error": "Invalid disk name"}), 400
    
    data = disk_utils.get_smart_data(disk_name)
    
    # Basic health analysis based on smartctl output
    health_status = "UNKNOWN"
    if "smart_status" in data:
        passed = data["smart_status"].get("passed", False)
        health_status = "PASSED" if passed else "FAILED"
    
    database.log_smart(disk_name, str(data), health_status)
    return jsonify({"health": health_status, "data": data})

@app.route('/api/fio/<disk_name>/start', methods=['POST'])
def start_fio(disk_name):
    if not disk_name.isalnum():
        return jsonify({"error": "Invalid disk name"}), 400
    
    req_data = request.get_json() or {}
    test_type = req_data.get("type", "read")
    
    result = disk_utils.start_fio_test(disk_name, test_type)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/fio/<disk_name>/status', methods=['GET'])
def status_fio(disk_name):
    if not disk_name.isalnum():
        return jsonify({"error": "Invalid disk name"}), 400
    res = disk_utils.get_fio_status(disk_name)
    if res.get("status") == "finished":
        # Log result once
        database.log_test(disk_name, "fio_test", "COMPLETED", "Test completed.")
    return jsonify(res)

@app.route('/api/fio/<disk_name>/stop', methods=['POST'])
def stop_fio(disk_name):
    if not disk_name.isalnum():
        return jsonify({"error": "Invalid disk name"}), 400
    
    result = disk_utils.stop_fio_test(disk_name)
    database.log_test(disk_name, "fio_test", "ABORTED", "Test was manually aborted.")
    return jsonify(result)

@app.route('/api/fio/<disk_name>/expected', methods=['GET'])
def expected_fio(disk_name):
    if not disk_name.isalnum():
        return jsonify({"error": "Invalid disk name"}), 400
        
    disks = disk_utils.get_disks()
    # Handle error case from get_disks
    if len(disks) > 0 and 'error' in disks[0]:
        return jsonify({"drive_type": "Unknown", "expected": {"read": "Unknown", "write": "Unknown", "iops": "Unknown"}})
        
    meta = next((d for d in disks if d.get('name') == disk_name), None)
    if not meta:
        return jsonify({"drive_type": "Unknown", "expected": {"read": "Unknown", "write": "Unknown", "iops": "Unknown"}})
    
    # Simple heuristic
    if meta.get('rota'): # HDD
        expected = {"read": "100 - 150 MB/s", "write": "80 - 130 MB/s", "iops": "70 - 120 IOPS"}
    else: # SSD
        if 'nvme' in disk_name.lower():
            expected = {"read": "2000 - 3500+ MB/s", "write": "1500 - 3000+ MB/s", "iops": "100k - 300k+ IOPS"}
        else:
            expected = {"read": "450 - 550 MB/s", "write": "400 - 500 MB/s", "iops": "50k - 90k IOPS"}
            
    return jsonify({"drive_type": "HDD" if meta.get('rota') else "SSD", "expected": expected})

@app.route('/api/format/<disk_name>', methods=['POST'])
def format_drive(disk_name):
    if not disk_name.isalnum():
        return jsonify({"error": "Invalid disk name"}), 400
        
    req_data = request.get_json() or {}
    fs_type = req_data.get("fs_type", "ext4")
    
    output = disk_utils.format_disk(disk_name, fs_type)
    
    database.log_test(
        disk_name,
        f"format_{fs_type}",
        "COMPLETED",
        output
    )
    return jsonify({"output": output})

@app.route('/api/history', methods=['GET'])
def get_history():
    disk_name = request.args.get('disk')
    history = database.get_history(disk_name)
    return jsonify({"history": history})

@app.route('/api/events')
def stream_events():
    """SSE endpoint to stream disk hotplug events."""
    def event_generator():
        q = Queue()
        with subscribers_lock:
            subscribers.append(q)
        try:
            # Send initial ping to confirm connection
            yield "data: connected\n\n"
            while True:
                msg = q.get()
                yield f"data: {msg}\n\n"
        except GeneratorExit:
            with subscribers_lock:
                if q in subscribers:
                    subscribers.remove(q)
        except Exception:
            with subscribers_lock:
                if q in subscribers:
                    subscribers.remove(q)

    return Response(event_generator(), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
