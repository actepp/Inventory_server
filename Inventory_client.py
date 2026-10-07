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
import pythoncom
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

    def _get_wmi_conn(self):
        """Get or create WMI connection with proper COM initialization"""
        if self.wmi_conn is None:
            try:
                pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
                self.wmi_conn = wmi.WMI()
            except Exception as e:
                logger.warning(f"WMI not available: {e}")
                self.wmi_conn = False  # Use False to indicate failed initialization
        return self.wmi_conn if self.wmi_conn is not False else None

    def collect(self):
        # Initialize COM for this thread
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except:
            pass
            
        cpu_info = self._get_detailed_cpu_info()
        ram_info = self._get_detailed_ram_info()
        gpu_info = self._get_gpu_info()
        monitor_info = self._get_monitor_info()
        motherboard_info = self._get_motherboard_info()
        
        devices = self._get_devices()
        # Add detailed hardware as devices
        devices.extend(cpu_info)
        devices.extend(ram_info)
        devices.extend(gpu_info)
        devices.extend(monitor_info)
        devices.extend(motherboard_info)
        
        data = {
            'hostname': platform.node(),
            'mac_address': self._get_mac_address(),
            'os_name': platform.system(),
            'os_version': platform.version(),
            'cpu_info': cpu_info[0]['name'] if cpu_info else self._get_cpu_info(),
            'ram_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
            'devices': devices
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
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for cpu in wmi_conn.Win32_Processor():
                    return cpu.Name.strip()
        except:
            pass
        return platform.processor() or "Unknown"

    def _get_detailed_cpu_info(self):
        """Get detailed CPU info as device entries"""
        devices = []
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for cpu in wmi_conn.Win32_Processor():
                    name = cpu.Name.strip() if cpu.Name else "Unknown Processor"
                    cores = cpu.NumberOfCores if cpu.NumberOfCores else 0
                    threads = cpu.NumberOfLogicalProcessors if cpu.NumberOfLogicalProcessors else 0
                    max_clock = cpu.MaxClockSpeed if cpu.MaxClockSpeed else 0
                    current_clock = cpu.CurrentClockSpeed if cpu.CurrentClockSpeed else 0
                    manufacturer = cpu.Manufacturer.strip() if cpu.Manufacturer else "Unknown"
                    
                    devices.append({
                        'class': 'Processor',
                        'name': f'{name} ({cores}C/{threads}T, {max_clock} MHz)',
                        'device_id': cpu.DeviceID.strip() if cpu.DeviceID else 'CPU0',
                        'manufacturer': manufacturer,
                        'driver_version': f'Cores: {cores}, Threads: {threads}, Max Clock: {max_clock} MHz, Current: {current_clock} MHz',
                        'status': cpu.Status.strip() if cpu.Status else 'OK'
                    })
        except Exception as e:
            logger.warning(f"Detailed CPU collection failed: {e}")
        return devices

    def _get_detailed_ram_info(self):
        """Get detailed RAM stick info"""
        devices = []
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for mem in wmi_conn.Win32_PhysicalMemory():
                    capacity_gb = round(int(mem.Capacity) / (1024**3), 2) if mem.Capacity else 0
                    speed = mem.Speed if mem.Speed else 0
                    manufacturer = mem.Manufacturer.strip() if mem.Manufacturer else "Unknown"
                    part_number = mem.PartNumber.strip() if mem.PartNumber else "Unknown"
                    serial = mem.SerialNumber.strip() if mem.SerialNumber else "Unknown"
                    device_locator = mem.DeviceLocator.strip() if mem.DeviceLocator else "Unknown"
                    bank_label = mem.BankLabel.strip() if mem.BankLabel else "Unknown"
                    
                    # Memory type mapping
                    mem_type_map = {
                        20: 'DDR',
                        21: 'DDR2',
                        22: 'DDR2 FB-DIMM',
                        23: 'DDR2 FB-DIMM',
                        24: 'DDR3',
                        25: 'FBD2',
                        26: 'DDR4',
                        27: 'DDR5'
                    }
                    mem_type = mem_type_map.get(mem.MemoryType, '') if mem.MemoryType else ''
                    # Infer from speed if type is unknown
                    if not mem_type or mem_type.startswith('Type'):
                        if speed >= 4800:
                            mem_type = 'DDR5'
                        elif speed >= 2133:
                            mem_type = 'DDR4'
                        elif speed >= 800:
                            mem_type = 'DDR3'
                        else:
                            mem_type = 'DDR'
                    # Also check part number for DDR info
                    if 'DDR5' in part_number.upper():
                        mem_type = 'DDR5'
                    elif 'DDR4' in part_number.upper():
                        mem_type = 'DDR4'
                    elif 'DDR3' in part_number.upper():
                        mem_type = 'DDR3'
                    elif 'DDR2' in part_number.upper():
                        mem_type = 'DDR2'
                    
                    devices.append({
                        'class': 'Memory',
                        'name': f'{device_locator} / {bank_label}: {capacity_gb} GB {mem_type} {speed} MHz',
                        'device_id': mem.Tag.strip() if mem.Tag else 'RAM',
                        'manufacturer': manufacturer,
                        'driver_version': f'Part: {part_number}, Serial: {serial}, Speed: {speed} MHz, Type: {mem_type}',
                        'status': 'OK'
                    })
        except Exception as e:
            logger.warning(f"Detailed RAM collection failed: {e}")
        return devices

    def _get_gpu_info(self):
        """Get video card info"""
        devices = []
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for gpu in wmi_conn.Win32_VideoController():
                    name = gpu.Name.strip() if gpu.Name else "Unknown GPU"
                    vram = round(int(gpu.AdapterRAM) / (1024**3), 2) if gpu.AdapterRAM else 0
                    driver_version = gpu.DriverVersion.strip() if gpu.DriverVersion else "Unknown"
                    driver_date = gpu.DriverDate.strip() if gpu.DriverDate else "Unknown"
                    manufacturer = gpu.AdapterCompatibility.strip() if gpu.AdapterCompatibility else "Unknown"
                    video_mode = gpu.VideoModeDescription.strip() if gpu.VideoModeDescription else "Unknown"
                    pnp_device_id = gpu.PNPDeviceID.strip() if gpu.PNPDeviceID else "Unknown"
                    
                    devices.append({
                        'class': 'DisplayAdapter',
                        'name': f'{name} ({vram} GB VRAM)',
                        'device_id': pnp_device_id,
                        'manufacturer': manufacturer,
                        'driver_version': f'Driver: {driver_version} ({driver_date}), Mode: {video_mode}',
                        'status': gpu.Status.strip() if gpu.Status else 'OK'
                    })
        except Exception as e:
            logger.warning(f"GPU collection failed: {e}")
        return devices

    def _get_monitor_info(self):
        """Get monitor info with actual current resolution from video controllers"""
        devices = []
        monitor_details = {}
        
        # Get actual current resolutions from video controllers (per-GPU)
        video_resolutions = []
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for vc in wmi_conn.Win32_VideoController():
                    if vc.CurrentHorizontalResolution and vc.CurrentVerticalResolution:
                        w = vc.CurrentHorizontalResolution
                        h = vc.CurrentVerticalResolution
                        video_resolutions.append((w, h))
        except Exception as e:
            logger.warning(f"Video controller resolution collection failed: {e}")
        
        # Sort by area descending (highest resolution first)
        video_resolutions.sort(key=lambda r: r[0] * r[1], reverse=True)
        
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                # First, get detailed monitor info from WmiMonitorID (root\wmi)
                try:
                    wmi_root = wmi.WMI(namespace='root\\wmi')
                    for m in wmi_root.WmiMonitorID():
                        instance_name = m.InstanceName.strip() if m.InstanceName else ""
                        # Decode byte arrays to strings
                        def decode_bytes(byte_array):
                            if byte_array:
                                return ''.join(chr(b) for b in byte_array if b != 0)
                            return ""
                        
                        user_friendly = decode_bytes(m.UserFriendlyName)
                        manufacturer = decode_bytes(m.ManufacturerName)
                        product_code = decode_bytes(m.ProductCodeID)
                        serial = decode_bytes(m.SerialNumberID)
                        
                        if instance_name:
                            monitor_details[instance_name] = {
                                'name': user_friendly or "Unknown Monitor",
                                'manufacturer': manufacturer or "Unknown",
                                'product_code': product_code,
                                'serial': serial
                            }
                except Exception as e:
                    logger.warning(f"WmiMonitorID collection failed: {e}")
                
                # Now get Win32_DesktopMonitor and match with detailed info
                # First, collect all monitors from WMI
                wmi_monitors = []
                for monitor in wmi_conn.Win32_DesktopMonitor():
                    name = monitor.Name.strip() if monitor.Name else "Unknown Monitor"
                    manufacturer = monitor.MonitorManufacturer.strip() if monitor.MonitorManufacturer else "Unknown"
                    monitor_type = monitor.MonitorType.strip() if monitor.MonitorType else "Unknown"
                    pnp_device_id = monitor.PNPDeviceID.strip() if monitor.PNPDeviceID else "Unknown"
                    
                    # Get resolution from monitor's reported resolution (EDID preferred)
                    resolution = 'Unknown'
                    screen_width = monitor.ScreenWidth if monitor.ScreenWidth else 0
                    screen_height = monitor.ScreenHeight if monitor.ScreenHeight else 0
                    has_edid_resolution = False
                    if screen_width and screen_height:
                        resolution = f'{screen_width}x{screen_height}'
                        has_edid_resolution = True
                    
                    # Try to match with detailed info from WmiMonitorID
                    detailed = None
                    pnp_lower = pnp_device_id.lower()
                    for inst_name, info in monitor_details.items():
                        inst_lower = inst_name.lower()
                        if inst_lower.startswith(pnp_lower) or pnp_lower in inst_lower:
                            detailed = info
                            break
                    
                    if detailed:
                        display_name = detailed['name']
                        display_manufacturer = detailed['manufacturer']
                        extra_info = f"Product: {detailed['product_code']}, Serial: {detailed['serial']}"
                    else:
                        display_name = name
                        display_manufacturer = manufacturer
                        extra_info = ""
                    
                    wmi_monitors.append({
                        'display_name': display_name,
                        'display_manufacturer': display_manufacturer,
                        'extra_info': extra_info,
                        'monitor_type': monitor_type,
                        'pnp_device_id': pnp_device_id,
                        'resolution': resolution,
                        'has_edid_resolution': has_edid_resolution,
                        'status': monitor.Status.strip() if monitor.Status else 'OK'
                    })
                
# Now assign actual resolutions from video controllers
                # Build a set of GPU resolutions for quick lookup
                gpu_resolutions = set()
                for w, h in video_resolutions:
                    gpu_resolutions.add((w, h))
                
                # Track which video resolutions have been assigned
                assigned_resolutions = set()
                
                for wmi_mon in wmi_monitors:
                    final_resolution = wmi_mon['resolution']
                    
                    # If no EDID resolution, report Unknown (can't reliably map to GPU)
                    if not wmi_mon['has_edid_resolution']:
                        final_resolution = 'Unknown'
                    # If EDID resolution matches a GPU resolution, monitor is likely on that GPU
                    # Use the GPU's current resolution (which may be higher than EDID preferred)
                    elif wmi_mon['has_edid_resolution'] and video_resolutions:
                        edid_width = wmi_mon['resolution'].split('x')[0] if 'x' in wmi_mon['resolution'] else 0
                        edid_height = wmi_mon['resolution'].split('x')[1] if 'x' in wmi_mon['resolution'] else 0
                        try:
                            edid_w = int(edid_width)
                            edid_h = int(edid_height)
                            # Check if EDID resolution matches any GPU resolution
                            if (edid_w, edid_h) in gpu_resolutions:
                                # Find the matching GPU and use its resolution
                                for i, (w, h) in enumerate(video_resolutions):
                                    if i not in assigned_resolutions and w == edid_w and h == edid_h:
                                        final_resolution = f"{w}x{h}"
                                        assigned_resolutions.add(i)
                                        break
                            # If EDID doesn't match any GPU, keep EDID resolution
                        except (ValueError, IndexError):
                            pass
                    
                    driver_version = f'Type: {wmi_mon["monitor_type"]}, Resolution: {final_resolution}'
                    if wmi_mon['extra_info']:
                        driver_version += f', {wmi_mon["extra_info"]}'
                    
                    devices.append({
                        'class': 'Monitor',
                        'name': f'{wmi_mon["display_name"]} ({final_resolution})',
                        'device_id': wmi_mon['pnp_device_id'],
                        'manufacturer': wmi_mon['display_manufacturer'],
                        'driver_version': driver_version,
                        'status': wmi_mon['status']
                    })
        except Exception as e:
            logger.warning(f"Monitor collection failed: {e}")
        return devices

    def _get_motherboard_info(self):
        """Get motherboard info"""
        devices = []
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for mb in wmi_conn.Win32_BaseBoard():
                    manufacturer = mb.Manufacturer.strip() if mb.Manufacturer else "Unknown"
                    product = mb.Product.strip() if mb.Product else "Unknown"
                    version = mb.Version.strip() if mb.Version else "Unknown"
                    serial = mb.SerialNumber.strip() if mb.SerialNumber else "Unknown"
                    
                    devices.append({
                        'class': 'Motherboard',
                        'name': f'{manufacturer} {product} (Rev {version})',
                        'device_id': mb.Tag.strip() if mb.Tag else 'MB0',
                        'manufacturer': manufacturer,
                        'driver_version': f'Version: {version}, Serial: {serial}',
                        'status': 'OK'
                    })
        except Exception as e:
            logger.warning(f"Motherboard collection failed: {e}")
        return devices

    def _get_network_adapters(self):
        """Get network adapters with link speed"""
        devices = []
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for na in wmi_conn.Win32_NetworkAdapter():
                    if na.NetConnectionStatus == 2 and na.Speed:  # Connected and has speed
                        name = na.Name.strip() if na.Name else "Unknown"
                        speed = na.Speed
                        # Speed can be string or int
                        try:
                            speed = int(speed)
                        except (ValueError, TypeError):
                            speed = 0
                        
                        # Format speed
                        if speed >= 1000000000:
                            speed_str = f'{speed // 1000000000} Gbps'
                        elif speed >= 1000000:
                            speed_str = f'{speed // 1000000} Mbps'
                        else:
                            speed_str = f'{speed} bps'
                        
                        mac = na.MACAddress.strip() if na.MACAddress else "Unknown"
                        manufacturer = na.Manufacturer.strip() if na.Manufacturer else "Unknown"
                        
                        devices.append({
                            'class': 'NetworkAdapter',
                            'name': name,
                            'device_id': mac,
                            'manufacturer': manufacturer,
                            'driver_version': f'Speed: {speed_str}',
                            'status': 'OK'
                        })
        except Exception as e:
            logger.warning(f"Network adapter collection failed: {e}")
        return devices

    def _get_devices(self):
        devices = []
        try:
            wmi_conn = self._get_wmi_conn()
            if wmi_conn:
                for device in wmi_conn.Win32_PnPEntity():
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
        
        # Add network adapters with speed info
        devices.extend(self._get_network_adapters())
        return devices

    def _get_devices_fallback(self):
        """Fallback device collection using basic system info (for non-WMI systems)"""
        devices = []
        try:
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