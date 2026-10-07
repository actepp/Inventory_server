import sys
import os
import socket
import threading
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import ttk, messagebox

CONFIG_FILE = Path(__file__).parent / "server_config.json"
DB_FILE = Path(__file__).parent / "inventory.db"
DEFAULT_PORT = 5000

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
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                    cpu_info=?, ram_total_gb=?, last_seen=? WHERE id=?
                ''', (data.get('hostname'), data.get('mac_address'), data.get('os_name'),
                      data.get('os_version'), data.get('cpu_info'), data.get('ram_total_gb'),
                      now, computer_id))
            else:
                cursor.execute('''
                    INSERT INTO computers (hostname, ip_address, mac_address, os_name, os_version,
                    cpu_info, ram_total_gb, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (data.get('hostname'), data.get('ip_address'), data.get('mac_address'),
                      data.get('os_name'), data.get('os_version'), data.get('cpu_info'),
                      data.get('ram_total_gb'), now, now))
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


class ConfigManager:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = self.load()

    def load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        return {'port': DEFAULT_PORT}

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
                self._process_data(data.decode('utf-8'), addr[0])
        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            client_socket.close()

    def _process_data(self, json_data: str, ip_address: str):
        try:
            data = json.loads(json_data)
            computer_data = {
                'hostname': data.get('hostname', 'Unknown'),
                'ip_address': ip_address,
                'mac_address': data.get('mac_address'),
                'os_name': data.get('os_name'),
                'os_version': data.get('os_version'),
                'cpu_info': data.get('cpu_info'),
                'ram_total_gb': data.get('ram_total_gb'),
            }
            computer_id = self.db_manager.upsert_computer(computer_data)
            devices = data.get('devices', [])
            if devices:
                self.db_manager.save_devices(computer_id, devices)
            logger.info(f"Received inventory from {computer_data['hostname']} ({ip_address})")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from {ip_address}: {e}")
        except Exception as e:
            logger.error(f"Error processing data from {ip_address}: {e}")


class SettingsWindow:
    def __init__(self, parent, config_manager: ConfigManager, on_port_change):
        self.config_manager = config_manager
        self.on_port_change = on_port_change
        self.window = tk.Toplevel(parent)
        self.window.title("Настройки сервера инвентаризации")
        self.window.geometry("300x180")
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

        ttk.Label(main_frame, text="Порт для прослушивания:").pack(anchor=tk.W, pady=(0, 5))

        current_port = self.config_manager.get_port()
        if not current_port:
            current_port = DEFAULT_PORT
        self.port_var = tk.StringVar(value=str(current_port))
        port_entry = ttk.Entry(main_frame, textvariable=self.port_var, width=20)
        port_entry.pack(fill=tk.X, pady=(0, 15))

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
            if not (1 <= port <= 65535):
                raise ValueError("Port out of range")
            self.config_manager.set_port(port)
            self.on_port_change(port)
            self.window.destroy()
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректный порт (1-65535)")

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
        self.tray_icon = None
        self.root = tk.Tk()
        self.root.withdraw()

    def run(self):
        self.listener.start()
        self._create_tray_icon()
        self.root.mainloop()

    def _create_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem("Настройки", self._show_settings),
            pystray.MenuItem("Выход", self._exit_app)
        )
        self.tray_icon = pystray.Icon(
            "InventoryServer",
            create_tray_icon(),
            "Inventory Server",
            menu
        )
        self.tray_icon.run_detached()

    def _show_settings(self, icon=None, item=None):
        def on_port_change(new_port):
            self.listener.stop()
            self.listener = NetworkListener(new_port, self.db_manager)
            self.listener.start()

        # Schedule in main thread to avoid threading issues with Tkinter
        self.root.after(0, lambda: SettingsWindow(self.root, self.config_manager, on_port_change))

    def _exit_app(self, icon=None, item=None):
        self.listener.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.quit)


if __name__ == "__main__":
    server = InventoryServer()
    server.run()