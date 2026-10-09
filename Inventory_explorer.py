import sys
import json
import logging
import threading
import time
import queue
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from packaging import version

import tkinter as tk
from tkinter import ttk, messagebox, font

# Setup logging with file handler for command tracing
def setup_logger(name, log_file):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler for command tracing
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)
    
    return logger

base_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
logger = setup_logger('InventoryExplorer', base_dir / "Inventory_explorer.log")
cmd_logger = setup_logger('InventoryExplorer.Commands', base_dir / "Inventory_explorer.log")

def get_os_display_name(os_name: str, os_version: str) -> str:
    """Get proper OS display name (e.g., Windows 11 vs Windows 10)"""
    if os_name == 'Windows':
        try:
            build = int(os_version.split('.')[-1]) if '.' in os_version else 0
            if build >= 22000:
                return f'Windows 11 (build {build})'
        except:
            pass
        return f'Windows 10 (build {os_version})'
    return f'{os_name} {os_version}'.strip()


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class PushNotificationHandler(BaseHTTPRequestHandler):
    """Handle push notifications from server"""
    def __init__(self, *args, explorer=None, **kwargs):
        self.explorer = explorer
        super().__init__(*args, **kwargs)

    def do_POST(self):
        if self.path == '/push':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                if data.get('type') == 'data_updated':
                    logger.info("Received push notification: data_updated")
                    if self.explorer:
                        self.explorer._msg_queue.put(('push_update', None))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                logger.error(f"Error handling push notification: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default log messages
        pass


class PushNotificationServer:
    """Simple HTTP server to receive push notifications from inventory server"""
    def __init__(self, explorer):
        self.explorer = explorer
        self.server = None
        self.thread = None
        self.port = None
        self.callback_url = None

    def start(self):
        """Start the server on a random available port"""
        # Find an available port
        for port in range(50100, 50200):
            try:
                handler = lambda *args, **kwargs: PushNotificationHandler(*args, explorer=self.explorer, **kwargs)
                self.server = HTTPServer(('127.0.0.1', port), handler)
                self.port = port
                self.callback_url = f"http://127.0.0.1:{port}/push"
                break
            except OSError:
                continue
        
        if not self.server:
            logger.error("Failed to find available port for push notifications")
            return False

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info(f"Push notification server started on port {self.port}")
        return True

    def _run(self):
        self.server.serve_forever()

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
            except:
                pass
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("Push notification server stopped")

    def get_callback_url(self):
        return self.callback_url


CONFIG_FILE = get_base_dir() / "explorer_config.json"
DEFAULT_SERVER_IP = "127.0.0.1"
DEFAULT_SERVER_PORT = 5001
DEFAULT_API_PORT = 5002
DEFAULT_CHECK_INTERVAL = 30  # seconds
EXPLORER_VERSION = "1.0.0"

# GitHub repository for updates (change to your repo)
GITHUB_REPO = "actepp/Inventory_server"  # Формат: "username/repository"
GITHUB_VERSION_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/version.txt"


class ExplorerConfig:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = self.load()

    def load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Migration: add check_interval if missing
                    if 'check_interval' not in config:
                        config['check_interval'] = DEFAULT_CHECK_INTERVAL
                        self.save(config)
                    return config
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        # First run - create default config
        default_config = {
            'server_ip': DEFAULT_SERVER_IP,
            'server_port': DEFAULT_SERVER_PORT,
            'api_port': DEFAULT_API_PORT,
            'check_interval': DEFAULT_CHECK_INTERVAL
        }
        self.save(default_config)
        return default_config

    def save(self, config=None):
        if config is not None:
            self.config = config
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            logger.info("Explorer config saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    def get_server_ip(self):
        return self.config.get('server_ip', DEFAULT_SERVER_IP)

    def get_server_port(self):
        return self.config.get('server_port', DEFAULT_SERVER_PORT)

    def get_api_port(self):
        return self.config.get('api_port', DEFAULT_API_PORT)

    def get_check_interval(self):
        return self.config.get('check_interval', DEFAULT_CHECK_INTERVAL)

    def set_config(self, server_ip: str, server_port: int, api_port: int, check_interval: int):
        self.config['server_ip'] = server_ip
        self.config['server_port'] = server_port
        self.config['api_port'] = api_port
        self.config['check_interval'] = check_interval
        self.save()


class APIClient:
    def __init__(self, config: ExplorerConfig):
        self.config = config

    def _make_request(self, method: str, endpoint: str, data=None):
        url = f"http://{self.config.get_server_ip()}:{self.config.get_api_port()}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        
        if data:
            req_data = json.dumps(data).encode('utf-8')
        else:
            req_data = None
            
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            try:
                error_json = json.loads(error_body)
                raise Exception(error_json.get('error', str(e)))
            except:
                raise Exception(f"HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise Exception(f"Connection error: {e}")
        except Exception as e:
            raise Exception(f"Request failed: {e}")

    def get_computers(self):
        return self._make_request('GET', '/api/computers')

    def get_online(self):
        return self._make_request('GET', '/api/online')

    def get_devices(self, computer_id):
        return self._make_request('GET', f'/api/computers/{computer_id}/devices')

    def delete_computer(self, computer_id):
        return self._make_request('DELETE', f'/api/computers/{computer_id}')

    def poll_computer(self, computer_id):
        cmd_logger.info(f"COMMAND SENT: Explorer -> Server API | POLL_COMPUTER | computer_id={computer_id}")
        result = self._make_request('POST', f'/api/computers/{computer_id}/poll')
        cmd_logger.info(f"COMMAND RESPONSE: Server API -> Explorer | POLL_COMPUTER | computer_id={computer_id} | result={result}")
        return result

    def poll_all_computers(self):
        cmd_logger.info(f"COMMAND SENT: Explorer -> Server API | POLL_ALL_COMPUTERS")
        result = self._make_request('POST', '/api/computers/poll-all')
        cmd_logger.info(f"COMMAND RESPONSE: Server API -> Explorer | POLL_ALL_COMPUTERS | result={result}")
        return result


class InventoryExplorer:
    def __init__(self):
        self.config = ExplorerConfig(CONFIG_FILE)
        self.api_client = APIClient(self.config)
        self.root = tk.Tk()
        self.root.title("Инвентаризация - Обозреватель")
        self.root.geometry("1000x700")
        self.root.minsize(800, 600)
        
        self.computers_data = {}
        self.selected_computer_id = None
        self._status_check_running = True
        self._was_offline = False
        self._refreshing = False
        self._msg_queue = queue.Queue()
        
        # Push notification server
        self.push_server = PushNotificationServer(self)
        self._push_registered = False
        
        self._setup_styles()
        self._create_ui()
        self._create_menu()
        self._start_push_server()
        self._refresh_data()
        self._start_status_checker()
        self._process_queue()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure('Treeview', rowheight=24, font=('Segoe UI', 9))
        style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'))
        style.map('Treeview', background=[('selected', '#0078d7')])

    def _create_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Toolbar
        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(toolbar, text="📡 Опросить все", command=self._poll_all).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(toolbar, text="🗑 Удалить ПК", command=self._delete_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="🔄 Обновить", command=self._refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="⚙ Настройки", command=self._show_settings).pack(side=tk.LEFT, padx=5)

        # Server agent status
        self.server_status_var = tk.StringVar(value="серверный агент оффлайн")
        self.server_status_label = ttk.Label(toolbar, textvariable=self.server_status_var, font=('Segoe UI', 9, 'bold'), foreground='red')
        self.server_status_label.pack(side=tk.RIGHT, padx=10)

        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side=tk.RIGHT)

        # Tree view
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ('info',)
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='tree headings')
        
        self.tree.heading('#0', text='Компьютер', anchor=tk.W)
        self.tree.heading('info', text='Информация', anchor=tk.W)

        self.tree.column('#0', width=300, minwidth=250, stretch=False)
        self.tree.column('info', width=500, minwidth=300, stretch=True)

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind('<<TreeviewOpen>>', self._on_tree_open)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        # Context menu
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Опросить", command=self._poll_selected)
        self.context_menu.add_command(label="Удалить", command=self._delete_selected)
        self.tree.bind("<Button-3>", self._show_context_menu)

    def _create_menu(self):
        menubar = tk.Menu(self.root)
        
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Обновить (F5)", command=self._refresh_data, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Настройки", command=self._show_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self._on_close)
        menubar.add_cascade(label="Файл", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе", command=self._show_about)
        menubar.add_cascade(label="Справка", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind('<F5>', lambda e: self._refresh_data())

    def _start_push_server(self):
        """Start push notification server and register with inventory server"""
        if self.push_server.start():
            callback_url = self.push_server.get_callback_url()
            threading.Thread(target=self._register_push_callback, args=(callback_url,), daemon=True).start()

    def _register_push_callback(self, callback_url):
        """Register callback URL with inventory server"""
        try:
            url = f"http://{self.config.get_server_ip()}:{self.config.get_api_port()}/api/explorer/register?callback={urllib.parse.quote(callback_url)}"
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('success'):
                    self._push_registered = True
                    cmd_logger.info(f"COMMAND SENT: Explorer -> Server API | REGISTER_PUSH | callback={callback_url}")
                    logger.info(f"Registered for push notifications: {callback_url}")
        except Exception as e:
            logger.warning(f"Failed to register push callback: {e}")

    def _unregister_push_callback(self):
        """Unregister callback URL from inventory server"""
        if not self._push_registered:
            return
        try:
            callback_url = self.push_server.get_callback_url()
            url = f"http://{self.config.get_server_ip()}:{self.config.get_api_port()}/api/explorer/unregister?callback={urllib.parse.quote(callback_url)}"
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('success'):
                    self._push_registered = False
                    cmd_logger.info(f"COMMAND SENT: Explorer -> Server API | UNREGISTER_PUSH | callback={callback_url}")
        except Exception as e:
            logger.warning(f"Failed to unregister push callback: {e}")

    def _show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            # Check if this is the client version node
            item_text = self.tree.item(item, 'text').strip()
            if item_text == '📦 Версия агент-клиента':
                self._show_client_version_context_menu(event, item)
            else:
                self.context_menu.post(event.x_root, event.y_root)

    def _show_client_version_context_menu(self, event, item):
        """Show context menu for client version node"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Обновить агент", command=lambda: self._check_client_update(item))
        menu.post(event.x_root, event.y_root)

    def _refresh_data(self):
        if getattr(self, '_refreshing', False):
            return
        self._refreshing = True
        self.status_var.set("Загрузка...")
        threading.Thread(target=self._fetch_computers, daemon=True).start()

    def _fetch_computers(self):
        try:
            computers = self.api_client.get_computers()
            online_data = self.api_client.get_online()
            self._msg_queue.put(('refresh_data', (computers, online_data)))
        except Exception as e:
            logger.error(f"Failed to fetch computers: {e}")
            self._msg_queue.put(('refresh_error', None))
        finally:
            self._msg_queue.put(('refresh_done', None))

    def _handle_refresh_data(self, data):
        computers, online_data = data
        self._update_tree(computers)
        self._set_server_online(online_data)

    def _handle_refresh_error(self):
        self._set_server_offline()
        self._was_offline = True
        self.status_var.set("Ошибка загрузки")

    def _set_server_online(self, online_data=None):
        server_version = online_data.get('server_version', '?') if online_data else '?'
        self.server_status_var.set(f"серверный агент онлайн v{server_version}")
        self.server_status_label.configure(foreground='green')

    def _set_server_offline(self):
        self.server_status_var.set("серверный агент оффлайн")
        self.server_status_label.configure(foreground='red')

    def _update_tree(self, computers, online_data=None):
        # Save expanded hostnames before rebuilding
        expanded_hostnames = self._get_expanded_hostnames()
        
        self.tree.delete(*self.tree.get_children())
        self.computers_data = {}
        
        # Track item_ids of computers that were expanded
        expanded_items = []
        
        for comp in computers:
            comp_id = comp['id']
            self.computers_data[comp_id] = comp
            
            item_id = self.tree.insert('', 'end', 
                text=f"  {comp['hostname']}",
                values=(''),
                open=False)
            
            # Add system info as child nodes
            self._add_system_info_nodes(item_id, comp)
            
            # Add placeholder for devices
            self.tree.insert(item_id, 'end', text='Загрузка...', values=(''))
            
            # Restore expanded state for this computer
            if comp['hostname'] in expanded_hostnames:
                self.tree.item(item_id, open=True)
                expanded_items.append(item_id)
        
        # Load devices for computers that were expanded
        for item_id in expanded_items:
            self._load_devices(item_id)
        
        self.status_var.set(f"Найдено ПК: {len(computers)}")
        
        # Force load all devices after a short delay
        if computers:
            self.root.after(1000, self._force_load_all_devices)

    def _force_load_all_devices(self):
        """Force load devices for all computers"""
        # Find all computer items and load their devices
        for item_id in self.tree.get_children():
            # Check if it has the "Загрузка..." placeholder
            children = self.tree.get_children(item_id)
            for child in children:
                if self.tree.item(child, 'text') == 'Загрузка...':
                    self.tree.delete(child)
                    self._load_devices(item_id)
                    break

    def _get_expanded_hostnames(self):
        """Get hostnames of expanded computer nodes"""
        expanded = set()
        for item in self.tree.get_children():
            if self.tree.item(item, 'open'):
                hostname = self.tree.item(item, 'text').strip()
                expanded.add(hostname)
        return expanded

    def _add_system_info_nodes(self, parent_item, comp):
        """Add system info as child nodes under computer"""
        # Client version
        client_version = comp.get('client_version', '?')
        self.tree.insert(parent_item, 'end', text='📦 Версия агент-клиента', values=(client_version,))
        
        # IP Address
        ip = comp.get('ip_address', 'Unknown')
        self.tree.insert(parent_item, 'end', text='🌐 IP адрес', values=(ip,))
        
        # OS
        os_name = comp.get('os_name', 'Unknown')
        os_version = comp.get('os_version', '')
        os_info = get_os_display_name(os_name, os_version)
        self.tree.insert(parent_item, 'end', text='🖥 Операционная система', values=(os_info,))
        
        # CPU
        cpu = comp.get('cpu_info', 'Unknown')
        self.tree.insert(parent_item, 'end', text='🔧 Процессор', values=(cpu,))
        
        # RAM
        ram = comp.get('ram_total_gb', 0)
        ram_text = f'{ram:.1f} GB' if ram else 'Unknown'
        self.tree.insert(parent_item, 'end', text='💾 Оперативная память', values=(ram_text,))
        
        # Last seen
        last_seen = comp.get('last_seen', '')
        if last_seen:
            try:
                dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                last_seen = dt.strftime('%d.%m.%Y %H:%M')
            except:
                pass
        self.tree.insert(parent_item, 'end', text='🕐 Последний раз', values=(last_seen or 'Unknown',))

    def _on_tree_open(self, event):
        item = self.tree.focus()
        if not item:
            return
        
        # Check if this is a computer node (has children with placeholder)
        children = self.tree.get_children(item)
        if children:
            for child in children:
                child_text = self.tree.item(child, 'text')
                if child_text == 'Загрузка...':
                    self.tree.delete(child)
                    self._load_devices(item)
                    return

    def _load_devices(self, computer_item):
        comp_id = None
        computer_text = self.tree.item(computer_item, 'text').strip()
        for cid, comp in self.computers_data.items():
            if computer_text == comp['hostname']:
                comp_id = cid
                break
        
        if not comp_id:
            return
        
        try:
            devices = self.api_client.get_devices(comp_id)
            self._populate_devices(computer_item, devices)
        except Exception as e:
            logger.error(f"Failed to load devices: {e}")
            self.tree.insert(computer_item, 'end', text=f'Ошибка: {e}', values=(''))

    def _populate_devices(self, computer_item, devices):
        # Remove the placeholder
        children = self.tree.get_children(computer_item)
        for child in children:
            if 'загрузка' in self.tree.item(child, 'text').lower():
                self.tree.delete(child)
                break
        
        # Group by class
        classes = {}
        for dev in devices:
            cls = dev.get('device_class', 'Unknown')
            if cls not in classes:
                classes[cls] = []
            classes[cls].append(dev)
        
        # Class icons and Russian names mapping
        class_icons = {
            'Processor': '🔧',
            'Memory': '💾',
            'DisplayAdapter': '🎮',
            'Monitor': '🖥',
            'Motherboard': '📋',
            'DiskDrive': '💿',
            'NetworkAdapter': '🌐',
        }
        
        class_names_ru = {
            'Processor': 'Процессор',
            'Memory': 'Оперативная память',
            'DisplayAdapter': 'Видеокарты',
            'Monitor': 'Мониторы',
            'Motherboard': 'Материнская плата',
            'DiskDrive': 'Локальные диски',
            'NetworkAdapter': 'Сетевые адаптеры',
        }
        
        for cls, devs in sorted(classes.items()):
            icon = class_icons.get(cls, '📦')
            display_name = class_names_ru.get(cls, cls)
            class_item = self.tree.insert(computer_item, 'end', text=f'{icon} {display_name}', values=(''))
            for dev in devs:
                name = dev.get('device_name', 'Unknown')
                manufacturer = dev.get('manufacturer', '')
                driver_version = dev.get('driver_version', '')
                status = dev.get('status', '')
                device_id = dev.get('device_id', '')
                
                # For NetworkAdapter, skip if no driver_version or no detailed info (no IP config = not expandable)
                if cls == 'NetworkAdapter':
                    if not driver_version or driver_version == 'Unknown' or driver_version == 'N/A':
                        continue
                    # Check if driver_version contains actual network details (MAC, IP, Speed, etc.)
                    # Skip if it only has basic info without expandable details
                    detailed_keywords = ['MAC:', 'IP:', 'Скорость:', 'Маска:', 'Шлюз:', 'DNS:', 'DHCP:']
                    if not any(kw in driver_version for kw in detailed_keywords):
                        continue
                
                # For NetworkAdapter, don't show details in parent row
                if cls == 'NetworkAdapter':
                    info_parts = []
                    if manufacturer and manufacturer != 'Unknown':
                        info_parts.append(f'Производитель: {manufacturer}')
                    info_text = ' | '.join(info_parts) if info_parts else ''
                    
                    device_item = self.tree.insert(class_item, 'end', 
                        text=f'  {name}',
                        values=(info_text,))
                    
                    # Create sub-items with all details (IP, DNS, DHCP, etc.)
                    if driver_version:
                        self._add_network_adapter_details(device_item, driver_version)
                else:
                    # Build info string for other device types
                    info_parts = []
                    if manufacturer and manufacturer != 'Unknown':
                        info_parts.append(f'Производитель: {manufacturer}')
                    if driver_version and driver_version != 'Unknown' and driver_version != 'N/A':
                        info_parts.append(f'Драйвер: {driver_version}')
                    if device_id and device_id != 'Unknown':
                        info_parts.append(f'ID: {device_id}')
                    
                    info_text = ' | '.join(info_parts) if info_parts else ''
                    
                    self.tree.insert(class_item, 'end', 
                        text=f'  {name}',
                        values=(info_text,))

    def _on_select(self, event):
        item = self.tree.focus()
        if item:
            # Check if it's a computer (top level)
            parent = self.tree.parent(item)
            if not parent:
                self.selected_computer_id = self._get_computer_id_from_item(item)

    def _add_network_adapter_details(self, parent_item, details_str):
        """Parse network adapter details and create sub-items"""
        # Expected format: "Скорость: 1 Gbps | IP: 192.168.1.100 | Маска: 255.255.255.0 | Шлюз: 192.168.1.1 | DNS: 8.8.8.8 | DHCP: Да | DHCP сервер: 192.168.1.1"
        parts = [p.strip() for p in details_str.split('|')]
        for part in parts:
            if ':' in part:
                key, value = part.split(':', 1)
                key = key.strip()
                value = value.strip()
                if value and value != 'N/A':
                    self.tree.insert(parent_item, 'end', text=f'    {key}', values=(value,))

    def _get_computer_id_from_item(self, item):
        hostname = self.tree.item(item, 'text').strip()
        for cid, comp in self.computers_data.items():
            if comp['hostname'] == hostname:
                return cid
        return None

    def _delete_selected(self):
        if not self.selected_computer_id:
            messagebox.showwarning("Внимание", "Выберите компьютер для удаления")
            return
        
        comp = self.computers_data.get(self.selected_computer_id)
        if not comp:
            return
        
        if messagebox.askyesno("Подтверждение", 
            f"Удалить компьютер '{comp['hostname']}' ({comp['ip_address']})?\nЭто действие необратимо."):
            self.status_var.set("Удаление...")
            threading.Thread(target=self._do_delete, args=(self.selected_computer_id,), daemon=True).start()

    def _do_delete(self, computer_id):
        try:
            self.api_client.delete_computer(computer_id)
            self.root.after(0, self._refresh_data)
            self.root.after(0, lambda: self.status_var.set("Удалено"))
        except Exception as e:
            logger.error(f"Failed to delete computer: {e}")
            self.root.after(0, lambda: self.status_var.set("Ошибка удаления"))

    def _poll_selected(self):
        if not self.selected_computer_id:
            messagebox.showwarning("Внимание", "Выберите компьютер для опроса")
            return
        
        self.status_var.set("Отправка команды опроса...")
        threading.Thread(target=self._do_poll_and_refresh, args=(self.selected_computer_id,), daemon=True).start()

    def _do_poll_and_refresh(self, computer_id):
        try:
            result = self.api_client.poll_computer(computer_id)
            self.root.after(0, lambda: self.status_var.set(f"Команда опроса отправлена, ждём ответ..."))
            # Wait for client to respond and send inventory
            time.sleep(1.5)
            # Refresh tree to show new data
            self.root.after(0, self._refresh_data)
        except Exception as e:
            logger.error(f"Failed to poll computer: {e}")
            self.root.after(0, lambda: self.status_var.set("Ошибка опроса"))

    def _poll_all(self):
        self.status_var.set("Отправка команды опроса всем...")
        threading.Thread(target=self._do_poll_all, daemon=True).start()

    def _do_poll_all(self):
        try:
            result = self.api_client.poll_all_computers()
            self.root.after(0, lambda: self.status_var.set(f"Команда опроса всем отправлена: {result.get('message', 'OK')}"))
        except Exception as e:
            logger.error(f"Failed to poll all: {e}")
            self.root.after(0, lambda: self.status_var.set("Ошибка опроса всех"))

    def _check_client_update(self, version_item):
        """Check for client agent update from GitHub releases"""
        # Get computer ID from parent item
        computer_item = self.tree.parent(version_item)
        if not computer_item:
            return
        
        computer_text = self.tree.item(computer_item, 'text').strip()
        comp_id = None
        for cid, comp in self.computers_data.items():
            if computer_text == comp['hostname']:
                comp_id = cid
                break
        
        if not comp_id:
            return
        
        current_version = self.tree.item(version_item, 'values')[0] if self.tree.item(version_item, 'values') else '?'
        self.status_var.set("Проверка обновлений...")
        
        threading.Thread(target=self._do_check_client_update, args=(comp_id, current_version, version_item), daemon=True).start()

    def _do_check_client_update(self, comp_id, current_version, version_item):
        """Background task to check for client update"""
        try:
            latest_version = self._fetch_latest_client_version()
            if not latest_version:
                self.root.after(0, lambda: self._show_update_result("Не удалось получить информацию о версии"))
                return
            
            # Compare versions
            try:
                current_ver = version.parse(current_version)
                latest_ver = version.parse(latest_version)
            except Exception:
                # Fallback to string comparison if parsing fails
                current_ver = current_version
                latest_ver = latest_version
            
            if latest_ver > current_ver:
                self.root.after(0, lambda: self._prompt_update(comp_id, current_version, latest_version, version_item))
            else:
                self.root.after(0, lambda: self._show_update_result("Обновлений нет"))
                
        except Exception as e:
            logger.error(f"Failed to check client update: {e}")
            self.root.after(0, lambda: self._show_update_result(f"Ошибка проверки: {e}"))

    def _fetch_latest_client_version(self):
        """Fetch latest client version from GitHub releases version.txt"""
        try:
            req = urllib.request.Request(GITHUB_VERSION_URL, headers={'User-Agent': 'InventoryExplorer'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8').strip()
            
            # Parse version.txt format:
            # server 1.0.1
            # client 1.0.1
            # explorer 1.0.1
            for line in content.splitlines():
                line = line.strip()
                if line.startswith('client '):
                    return line.split(' ', 1)[1].strip()
            return None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.warning("version.txt not found in GitHub releases")
            else:
                logger.error(f"HTTP error fetching version.txt: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch version.txt: {e}")
            return None

    def _prompt_update(self, comp_id, current_version, latest_version, version_item):
        """Show update confirmation dialog"""
        self.status_var.set("Готов")
        result = messagebox.askyesno(
            "Обновление агента",
            f"Доступна новая версия агент-клиента:\n"
            f"Текущая: {current_version}\n"
            f"Новая: {latest_version}\n\n"
            f"Обновить агент на этом компьютере?"
        )
        if result:
            self._start_client_update(comp_id, latest_version, version_item)
        else:
            self.status_var.set("Обновление отменено")

    def _start_client_update(self, comp_id, latest_version, version_item):
        """Start client update process (placeholder for now)"""
        # TODO: Implement actual update mechanism
        # For now just show a message
        self.status_var.set("Функция обновления в разработке...")
        messagebox.showinfo(
            "Обновление",
            f"Запуск обновления до версии {latest_version}...\n\n"
            f"(Реальная механика обновления будет реализована позже)"
        )

    def _show_update_result(self, message):
        """Show update check result"""
        self.status_var.set("Готов")
        messagebox.showinfo("Проверка обновлений", message)

    def _show_settings(self):
        SettingsWindow(self.root, self.config, self._on_config_change)

    def _on_config_change(self):
        self.api_client = APIClient(self.config)
        # Re-register push callback with new server IP/port
        self._unregister_push_callback()
        if self.push_server:
            self.push_server.stop()
        self.push_server = PushNotificationServer(self)
        self._start_push_server()
        self._refresh_data()
        # Status checker will pick up new interval on next iteration

    def _show_about(self):
        messagebox.showinfo("О программе", 
            "Инвентаризация - Обозреватель\n\n"
            "Просмотр и управление инвентаризацией компьютеров сети.\n"
            "Версия 1.0")

    def _on_close(self):
        self._status_check_running = False
        self._unregister_push_callback()
        if self.push_server:
            self.push_server.stop()
        self.root.destroy()

    def run(self):
        self._refresh_data()
        self.root.mainloop()

    def _start_status_checker(self):
        """Start background thread to periodically check server status"""
        def status_checker():
            first_check = True
            while self._status_check_running:
                if not first_check:
                    interval = self.config.get_check_interval()
                    if interval < 5:
                        interval = 5  # minimum 5 seconds
                    time.sleep(interval)
                else:
                    first_check = False
                
                if not self._status_check_running:
                    break
                if self._refreshing:
                    continue
                try:
                    online_data = self.api_client.get_online()
                    self._msg_queue.put(('server_online', online_data))
                except Exception:
                    self._msg_queue.put(('server_offline', None))
        
        threading.Thread(target=status_checker, daemon=True).start()

    def _process_queue(self):
        """Process messages from background thread in main UI thread"""
        try:
            while True:
                msg_type, data = self._msg_queue.get_nowait()
                if msg_type == 'server_online':
                    self._handle_server_online(data)
                elif msg_type == 'server_offline':
                    self._handle_server_offline()
                elif msg_type == 'refresh_data':
                    self._handle_refresh_data(data)
                elif msg_type == 'refresh_error':
                    self._handle_refresh_error()
                elif msg_type == 'refresh_done':
                    self._refreshing = False
                    self.status_var.set(f"Найдено ПК: {len(self.computers_data)}")
                elif msg_type == 'push_update':
                    self._handle_push_update()
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._process_queue)

    def _handle_push_update(self):
        """Handle push notification from server - immediately refresh data"""
        logger.info("Push notification received, refreshing data immediately")
        self.status_var.set("Получено обновление от сервера...")
        self._refresh_data()

    def _handle_server_online(self, online_data):
        self._set_server_online(online_data)
        online_count = online_data.get('online_count', 0) if online_data else 0
        current_count = len(self.computers_data)
        
        if self._was_offline:
            self._was_offline = False
            self.status_var.set("Подключение восстановлено...")
            self._refresh_data()
        elif online_count != current_count and online_count > 0:
            logger.info(f"Computer count changed: was {current_count}, now {online_count}")
            self._refresh_data()

    def _handle_server_offline(self):
        if not self._was_offline:
            self._was_offline = True
        self._set_server_offline()


class SettingsWindow:
    def __init__(self, parent, config: ExplorerConfig, on_save_callback):
        self.config = config
        self.on_save_callback = on_save_callback
        self.window = tk.Toplevel(parent)
        self.window.title("Настройки Обозревателя")
        self.window.geometry("400x330")
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.grab_set()

        self._create_widgets()
        self._center_window(parent)

    def _create_widgets(self):
        main_frame = ttk.Frame(self.window, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(1, weight=1)

        # Server IP
        ttk.Label(main_frame, text="IP адрес сервера:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        self.ip_var = tk.StringVar(value=self.config.get_server_ip())
        ttk.Entry(main_frame, textvariable=self.ip_var).grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))

        # Server Port
        ttk.Label(main_frame, text="Порт сервера (агенты):").grid(row=2, column=0, sticky=tk.W, pady=(0, 5))
        self.port_var = tk.StringVar(value=str(self.config.get_server_port()))
        ttk.Entry(main_frame, textvariable=self.port_var).grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))

        # API Port
        ttk.Label(main_frame, text="Порт API (веб):").grid(row=4, column=0, sticky=tk.W, pady=(0, 5))
        self.api_port_var = tk.StringVar(value=str(self.config.get_api_port()))
        ttk.Entry(main_frame, textvariable=self.api_port_var).grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))

        # Check Interval
        ttk.Label(main_frame, text="Интервал опроса сервера (сек):").grid(row=6, column=0, sticky=tk.W, pady=(0, 5))
        self.check_interval_var = tk.StringVar(value=str(self.config.get_check_interval()))
        ttk.Entry(main_frame, textvariable=self.check_interval_var).grid(row=7, column=0, columnspan=2, sticky=tk.EW, pady=(0, 20))

        # Spacer row
        main_frame.rowconfigure(8, weight=1)

        # Separator
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(row=9, column=0, columnspan=2, sticky=tk.EW, pady=(5, 10))

        # Buttons - bottom right, using tk.Button for custom colors
        btn_frame = tk.Frame(main_frame)
        btn_frame.grid(row=10, column=0, columnspan=2, sticky=tk.E, pady=(0, 10))

        # Cancel - red
        tk.Button(btn_frame, text="Отмена", command=self.on_cancel, 
                  bg='#e74c3c', fg='white', activebackground='#c0392b', activeforeground='white',
                  relief=tk.FLAT, padx=20, pady=8, font=('Segoe UI', 9), cursor='hand2').pack(side=tk.LEFT, padx=(0, 10))
        # Save - green
        tk.Button(btn_frame, text="Сохранить", command=self.on_save,
                  bg='#27ae60', fg='white', activebackground='#219150', activeforeground='white',
                  relief=tk.FLAT, padx=20, pady=8, font=('Segoe UI', 9), cursor='hand2').pack(side=tk.LEFT)

    def _center_window(self, parent):
        self.window.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.window.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.window.winfo_height() // 2)
        self.window.geometry(f"+{x}+{y}")

    def on_save(self):
        try:
            server_ip = self.ip_var.get().strip()
            if not server_ip:
                raise ValueError("IP адрес не может быть пустым")
            
            server_port = int(self.port_var.get())
            api_port = int(self.api_port_var.get())
            check_interval = int(self.check_interval_var.get())
            
            for port in (server_port, api_port):
                if not (1 <= port <= 65535):
                    raise ValueError("Порт должен быть 1-65535")
            
            if server_port == api_port:
                raise ValueError("Порты сервера и API должны отличаться")
            
            if check_interval < 5:
                raise ValueError("Интервал опроса не может быть меньше 5 секунд")
            
            # Check if anything actually changed
            ip_changed = server_ip != self.config.get_server_ip()
            port_changed = server_port != self.config.get_server_port()
            api_port_changed = api_port != self.config.get_api_port()
            interval_changed = check_interval != self.config.get_check_interval()
            
            something_changed = ip_changed or port_changed or api_port_changed or interval_changed
            
            self.config.set_config(server_ip, server_port, api_port, check_interval)
            
            if something_changed:
                self.on_save_callback()
            
            self.window.destroy()
        except ValueError as e:
            messagebox.showerror("Ошибка", str(e))

    def on_cancel(self):
        self.window.destroy()


if __name__ == "__main__":
    app = InventoryExplorer()
    app.run()