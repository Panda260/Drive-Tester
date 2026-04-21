import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Create History table
    c.execute('''
        CREATE TABLE IF NOT EXISTS test_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disk_name TEXT,
            test_type TEXT,
            result TEXT,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Create SMART History table
    c.execute('''
        CREATE TABLE IF NOT EXISTS smart_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disk_name TEXT,
            smart_data TEXT,
            health_status TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def log_test(disk_name, test_type, result, details):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO test_history (disk_name, test_type, result, details) VALUES (?, ?, ?, ?)',
              (disk_name, test_type, result, details))
    conn.commit()
    conn.close()

def log_smart(disk_name, smart_data_json, health_status):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO smart_history (disk_name, smart_data, health_status) VALUES (?, ?, ?)',
              (disk_name, smart_data_json, health_status))
    conn.commit()
    conn.close()

def get_history(disk_name=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if disk_name:
        c.execute('SELECT * FROM test_history WHERE disk_name = ? ORDER BY timestamp DESC LIMIT 50', (disk_name,))
    else:
        c.execute('SELECT * FROM test_history ORDER BY timestamp DESC LIMIT 50')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]
