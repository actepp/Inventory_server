import sys
import os
import socket
import threading
import json
import logging
import time
import uuid
import platform
import subprocess
import psutil
import wmi
from datetime import datetime, timedelta
from pathlib import Path

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

CONFIG_FILE = get_base_dir() / "client_config.json"
DEFAULT_SERVER_IP = "127.0.0.1"
DEFAULT_SERVER_PORT = 5000
DEFAULT_INTERVAL_HOURS = 1
DEFAULT_INTERVAL_MINUTES = 0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ClientConfigManager:
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
        return {
            'server_ip': DEFAULT_SERVER_IP,
            'server_port': DEFAULT_SERVER_PORT,
            'interval_hours': DEFAULT_INTERVAL_HOURS,
            'interval_minutes': DEFAULT_INTERVAL_MINUTES
        }

    def save(self, config: dict):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            self.config = config
            logger.info("Client config saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    def is_configured(self):
        return self.config_path.exists()

    def get_server_ip(self):
        return self.config.get('server_ip', DEFAULT_SERVER_IP)

    def get_server_port(self):
        return self.config.get('server_port', DEFAULT_SERVER_PORT)

    def get_interval_hours(self):
        return self.config.get('interval_hours', DEFAULT_INTERVAL_HOURS)

    def get_interval_minutes(self):
        return self.config.get('interval_minutes', DEFAULT_INTERVAL_MINUTES)

    def get_interval_seconds(self):
        return self.get_interval_hours() * 3600 + self.get_interval_minutes() * 60

    def set_config(self, server_ip: str, server_port: int, interval_hours: int, interval_minutes: int):
        self.config['server_ip'] = server_ip
        self.config['server_port'] = server_port
        self.config['interval_hours'] = interval_hours
        self.config['interval_minutes'] = interval_minutes
        self.save(self.config)


class SystemInfoCollector:
    def __init__(self):
        self.wmi_conn = None
        try:
            self.wmi_conn = wmi.WMI()
        except Exception as e:
            logger.warning(f"WMI not available: {e}")

    def collect(self):
        data = {
            'hostname': platform.node(),
            'mac_address': self._get_mac_address(),
            'os_name': platform.system(),
            'os_version': platform.version(),
            'cpu_info': self._get_cpu_info(),
            'ram_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
            'devices': self._get_devices()
        }
        return data

    def _get_mac_address(self):
        try:
            mac = uuid.UUID(int=uuid.getnode()).hex[-12:]
            return ':'.join([mac[i:i+2] for i in range(0, 12, 2)])
        except:
            return "Unknown"

    def _get_cpu_info(self):
        try:
            if self.wmi_conn:
                for cpu in self.wmi_conn.Win32_Processor():
                    return cpu.Name.strip()
        except:
            pass
        return platform.processor() or "Unknown"

    def _get_devices(self):
        devices = []
        try:
            if self.wmi_conn:
                for device in self.wmi_conn.Win32_PnPEntity():
                    if device.DeviceID and device.Name:
                        devices.append({
                            'class': device.ClassGuid or "Unknown",
                            'name': device.Name.strip(),
                            'device_id': device.DeviceID.strip(),
                            'manufacturer': device.Manufacturer.strip() if device.Manufacturer else "Unknown",
                            'driver_version': device.DriverVersion.strip() if device.DriverVersion else "Unknown",
                            'status': device.Status.strip() if device.Status else "Unknown"
                        })
        except Exception as e:
            logger.warning(f"WMI device collection failed, using fallback: {e}")
            devices = self._get_devices_fallback()
        return devices

    def _get_devices_fallback(self):
        """Fallback device collection using basic system info"""
        devices = []
        try:
            # Add basic CPU as a device
            devices.append({
                'class': 'Processor',
                'name': self._get_cpu_info(),
                'device_id': 'CPU0',
                'manufacturer': 'Unknown',
                'driver_version': 'N/A',
                'status': 'OK'
            })
            # Add memory info
            mem = psutil.virtual_memory()
            devices.append({
                'class': 'Memory',
                'name': f'RAM {round(mem.total / (1024**3), 2)} GB',
                'device_id': 'RAM0',
                'manufacturer': 'Unknown',
                'driver_version': 'N/A',
                'status': 'OK'
            })
            # Add disk info
            for partition in psutil.disk_partitions():
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    devices.append({
                        'class': 'DiskDrive',
                        'name': f'{partition.device} ({partition.fstype})',
                        'device_id': partition.device,
                        'manufacturer': 'Unknown',
                        'driver_version': 'N/A',
                        'status': 'OK'
                    })
                except:
                    pass
            # Add network interfaces
            for name, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        devices.append({
                            'class': 'NetworkAdapter',
                            'name': name,
                            'device_id': addr.address,
                            'manufacturer': 'Unknown',
                            'driver_version': 'N/A',
                            'status': 'OK'
                        })
        except Exception as e:
            logger.error(f"Fallback device collection failed: {e}")
        return devices


class ClientAgent:
    def __init__(self):
        self.config_manager = ClientConfigManager(CONFIG_FILE)
        self.info_collector = SystemInfoCollector()
        self.tray_icon = None
        self.running = False
        self.send_thread = None
        self.error_message = ""
        self.last_status = "idle"
        self.root = tk.Tk()
        self.root.withdraw()

    def run(self):
        self.running = True
        
        if not self.config_manager.is_configured():
            self.root.after(100, self._show_settings_first_run)
        else:
            self._start_sender()
        
        self._create_tray_icon()
        self.root.mainloop()

    def _start_sender(self):
        if self.send_thread and self.send_thread.is_alive():
            return
        self.send_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self.send_thread.start()

    def _sender_loop(self):
        while self.running:
            interval = self.config_manager.get_interval_seconds()
            if interval <= 0:
                interval = 3600
            
            self._send_inventory()
            
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

    def _send_inventory(self):
        try:
            data = self.info_collector.collect()
            json_data = json.dumps(data)
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15.0)
            sock.connect((self.config_manager.get_server_ip(), self.config_manager.get_server_port()))
            sock.sendall(json_data.encode('utf-8'))
            sock.shutdown(socket.SHUT_WR)
            
            response = sock.recv(1024).decode('utf-8')
            sock.close()
            
            if response == "OK":
                self._set_status("idle", "")
                logger.info("Inventory sent successfully")
            elif response == "ERROR_FORMAT":
                self._set_status("error", "Сервер: неверный формат данных")
                logger.warning("Server returned format error")
            else:
                self._set_status("error", f"Сервер: неизвестный ответ: {response}")
                logger.warning(f"Unknown server response: {response}")
                
        except socket.timeout:
            self._set_status("error", "Таймаут: сервер не ответил за 15 секунд")
            logger.warning("Server timeout")
        except ConnectionRefusedError:
            self._set_status("error", "Ошибка соединения: сервер недоступен")
            logger.warning("Connection refused")
        except Exception as e:
            self._set_status("error", f"Ошибка отправки: {str(e)}")
            logger.error(f"Send error: {e}")

    def _set_status(self, status: str, error_msg: str = ""):
        self.last_status = status
        self.error_message = error_msg
        if self.tray_icon:
            if status == "error":
                self.tray_icon.icon = create_tray_icon(error=True)
                self.tray_icon.title = "Inventory Client - Ошибка"
            else:
                self.tray_icon.icon = create_tray_icon(error=False)
                self.tray_icon.title = "Inventory Client"

    def _show_settings_first_run(self):
        SettingsWindow(self.root, self.config_manager, self, first_run=True)

    def _show_settings(self):
        SettingsWindow(self.root, self.config_manager, self, first_run=False)

    def _create_tray_icon(self):
        menu = pystray.Menu(
            pystray.MenuItem("Настройки", self._show_settings),
            pystray.MenuItem("Выход", self._exit_app)
        )
        self.tray_icon = DoubleClickIcon(
            "InventoryClient",
            create_tray_icon(error=False),
            "Inventory Client",
            menu,
            on_double_click=lambda icon: self.root.after(0, self._show_settings)
        )
        self.tray_icon.run_detached()

    def _exit_app(self, icon=None, item=None):
        self.running = False
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.quit)


