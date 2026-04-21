let currentDisks = [];
let selectedDisk = null;
let tempPollInterval = null;

// Critical SMART attributes to highlight
const CRITICAL_ATTRIBUTES = [
    5,   // Reallocated Sector Count
    196, // Reallocation Event Count
    197, // Current Pending Sector Count
    198  // Uncorrectable Sector Count
];

document.addEventListener('DOMContentLoaded', () => {
    fetchDisks();
    setupEventListeners();
    
    // Add listener for format confirm input
    const confirmInput = document.getElementById('confirm-input');
    const formatBtn = document.getElementById('btn-format-confirm');
    confirmInput.addEventListener('input', (e) => {
        if (selectedDisk && e.target.value === selectedDisk.name) {
            formatBtn.disabled = false;
        } else {
            formatBtn.disabled = true;
        }
    });

    // Add UI change listeners for benchmarking settings
    const testTypeSelect = document.getElementById('fio-test-type');
    if (testTypeSelect) {
        testTypeSelect.addEventListener('change', handleTestTypeChange);
        handleTestTypeChange(); // Initialize UI state
    }
});

async function fetchDisks() {
    try {
        const response = await fetch('/api/disks');
        const data = await response.json();
        currentDisks = data.disks || [];
        renderDiskList();
    } catch (e) {
        console.error('Error fetching disks:', e);
        document.getElementById('drive-list').innerHTML = '<div class="danger-row p-2">Failed to load drives</div>';
    }
}

function renderDiskList() {
    const list = document.getElementById('drive-list');
    list.innerHTML = '';
    
    if (currentDisks.length === 0) {
        list.innerHTML = '<div class="p-2 text-secondary">No disks detected.</div>';
        return;
    }

    currentDisks.forEach(disk => {
        const el = document.createElement('div');
        el.className = `drive-item ${selectedDisk?.name === disk.name ? 'active' : ''}`;
        el.innerHTML = `
            <div class="drive-name">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <span>${disk.name}</span>
                    <span class="sidebar-temp" id="sidebar-temp-${disk.name}">--</span>
                </div>
                <div style="font-size: 0.8rem; color: var(--text-secondary)">(${formatDiskSize(disk.size)})</div>
            </div>
            <div class="drive-desc">${disk.model || 'Unknown Model'}</div>
        `;
        el.onclick = () => selectDisk(disk);
        list.appendChild(el);
    });
}

function selectDisk(disk) {
    selectedDisk = disk;
    renderDiskList(); // Re-render to update active state
    
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    
    document.getElementById('selected-drive-title').textContent = `/dev/${disk.name}`;
    
    // Render details
    document.getElementById('drive-details').innerHTML = `
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; font-size: 0.85rem;">
            <p><strong>Name:</strong> ${disk.name}</p>
            <p><strong>Model:</strong> ${disk.model || disk.vendor || 'N/A'}</p>
            <p><strong>Size:</strong> ${formatDiskSize(disk.size)}</p>
            <p><strong>Type:</strong> ${disk.tran ? disk.tran.toUpperCase() : 'N/A'} ${disk.rota ? 'HDD' : 'SSD'}</p>
            <p><strong>Serial:</strong> ${disk.serial || 'N/A'}</p>
            <p><strong>WWN:</strong> <span style="font-size: 0.75rem;">${disk.wwn || 'N/A'}</span></p>
            <p><strong>Vendor:</strong> ${disk.vendor || 'N/A'}</p>
            <p><strong>Revision:</strong> ${disk.rev || 'N/A'}</p>
            <p><strong>Logical Sector:</strong> ${disk['log-sec'] || 'N/A'} B</p>
            <p><strong>Physical Sector:</strong> ${disk['phy-sec'] || 'N/A'} B</p>
            <p><strong>Mountpoint:</strong> ${disk.mountpoint || 'Not mounted'}</p>
            <p><strong>Hotplug:</strong> ${disk.hotplug ? 'Supported' : 'No'}</p>
        </div>
    `;
    
    // Reset SMART table
    document.getElementById('smart-table-body').innerHTML = '<tr><td colspan="6" class="placeholder-text">Fetch SMART data to view attributes.</td></tr>';
    document.getElementById('smart-status-badge').className = 'badge unknown';
    document.getElementById('smart-status-badge').textContent = 'Unknown';
    document.getElementById('fio-output').textContent = 'Run a test to see output here.';
    
    updateExpectedData();
    startTempPolling();
    checkRunningTest();
}

