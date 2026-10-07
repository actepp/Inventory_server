import sys
import os
import socket
import threading
import sqlite3
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import pystray
from pystray._win32 import Icon as Win32Icon
import win32con
from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import ttk, messagebox

# Custom Icon class with double-click support for Windows
class DoubleClickIcon(Win32Icon):
    def __init__(self, *args, on_double_click=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_double_click = on_double_click
        self._last_click_time = 0

    def _on_notify(self, wparam, lparam):
        current_time = time.time()
        
        if lparam == win32con.WM_LBUTTONUP:
            # Check for double-click
            if current_time - self._last_click_time < 0.5:
                if self._on_double_click:
                    self._on_double_click(self)
                self._last_click_time = 0  # Reset to avoid triple-click detection
            else:
                self._last_click_time = current_time
                # Show menu on single click (default behavior)
                super()._on_notify(wparam, lparam)
        elif lparam == win32con.WM_RBUTTONUP:
            # Right click shows menu
            super()._on_notify(wparam, lparam)
        else:
            super()._on_notify(wparam, lparam)

def get_base_dir():
    """Get the directory where the executable/script is located."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent

CONFIG_FILE = get_base_dir() / "server_config.json"
DB_FILE = get_base_dir() / "inventory.db"
DEFAULT_PORT = 5001
DEFAULT_API_PORT = 5002
SERVER_VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS computers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hostname TEXT NOT NULL,
                    ip_address TEXT NOT NULL,
                    mac_address TEXT,
                    os_name TEXT,
                    os_version TEXT,
                    cpu_info TEXT,
                    ram_total_gb REAL,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_heartbeat TIMESTAMP,
                    poll_requested INTEGER DEFAULT 0,
                    client_version TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    computer_id INTEGER NOT NULL,
                    device_class TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    device_id TEXT,
                    manufacturer TEXT,
                    driver_version TEXT,
                    status TEXT,
                    FOREIGN KEY (computer_id) REFERENCES computers (id) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_computers_ip ON computers(ip_address)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_devices_computer ON devices(computer_id)
            ''')
            # Add last_heartbeat column if not exists (for existing databases)
            try:
                cursor.execute('ALTER TABLE computers ADD COLUMN last_heartbeat TIMESTAMP')
            except sqlite3.OperationalError:
                pass
            # Add poll_requested column if not exists
            try:
                cursor.execute('ALTER TABLE computers ADD COLUMN poll_requested INTEGER DEFAULT 0')
            except sqlite3.OperationalError:
                pass
            # Add client_version column if not exists
            try:
                cursor.execute('ALTER TABLE computers ADD COLUMN client_version TEXT')
            except sqlite3.OperationalError:
                pass
            conn.commit()
        logger.info("Database initialized")

    def upsert_computer(self, data: dict) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id FROM computers WHERE ip_address = ?
            ''', (data['ip_address'],))
            row = cursor.fetchone()
            now = datetime.now().isoformat()
            if row:
                computer_id = row[0]
                cursor.execute('''
                    UPDATE computers SET hostname=?, mac_address=?, os_name=?, os_version=?,
                    cpu_info=?, ram_total_gb=?, last_seen=?, client_version=? WHERE id=?
                ''', (data.get('hostname'), data.get('mac_address'), data.get('os_name'),
                      data.get('os_version'), data.get('cpu_info'), data.get('ram_total_gb'),
                      now, data.get('client_version'), computer_id))
            else:
                cursor.execute('''
                    INSERT INTO computers (hostname, ip_address, mac_address, os_name, os_version,
                    cpu_info, ram_total_gb, first_seen, last_seen, client_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (data.get('hostname'), data.get('ip_address'), data.get('mac_address'),
                      data.get('os_name'), data.get('os_version'), data.get('cpu_info'),
                      data.get('ram_total_gb'), now, now, data.get('client_version')))
                computer_id = cursor.lastrowid
            conn.commit()
            return computer_id

    def save_devices(self, computer_id: int, devices: list):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM devices WHERE computer_id = ?', (computer_id,))
            for dev in devices:
                cursor.execute('''
                    INSERT INTO devices (computer_id, device_class, device_name, device_id,
                    manufacturer, driver_version, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (computer_id, dev.get('class'), dev.get('name'), dev.get('device_id'),
                      dev.get('manufacturer'), dev.get('driver_version'), dev.get('status')))
            conn.commit()

    def get_all_computers(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, hostname, ip_address, mac_address, os_name, os_version,
                cpu_info, ram_total_gb, last_seen FROM computers ORDER BY hostname
            ''')
            return cursor.fetchall()

    def get_devices_for_computer(self, computer_id: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT device_class, device_name, device_id, manufacturer, driver_version, status
                FROM devices WHERE computer_id = ? ORDER BY device_class, device_name
            ''', (computer_id,))
            return cursor.fetchall()

    def update_heartbeat(self, ip_address: str):
        """Update last_heartbeat for a computer by IP"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE computers SET last_heartbeat = ? WHERE ip_address = ?
            ''', (datetime.now().isoformat(), ip_address))
            conn.commit()

    def get_online_computers(self, timeout_seconds: int = 30):
        """Get computers that sent heartbeat within timeout"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, hostname, ip_address, mac_address, os_name, os_version,
                cpu_info, ram_total_gb, last_seen, last_heartbeat, client_version
                FROM computers 
                WHERE last_heartbeat IS NOT NULL 
                AND datetime(last_heartbeat) > datetime('now', ?)
                ORDER BY hostname
            ''', (f'-{timeout_seconds} seconds',))
            return cursor.fetchall()

    def get_computer_status(self):
        """Get all computers with their online status"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, hostname, ip_address, mac_address, os_name, os_version,
                cpu_info, ram_total_gb, last_seen, last_heartbeat, poll_requested, client_version
                FROM computers ORDER BY hostname
            ''')
            return cursor.fetchall()

    def get_poll_requested(self, ip_address: str) -> bool:
        """Check if poll was requested for this IP"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT poll_requested FROM computers WHERE ip_address = ?', (ip_address,))
            row = cursor.fetchone()
            return row and row[0] == 1

    def clear_poll_requested(self, ip_address: str):
        """Clear poll request flag"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE computers SET poll_requested = 0 WHERE ip_address = ?', (ip_address,))
            conn.commit()

    def set_poll_requested(self, computer_id: int):
        """Set poll request flag for a computer"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE computers SET poll_requested = 1 WHERE id = ?', (computer_id,))
            conn.commit()

    def set_poll_requested_all(self):
        """Set poll request flag for all computers"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE computers SET poll_requested = 1')
            conn.commit()


class APIRequestHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, db_manager=None, **kwargs):
        self.db_manager = db_manager
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == '/api/computers':
            self._handle_get_computers()
        elif path == '/api/online':
            self._handle_get_online()
        elif path.startswith('/api/computers/') and path.endswith('/devices'):
            computer_id = path.split('/')[3]
            self._handle_get_devices(computer_id)
        else:
            self._send_json({'error': 'Not found'}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path.startswith('/api/computers/'):
            computer_id = path.split('/')[3]
            self._handle_delete_computer(computer_id)
        else:
            self._send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path.startswith('/api/computers/') and path.endswith('/poll'):
            computer_id = path.split('/')[3]
            self._handle_poll_computer(computer_id)
        elif path == '/api/computers/poll-all':
            self._handle_poll_all()
        else:
            self._send_json({'error': 'Not found'}, 404)

    def _handle_poll_computer(self, computer_id):
        try:
            self.db_manager.set_poll_requested(int(computer_id))
            self._send_json({'success': True, 'message': 'Poll requested'})
        except Exception as e:
            logger.error(f"API error requesting poll: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_poll_all(self):
        try:
            self.db_manager.set_poll_requested_all()
            self._send_json({'success': True, 'message': 'Poll requested for all computers'})
        except Exception as e:
            logger.error(f"API error requesting poll all: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_get_computers(self):
        try:
            computers = self.db_manager.get_computer_status()
            result = []
            for c in computers:
                # Determine online status (heartbeat within 30 seconds)
                is_online = False
                if c[9]:  # last_heartbeat
                    try:
                        hb = datetime.fromisoformat(c[9].replace('Z', '+00:00'))
                        if (datetime.now() - hb).total_seconds() < 30:
                            is_online = True
                    except:
                        pass
                
                result.append({
                    'id': c[0],
                    'hostname': c[1],
                    'ip_address': c[2],
                    'mac_address': c[3],
                    'os_name': c[4],
                    'os_version': c[5],
                    'cpu_info': c[6],
                    'ram_total_gb': c[7],
                    'last_seen': c[8],
                    'last_heartbeat': c[9],
                    'is_online': is_online,
                    'client_version': c[11]
                })
            self._send_json(result)
        except Exception as e:
            logger.error(f"API error getting computers: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_get_online(self):
        try:
            computers = self.db_manager.get_online_computers(30)
            result = []
            for c in computers:
                result.append({
                    'id': c[0],
                    'hostname': c[1],
                    'ip_address': c[2],
                    'mac_address': c[3],
                    'os_name': c[4],
                    'os_version': c[5],
                    'cpu_info': c[6],
                    'ram_total_gb': c[7],
                    'last_seen': c[8],
                    'last_heartbeat': c[9],
                    'client_version': c[10]
                })
            self._send_json({
                'online_count': len(result),
                'computers': result,
                'server_version': SERVER_VERSION
            })
        except Exception as e:
            logger.error(f"API error getting online: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_get_devices(self, computer_id):
        try:
            devices = self.db_manager.get_devices_for_computer(int(computer_id))
            result = []
            for d in devices:
                result.append({
                    'device_class': d[0],
                    'device_name': d[1],
                    'device_id': d[2],
                    'manufacturer': d[3],
                    'driver_version': d[4],
                    'status': d[5]
                })
            self._send_json(result)
        except Exception as e:
            logger.error(f"API error getting devices: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_delete_computer(self, computer_id):
        try:
            with sqlite3.connect(self.db_manager.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM devices WHERE computer_id = ?', (computer_id,))
                cursor.execute('DELETE FROM computers WHERE id = ?', (computer_id,))
                conn.commit()
            logger.info(f"Deleted computer {computer_id}")
            self._send_json({'success': True})
        except Exception as e:
            logger.error(f"API error deleting computer: {e}")
            self._send_json({'error': str(e)}, 500)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def log_message(self, format, *args):
        logger.info(f"API: {format % args}")


class APIServer:
    def __init__(self, port: int, db_manager: DatabaseManager):
        self.port = port
        self.db_manager = db_manager
        self.server = None
        self.thread = None
        self.running = False

    def start(self):
        if self.running:
            return
        self.running = True
        handler = lambda *args, **kwargs: APIRequestHandler(*args, db_manager=self.db_manager, **kwargs)
        self.server = HTTPServer(('0.0.0.0', self.port), handler)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info(f"API server started on port {self.port}")

    def stop(self):
        self.running = False
        if self.server:
            try:
                self.server.shutdown()
            except:
                pass
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("API server stopped")

    def _run(self):
        self.server.serve_forever()


class ConfigManager:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = self.load()

    def load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Migration: add api_port if missing
                    if 'api_port' not in config:
                        config['api_port'] = DEFAULT_API_PORT
                        self.save(config)
                    return config
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        return {'port': DEFAULT_PORT, 'api_port': DEFAULT_API_PORT}

    def save(self, config: dict):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self.config = config
            logger.info("Config saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    def get_port(self):
        return self.config.get('port', DEFAULT_PORT)

    def set_port(self, port: int):
        self.config['port'] = port
        self.save(self.config)

    def get_api_port(self):
        return self.config.get('api_port', DEFAULT_API_PORT)

    def set_api_port(self, api_port: int):
        self.config['api_port'] = api_port
        self.save(self.config)


class NetworkListener:
    def __init__(self, port: int, db_manager: DatabaseManager):
        self.port = port
        self.db_manager = db_manager
        self.server_socket = None
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info(f"Network listener started on port {self.port}")

    def stop(self):
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("Network listener stopped")

    def _run(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind(('0.0.0.0', self.port))
            self.server_socket.listen(5)
            self.server_socket.settimeout(1.0)
            while self.running:
                try:
                    client_socket, addr = self.server_socket.accept()
                    threading.Thread(target=self._handle_client, args=(client_socket, addr), daemon=True).start()
                except socket.timeout:
                    continue
                except OSError:
                    break
        except Exception as e:
            logger.error(f"Network listener error: {e}")
        finally:
            if self.server_socket:
                self.server_socket.close()

    def _handle_client(self, client_socket: socket.socket, addr):
        try:
            data = b''
            client_socket.settimeout(10.0)
            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                data += chunk
            if data:
                response = self._process_data(data.decode('utf-8'), addr[0])
                try:
                    client_socket.sendall(response.encode('utf-8'))
                except Exception as e:
                    logger.error(f"Failed to send response to {addr[0]}: {e}")
        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            client_socket.close()

    def _process_data(self, json_data: str, ip_address: str):
        try:
            data = json.loads(json_data)
            
            # Handle lightweight command check
            if data.get('command_check'):
                commands = []
                if self.db_manager.get_poll_requested(ip_address):
                    commands.append("POLL_NOW")
                    self.db_manager.clear_poll_requested(ip_address)
                return json.dumps({"status": "OK", "commands": commands})
            
            required_fields = ['hostname', 'devices']
            for field in required_fields:
                if field not in data:
                    logger.warning(f"Missing field '{field}' from {ip_address}")
                    return json.dumps({"status": "ERROR_FORMAT"})
            
            computer_data = {
                'hostname': data.get('hostname', 'Unknown'),
                'ip_address': ip_address,
                'mac_address': data.get('mac_address'),
                'os_name': data.get('os_name'),
                'os_version': data.get('os_version'),
                'cpu_info': data.get('cpu_info'),
                'ram_total_gb': data.get('ram_total_gb'),
                'client_version': data.get('client_version'),
            }
            computer_id = self.db_manager.upsert_computer(computer_data)
            # Update heartbeat
            self.db_manager.update_heartbeat(ip_address)
            devices = data.get('devices', [])
            if devices:
                self.db_manager.save_devices(computer_id, devices)
            logger.info(f"Received inventory from {computer_data['hostname']} ({ip_address})")
            
            # Check if poll was requested
            commands = []
            if self.db_manager.get_poll_requested(ip_address):
                commands.append("POLL_NOW")
                self.db_manager.clear_poll_requested(ip_address)
            
            return json.dumps({"status": "OK", "commands": commands})
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from {ip_address}: {e}")
            return json.dumps({"status": "ERROR_FORMAT"})
        except Exception as e:
            logger.error(f"Error processing data from {ip_address}: {e}")
            return json.dumps({"status": "ERROR_FORMAT"})


class SettingsWindow:
    def __init__(self, parent, config_manager: ConfigManager, on_port_change, on_api_port_change):
        self.config_manager = config_manager
        self.on_port_change = on_port_change
        self.on_api_port_change = on_api_port_change
        self.window = tk.Toplevel(parent)
        self.window.title("Настройки сервера инвентаризации")
        self.window.geometry("300x250")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.on_cancel)

        self._create_widgets()
        self._center_window()
        self.window.grab_set()
        self.window.focus_set()
        self.window.wait_window()

    def _create_widgets(self):
        main_frame = ttk.Frame(self.window, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Порт для прослушивания (клиенты):").pack(anchor=tk.W, pady=(0, 5))

        current_port = self.config_manager.get_port()
        if not current_port:
            current_port = DEFAULT_PORT
        self.port_var = tk.StringVar(value=str(current_port))
        port_entry = ttk.Entry(main_frame, textvariable=self.port_var, width=20)
        port_entry.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(main_frame, text="Порт API (обозреватель):").pack(anchor=tk.W, pady=(0, 5))

        current_api_port = self.config_manager.get_api_port()
        if not current_api_port:
            current_api_port = DEFAULT_API_PORT
        self.api_port_var = tk.StringVar(value=str(current_api_port))
        api_port_entry = ttk.Entry(main_frame, textvariable=self.api_port_var, width=20)
        api_port_entry.pack(fill=tk.X, pady=(0, 15))

        # Spacer to push buttons to bottom
        ttk.Frame(main_frame).pack(fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, anchor=tk.E)

        ttk.Button(btn_frame, text="Сохранить", command=self.on_save).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Отмена", command=self.on_cancel).pack(side=tk.RIGHT)

    def _center_window(self):
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() // 2) - (self.window.winfo_width() // 2)
        y = (self.window.winfo_screenheight() // 2) - (self.window.winfo_height() // 2)
        self.window.geometry(f"+{x}+{y}")

    def on_save(self):
        try:
            port = int(self.port_var.get())
            api_port = int(self.api_port_var.get())
            if not (1 <= port <= 65535):
                raise ValueError("Port out of range")
            if not (1 <= api_port <= 65535):
                raise ValueError("API port out of range")
            if port == api_port:
                raise ValueError("Ports must be different")
            self.config_manager.set_port(port)
            self.config_manager.set_api_port(api_port)
            self.on_port_change(port)
            self.on_api_port_change(api_port)
            self.window.destroy()
        except ValueError as e:
            messagebox.showerror("Ошибка", str(e))

    def on_cancel(self):
        self.window.destroy()


def create_tray_icon():
    image = Image.new('RGB', (64, 64), color='white')
    draw = ImageDraw.Draw(image)
    draw.rectangle([16, 16, 48, 48], fill='blue')
    draw.text((20, 22), "INV", fill='white')
    return image


class InventoryServer:
    def __init__(self):
        self.config_manager = ConfigManager(CONFIG_FILE)
        self.db_manager = DatabaseManager(DB_FILE)
        self.listener = NetworkListener(self.config_manager.get_port(), self.db_manager)
        self.api_server = APIServer(self.config_manager.get_api_port(), self.db_manager)
        self.tray_icon = None
        self.root = tk.Tk()
        self.root.withdraw()

    def run(self):
        self.listener.start()
        self.api_server.start()
        self._create_tray_icon()
        self.root.mainloop()

    def _create_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem("Настройки", self._show_settings),
            pystray.MenuItem("Выход", self._exit_app)
        )
        self.tray_icon = DoubleClickIcon(
            "InventoryServer",
            create_tray_icon(),
            "Inventory Server",
            menu,
            on_double_click=lambda icon: self.root.after(0, self._show_settings)
        )
        self.tray_icon.run_detached()

    def _show_settings(self, icon=None, item=None):
        def on_port_change(new_port):
            self.listener.stop()
            self.listener = NetworkListener(new_port, self.db_manager)
            self.listener.start()

        def on_api_port_change(new_api_port):
            self.api_server.stop()
            self.api_server = APIServer(new_api_port, self.db_manager)
            self.api_server.start()

        # Schedule in main thread to avoid threading issues with Tkinter
        self.root.after(0, lambda: SettingsWindow(self.root, self.config_manager, on_port_change, on_api_port_change))

    def _exit_app(self, icon=None, item=None):
        self.listener.stop()
        self.api_server.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.quit)


if __name__ == "__main__":
    server = InventoryServer()
    server.run()