def create_tray_icon(error=False):
    image = Image.new('RGB', (64, 64), color='white')
    draw = ImageDraw.Draw(image)
    color = 'red' if error else 'green'
    draw.ellipse([16, 16, 48, 48], fill=color)
    if error:
        draw.text((28, 20), "!", fill='white')
    else:
        draw.text((22, 22), "INV", fill='white')
    return image


class SettingsWindow:
    def __init__(self, parent, config_manager: ClientConfigManager, agent: ClientAgent, first_run: bool):
        self.config_manager = config_manager
        self.agent = agent
        self.first_run = first_run
        self.window = tk.Toplevel(parent)
        self.window.title("Настройки клиента инвентаризации")
        self.window.geometry("380x380")
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

        # Server IP
        ttk.Label(main_frame, text="IP адрес сервера:").pack(anchor=tk.W, pady=(0, 5))
        self.ip_var = tk.StringVar(value=self.config_manager.get_server_ip())
        ttk.Entry(main_frame, textvariable=self.ip_var, width=30).pack(fill=tk.X, pady=(0, 10))

        # Server Port
        ttk.Label(main_frame, text="Порт сервера:").pack(anchor=tk.W, pady=(0, 5))
        self.port_var = tk.StringVar(value=str(self.config_manager.get_server_port()))
        ttk.Entry(main_frame, textvariable=self.port_var, width=30).pack(fill=tk.X, pady=(0, 10))

        # Interval
        ttk.Label(main_frame, text="Интервал отправки:").pack(anchor=tk.W, pady=(0, 5))
        interval_frame = ttk.Frame(main_frame)
        interval_frame.pack(fill=tk.X, pady=(0, 10))

        self.hours_var = tk.StringVar(value=str(self.config_manager.get_interval_hours()))
        self.minutes_var = tk.StringVar(value=str(self.config_manager.get_interval_minutes()))

        ttk.Entry(interval_frame, textvariable=self.hours_var, width=5).pack(side=tk.LEFT)
        ttk.Label(interval_frame, text="ч").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Entry(interval_frame, textvariable=self.minutes_var, width=5).pack(side=tk.LEFT)
        ttk.Label(interval_frame, text="мин").pack(side=tk.LEFT)

        # Error log
        ttk.Label(main_frame, text="Статус / Ошибки:").pack(anchor=tk.W, pady=(10, 5))
        self.error_text = tk.Text(main_frame, height=4, wrap=tk.WORD, state=tk.DISABLED)
        self.error_text.pack(fill=tk.X, pady=(0, 10))
        self._update_error_display()

        # Spacer
        ttk.Frame(main_frame).pack(fill=tk.BOTH, expand=True)

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, anchor=tk.E)

        ttk.Button(btn_frame, text="Сохранить", command=self.on_save).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Отмена", command=self.on_cancel).pack(side=tk.RIGHT)

    def _update_error_display(self):
        self.error_text.config(state=tk.NORMAL)
        self.error_text.delete(1.0, tk.END)
        if self.agent.error_message:
            self.error_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {self.agent.error_message}")
        else:
            self.error_text.insert(tk.END, "Ошибок нет")
        self.error_text.config(state=tk.DISABLED)

    def _center_window(self):
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() // 2) - (self.window.winfo_width() // 2)
        y = (self.window.winfo_screenheight() // 2) - (self.window.winfo_height() // 2)
        self.window.geometry(f"+{x}+{y}")

    def on_save(self):
        try:
            server_ip = self.ip_var.get().strip()
            if not server_ip:
                raise ValueError("IP адрес не может быть пустым")
            
            server_port = int(self.port_var.get())
            if not (1 <= server_port <= 65535):
                raise ValueError("Порт должен быть 1-65535")
            
            hours = int(self.hours_var.get())
            minutes = int(self.minutes_var.get())
            if hours < 0 or minutes < 0 or minutes >= 60:
                raise ValueError("Некорректное время (часы >= 0, минуты 0-59)")
            if hours == 0 and minutes == 0:
                raise ValueError("Интервал не может быть 0")
            
            self.config_manager.set_config(server_ip, server_port, hours, minutes)
            
            self.agent._set_status("idle", "")
            self._update_error_display()
            
            if self.first_run:
                self.agent._start_sender()
            
            self.window.destroy()
        except ValueError as e:
            messagebox.showerror("Ошибка", str(e))

    def on_cancel(self):
        if self.first_run:
            if messagebox.askyesno("Выход", "Настройки не сохранены. Выйти из программы?"):
                self.agent._exit_app()
        else:
            self.window.destroy()


if __name__ == "__main__":
    client = ClientAgent()
    client.run()