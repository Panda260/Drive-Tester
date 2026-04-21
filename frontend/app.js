let currentDisks = [];
let selectedDisk = null;

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
            <div class="drive-name">${disk.name} <span style="font-size: 0.8rem; color: var(--text-secondary)">(${formatDiskSize(disk.size)})</span></div>
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
            <p><strong>Model:</strong> ${disk.model || 'N/A'}</p>
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
    
    const attrs = smartData.ata_smart_attributes?.table || [];
    
    if (attrs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="placeholder-text">No SMART attributes available for this drive.</td></tr>';
        return;
    }
    
    attrs.forEach(attr => {
        const tr = document.createElement('tr');
        
        // Highlight logic
        if (CRITICAL_ATTRIBUTES.includes(attr.id) && attr.raw?.value > 0) {
            tr.className = 'danger-row';
        }
        
        tr.innerHTML = `
            <td>${attr.id}</td>
            <td>${attr.name}</td>
            <td>${attr.value}</td>
            <td>${attr.worst}</td>
            <td>${attr.thresh}</td>
            <td>${attr.raw?.string || attr.raw?.value}</td>
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

async function runFioTest() {
    if (!selectedDisk) return alert("Select a drive first.");
    
    const testType = document.getElementById('fio-test-type').value;
    const out = document.getElementById('fio-output');
    const runBtn = document.getElementById('btn-run-fio');
    const abortBtn = document.getElementById('btn-abort-fio');
    
    if (testType === 'write' || testType === 'rw') {
        const confirmWrite = confirm("WARNING: Running a write test is destructive and will overwrite data on this partition/disk. Are you absolutely sure?");
        if (!confirmWrite) return;
    }
    
    out.textContent = "Starting FIO target test...";
    runBtn.classList.add('hidden');
    abortBtn.classList.remove('hidden');
    
    try {
        const response = await fetch(`/api/fio/${selectedDisk.name}/start`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({type: testType})
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
        
        if (data.lines && data.lines.length > 0) {
            out.textContent = data.lines.join('');
            out.scrollTop = out.scrollHeight; // Auto-scroll
        }
        
        if (data.status === 'finished' || data.status === 'error' || data.status === 'aborted') {
            clearInterval(pollInterval);
            pollInterval = null;
            document.getElementById('btn-run-fio').classList.remove('hidden');
            document.getElementById('btn-abort-fio').classList.add('hidden');
            
            if (data.status === 'finished') {
                out.textContent += "\n[TEST COMPLETED]";
            } else if (data.status === 'error') {
                out.textContent += "\n[TEST ERRORED]";
            }
        }
    } catch (e) {
        console.error("Poll error", e);
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
            document.getElementById('drive-details').innerHTML = '<div class="placeholder-text">Select a drive from the list to view details and run benchmarks.</div>';
            document.getElementById('smart-table-body').innerHTML = '<tr><td colspan="6" class="placeholder-text">Select a drive first.</td></tr>';
            document.getElementById('fio-output').textContent = 'No drive selected.';
            document.getElementById('expected-output').innerHTML = '';
            renderDiskList();
        }
    }
}

function formatDiskSize(bytes) {
    if (!bytes || isNaN(bytes)) return 'Unknown';
    if (bytes === 0) return '0 B';
    const k = 1000;
    const sizes = ['B', 'kB', 'MB', 'GB', 'TB', 'PB', 'EB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    // Support showing 1 decimal place, e.g. 2.0 TB
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}