async function updateExpectedData() {
    const exp = document.getElementById('expected-output');
    exp.innerHTML = 'Loading expected benchmark data...';
    try {
        const response = await fetch(`/api/fio/${selectedDisk.name}/expected`);
        const data = await response.json();
        exp.innerHTML = `<strong>Expected (${data.drive_type}):</strong> Read: ${data.expected.read} | Write: ${data.expected.write} | IOPS: ${data.expected.iops}`;
    } catch (e) {
        exp.innerHTML = '';
    }
}

async function fetchSmartData() {
    if (!selectedDisk) return alert("Select a drive first.");
    
    const tbody = document.getElementById('smart-table-body');
    tbody.innerHTML = '<tr><td colspan="6" class="placeholder-text">Loading...</td></tr>';
    
    try {
        const response = await fetch(`/api/smart/${selectedDisk.name}`);
        const data = await response.json();
        
        // Update badge
        const badge = document.getElementById('smart-status-badge');
        if (data.health === "PASSED") {
            badge.className = "badge success";
            badge.textContent = "PASSED";
        } else if (data.health === "FAILED") {
            badge.className = "badge danger";
            badge.textContent = "FAILED";
        } else {
            badge.className = "badge warning";
            badge.textContent = data.health || "UNKNOWN";
        }
        
        renderSmartTable(data.data);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" class="danger-row">Error fetching SMART data.</td></tr>`;
    }
}

function renderSmartTable(smartData) {
    const tbody = document.getElementById('smart-table-body');
    tbody.innerHTML = '';
    
    if (smartData.error) {
        tbody.innerHTML = `<tr><td colspan="6" class="danger-row">${smartData.error}</td></tr>`;
        return;
    }
    
    let rows = [];

    // 1. Handle ATA/SATA Attributes (Standard HDD/SSD)
    if (smartData.ata_smart_attributes?.table) {
        smartData.ata_smart_attributes.table.forEach(attr => {
            rows.push({
                id: attr.id,
                name: attr.name,
                value: attr.value,
                worst: attr.worst,
                thresh: attr.thresh,
                raw: attr.raw?.string || attr.raw?.value,
                critical: CRITICAL_ATTRIBUTES.includes(attr.id) && attr.raw?.value > 0
            });
        });
    } 
    // 2. Handle NVMe Health Information (Modern M.2 SSDs)
    else if (smartData.nvme_smart_health_information_log) {
        const log = smartData.nvme_smart_health_information_log;
        Object.keys(log).forEach((key, index) => {
            const val = log[key];
            rows.push({
                id: index + 1,
                name: key.replace(/_/g, ' ').toUpperCase(),
                value: typeof val === 'object' ? JSON.stringify(val) : val,
                worst: '-',
                thresh: '-',
                raw: typeof val === 'object' ? '-' : val,
                critical: (key.includes('critical') || key.includes('error')) && val > 0
            });
        });
    }
    
    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="placeholder-text">No detailed SMART attributes found for this drive type.</td></tr>';
        return;
    }
    
    rows.forEach(row => {
        const tr = document.createElement('tr');
        if (row.critical) tr.className = 'danger-row';
        
        tr.innerHTML = `
            <td>${row.id}</td>
            <td>${row.name}</td>
            <td>${row.value}</td>
            <td>${row.worst}</td>
            <td>${row.thresh}</td>
            <td>${row.raw}</td>
        `;
        tbody.appendChild(tr);
    });
}

function openFormatModal() {
    if (!selectedDisk) return alert("Select a drive first.");
    document.getElementById('confirm-drive-name').textContent = selectedDisk.name;
    document.getElementById('confirm-input').value = '';
    document.getElementById('btn-format-confirm').disabled = true;
    document.getElementById('format-output').classList.add('hidden');
    document.getElementById('format-modal').classList.remove('hidden');
}

function closeModal(id) {
    document.getElementById(id).classList.add('hidden');
}

async function executeFormat() {
    if (!selectedDisk) return;
    
    const fsType = document.getElementById('fs-type').value;
    const btn = document.getElementById('btn-format-confirm');
    const out = document.getElementById('format-output');
    
    btn.disabled = true;
    btn.textContent = "Formatting...";
    out.classList.remove('hidden');
    out.textContent = "Starting format...";
    
    try {
        const response = await fetch(`/api/format/${selectedDisk.name}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({fs_type: fsType})
        });
        const data = await response.json();
        out.textContent = data.output || "Format complete.";
    } catch (e) {
        out.textContent = `Error: ${e.message}`;
    }
    
    btn.textContent = "FORMAT NOW";
    // We keep disabled to prevent accidental re-clicks until they close modal
}

let pollInterval = null;

function handleTestTypeChange() {
    const type = document.getElementById('fio-test-type').value;
    const modeGroup = document.getElementById('group-test-mode');
    const sizeGroup = document.getElementById('group-block-size');
    const directGroup = document.getElementById('group-direct-io');
    const runBtn = document.getElementById('btn-run-fio');
    const expectedBox = document.getElementById('expected-output');

    if (type === 'badblocks') {
        modeGroup.classList.add('hidden');
        sizeGroup.classList.add('hidden');
        directGroup.classList.add('hidden');
        runBtn.textContent = 'Start Surface Scan';
        expectedBox.innerHTML = '<span class="info-text"><b>Badblocks 100% Surface Scan:</b> This test physically verifies every single sector on the drive. It is the most thorough way to detect hardware defects and failing storage.</span>';
    } else if (type === 'suite') {
        modeGroup.classList.add('hidden');
        sizeGroup.classList.add('hidden');
        directGroup.classList.add('hidden');
        runBtn.textContent = 'Run Pro Suite';
        expectedBox.innerHTML = '<span class="info-text">The Pro Suite automates Read, Write, and Mixed benchmarks to determine realistic max performance.</span>';
    } else {
        modeGroup.classList.remove('hidden');
        sizeGroup.classList.remove('hidden');
        directGroup.classList.remove('hidden');
        runBtn.textContent = 'Run Benchmark';
        if (selectedDisk) updateExpectedData();
    }
}

async function runFioTest() {
    if (!selectedDisk) return alert("Select a drive first.");
    
    const testType = document.getElementById('fio-test-type').value;
    const testMode = document.getElementById('fio-test-mode').value;
    const bsSize = document.getElementById('fio-block-size').value;
    const isDirect = document.getElementById('fio-direct').checked ? 1 : 0;
    
    const out = document.getElementById('fio-output');
    const runBtn = document.getElementById('btn-run-fio');
    const abortBtn = document.getElementById('btn-abort-fio');
    const progPanel = document.getElementById('diagnostic-progress-panel');
    
    if (['write', 'rw', 'suite', 'badblocks'].includes(testType)) {
        const warning = testType === 'badblocks' 
            ? "CRITICAL WARNING: Badblocks will perform a DESTRUCTIVE surface scan. All data on the drive will be PERMANENTLY DELETED. This process can take several DAYS. Proceed?"
            : "WARNING: This operation involves destructive writes. All data on the selected drive will be lost. Proceed?";
        if (!confirm(warning)) return;
    }
    
    out.textContent = `Starting ${testType.toUpperCase()}...`;
    runBtn.classList.add('hidden');
    abortBtn.classList.remove('hidden');
    progPanel.classList.remove('hidden'); 
    
    try {
        const response = await fetch(`/api/fio/${selectedDisk.name}/start`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                type: testType,
                mode: testMode,
                bs: bsSize,
                direct: isDirect
            })
        });
        const data = await response.json();
        
        if (data.error) {
            out.textContent = `Error:\n${data.error}`;
            runBtn.classList.remove('hidden');
            abortBtn.classList.add('hidden');
            return;
        }
        
        // Start polling
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(() => pollFioStatus(), 1000);
        
    } catch (e) {
        out.textContent = `Error starting FIO: ${e.message}`;
        runBtn.classList.remove('hidden');
        abortBtn.classList.add('hidden');
    }
}

async function abortFioTest() {
    if (!selectedDisk) return;
    
    const out = document.getElementById('fio-output');
    try {
        await fetch(`/api/fio/${selectedDisk.name}/stop`, { method: 'POST' });
        out.textContent += "\n[ABORTED BY USER]";
    } catch (e) {
        out.textContent += `\n[Failed to abort: ${e.message}]`;
    }
    
    document.getElementById('btn-run-fio').classList.remove('hidden');
    document.getElementById('btn-abort-fio').classList.add('hidden');
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

async function pollFioStatus() {
    if (!selectedDisk) return;
    
    try {
        const res = await fetch(`/api/fio/${selectedDisk.name}/status`);
        const data = await res.json();
        const out = document.getElementById('fio-output');
        const diagPanel = document.getElementById('diagnostic-progress-panel');
        
        if (data.lines && data.lines.length > 0) {
            out.textContent = data.lines.join('');
            out.scrollTop = out.scrollHeight; // Auto-scroll
        }
        
        if (data.status === 'running') {
            // Re-hide run button and show abort if we just reconnected
            document.getElementById('btn-run-fio').classList.add('hidden');
            document.getElementById('btn-abort-fio').classList.remove('hidden');
            
            // Update Pro Diagnostic Dashboard
            if (data.phase) {
                diagPanel.classList.remove('hidden');
                document.getElementById('diag-phase').textContent = `Phase: ${data.phase}`;
                document.getElementById('diag-progress-bar').style.width = `${data.progress}%`;
                document.getElementById('diag-percent').textContent = `${data.progress.toFixed(2)}%`;
                document.getElementById('diag-eta').textContent = `ETA: ${data.eta}`;
                document.getElementById('diag-speed').textContent = data.speed;
                
                const e = data.errors || {read: 0, write: 0, compare: 0};
                document.getElementById('diag-errors').textContent = `${e.read} / ${e.write} / ${e.compare}`;
            }
            
            // Update queue depth
            const queueDepth = data.queue_depth || 0;
            const qEl = document.getElementById('diag-queue');
            if (qEl) {
                qEl.textContent = queueDepth > 0 ? `${queueDepth} pending` : 'Empty';
            }
        } else if (data.status === 'finished' || data.status === 'error' || data.status === 'aborted') {
            clearInterval(pollInterval);
            pollInterval = null;
            document.getElementById('btn-run-fio').classList.remove('hidden');
            document.getElementById('btn-abort-fio').classList.add('hidden');
            
            if (data.status === 'finished') {
                out.textContent += "\n[TEST COMPLETED]";
                alert("Diagnostic completed successfully!");
            } else if (data.status === 'error') {
                out.textContent += "\n[TEST ERRORED]";
            }
        }
    } catch (e) {
        console.error("Poll error", e);
    }
}

async function checkRunningTest() {
    if (!selectedDisk) return;
    try {
        const res = await fetch(`/api/fio/${selectedDisk.name}/status`);
        const data = await res.json();
        
        // If there's an active running test or pending in queue
        if (data.status === 'running' || data.queue_depth > 0) {
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(() => pollFioStatus(), 1000);
            
            // Do an immediate update
            pollFioStatus();
        } else {
            // Reset UI cleanly
            document.getElementById('btn-run-fio').classList.remove('hidden');
            document.getElementById('btn-abort-fio').classList.add('hidden');
            document.getElementById('diagnostic-progress-panel').classList.add('hidden');
        }
    } catch (e) {
        console.error("Error checking test status", e);
    }
}

async function loadHistory() {
    const tbody = document.getElementById('history-table-body');
    tbody.innerHTML = '<tr><td colspan="4">Loading...</td></tr>';
    document.getElementById('history-modal').classList.remove('hidden');
    
    try {
        // Load global history or for particular disk? The API supports both if `?disk=` is passed.
        const response = await fetch(`/api/history${selectedDisk ? '?disk=' + selectedDisk.name : ''}`);
        const data = await response.json();
        const history = data.history || [];
        
        tbody.innerHTML = '';
        if (history.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="placeholder-text">No history found.</td></tr>';
            return;
        }
        
        history.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${row.timestamp}</td>
                <td>${row.disk_name}</td>
                <td>${row.test_type}</td>
                <td>${row.result}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" class="danger-row">Failed to load history</td></tr>`;
    }
}

function setupEventListeners() {
    const eventSource = new EventSource('/api/events');
    
    eventSource.onmessage = (event) => {
        if (event.data === 'reload') {
            handleDiskReload();
        }
    };
    
    eventSource.onerror = (err) => {
        console.error('EventSource connection lost. Retrying in 5s...');
        eventSource.close();
        setTimeout(setupEventListeners, 5000);
    };
}

async function handleDiskReload() {
    const oldSelectedName = selectedDisk ? selectedDisk.name : null;
    await fetchDisks();
    
    if (oldSelectedName) {
        const stillExists = currentDisks.find(d => d.name === oldSelectedName);
        if (!stillExists) {
            selectedDisk = null;
            // Clear the view
            document.getElementById('selected-drive-title').textContent = 'Select a Drive';
            document.getElementById('temp-display').innerHTML = '';
            document.getElementById('drive-details').innerHTML = '<div class="placeholder-text">Select a drive from the list to view details and run benchmarks.</div>';
            document.getElementById('smart-table-body').innerHTML = '<tr><td colspan="6" class="placeholder-text">Select a drive first.</td></tr>';
            document.getElementById('fio-output').textContent = 'No drive selected.';
            document.getElementById('expected-output').innerHTML = '';
            renderDiskList();
            if (tempPollInterval) clearInterval(tempPollInterval);
        }
    }
}

function startTempPolling() {
    if (tempPollInterval) clearInterval(tempPollInterval);
    if (!selectedDisk) return;
    
    updateTempUI();
    tempPollInterval = setInterval(updateTempUI, 2000);
}

async function updateTempUI() {
    if (!selectedDisk) {
        if (tempPollInterval) clearInterval(tempPollInterval);
        document.getElementById('temp-display').innerHTML = '<div class="spinner loader-small hidden"></div>';
        return;
    }
    
    // Safety check: local variable to ensure we don't update if disk changed mid-fetch
    const diskToFetch = selectedDisk.name;
    const display = document.getElementById('temp-display');
    
    // Show loader
    const loader = display.querySelector('.spinner');
    if (loader) loader.classList.remove('hidden');
    
    try {
        const res = await fetch(`/api/temp/${diskToFetch}`);
        const data = await res.json();
        
        // Guard: check if the user switched disks while we were waiting
        if (!selectedDisk || selectedDisk.name !== diskToFetch) return;
        
        if (data.temps && data.temps.length > 0) {
            const tempStr = data.temps.map(t => `${t}°C`).join(' | ');
            display.innerHTML = `<span class="temp-badge">${tempStr}</span><div class="spinner loader-small hidden" style="margin-left: 0.5rem;"></div>`;
            if (data.temps.some(t => t > 50)) display.classList.add('hot');
            else display.classList.remove('hot');
        } else {
            display.innerHTML = '<span class="placeholder-text" style="font-size:0.7rem">No Temp</span><div class="spinner loader-small hidden" style="margin-left: 0.5rem;"></div>';
        }
    } catch (e) {
        console.error("Temp poll error", e);
    }
}

// Global temperature polling for sidebar
setInterval(async () => {
    try {
        const res = await fetch('/api/temps');
        const data = await res.json();
        Object.keys(data).forEach(diskName => {
            const el = document.getElementById(`sidebar-temp-${diskName}`);
            if (el) {
                const temps = data[diskName];
                if (temps && temps.length > 0) {
                    el.textContent = `${temps[0]}°C`;
                    el.style.display = 'inline-block';
                } else {
                    el.style.display = 'none';
                }
            }
        });
    } catch (e) {
        console.error("Global temp poll error", e);
    }
}, 5000); // 5 seconds is enough for sidebar updates

function formatDiskSize(bytes) {
    if (!bytes || isNaN(bytes)) return 'Unknown';
    if (bytes === 0) return '0 B';
    const k = 1000;
    const sizes = ['B', 'kB', 'MB', 'GB', 'TB', 'PB', 'EB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    // Support showing 1 decimal place, e.g. 2.0 TB
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}
