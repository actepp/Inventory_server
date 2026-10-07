import sys
import json
import logging
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, font

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_FILE = get_base_dir() / "explorer_config.json"
DEFAULT_SERVER_IP = "127.0.0.1"
DEFAULT_SERVER_PORT = 5001
DEFAULT_API_PORT = 5002


class ExplorerConfig:
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
        # First run - create default config
        default_config = {
            'server_ip': DEFAULT_SERVER_IP,
            'server_port': DEFAULT_SERVER_PORT,
            'api_port': DEFAULT_API_PORT
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

    def set_config(self, server_ip: str, server_port: int, api_port: int):
        self.config['server_ip'] = server_ip
        self.config['server_port'] = server_port
        self.config['api_port'] = api_port
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
        self.auto_refresh = True
        self.refresh_thread = None
        
        self._setup_styles()
        self._create_ui()
        self._create_menu()
        self._start_auto_refresh()
        
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

        ttk.Button(toolbar, text="🔄 Обновить", command=self._refresh_data).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(toolbar, text="🗑 Удалить ПК", command=self._delete_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="⚙ Настройки", command=self._show_settings).pack(side=tk.LEFT, padx=5)

        # Online status
        self.online_var = tk.StringVar(value="Онлайн: 0 / 0")
        ttk.Label(toolbar, textvariable=self.online_var, font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT, padx=20)

        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side=tk.RIGHT)

        # Tree view
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ('status', 'info')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='tree headings')
        
        self.tree.heading('#0', text='Компьютер', anchor=tk.W)
        self.tree.heading('status', text='Статус', anchor=tk.CENTER)
        self.tree.heading('info', text='Информация', anchor=tk.W)

        self.tree.column('#0', width=300, minwidth=250)
        self.tree.column('status', width=100, minwidth=80, anchor=tk.CENTER)
        self.tree.column('info', width=500, minwidth=300)

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
        self.context_menu.add_command(label="Удалить", command=self._delete_selected)
        self.context_menu.add_command(label="Обновить", command=self._refresh_data)
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

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_checkbutton(label="Автообновление", variable=tk.BooleanVar(value=self.auto_refresh),
                                  command=self._toggle_auto_refresh)
        menubar.add_cascade(label="Вид", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе", command=self._show_about)
        menubar.add_cascade(label="Справка", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind('<F5>', lambda e: self._refresh_data())

    def _show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def _start_auto_refresh(self):
        self.auto_refresh = True
        if self.refresh_thread and self.refresh_thread.is_alive():
            return
        self.refresh_thread = threading.Thread(target=self._auto_refresh_loop, daemon=True)
        self.refresh_thread.start()

    def _auto_refresh_loop(self):
        while self.auto_refresh:
            time.sleep(30)  # Refresh every 30 seconds
            if self.auto_refresh:
                self.root.after(0, self._refresh_data)

    def _toggle_auto_refresh(self):
        self.auto_refresh = not self.auto_refresh
        if self.auto_refresh:
            self._start_auto_refresh()

    def _refresh_data(self):
        self.status_var.set("Загрузка...")
        threading.Thread(target=self._fetch_computers, daemon=True).start()

    def _fetch_computers(self):
        try:
            computers = self.api_client.get_computers()
            online_data = self.api_client.get_online()
            self.root.after(0, lambda c=computers, o=online_data: self._update_tree(c, o))
        except Exception as e:
            logger.error(f"Failed to fetch computers: {e}")
            self.root.after(0, lambda err=e: self._show_error(f"Ошибка загрузки: {err}"))

    def _update_tree(self, computers, online_data=None):
        self.tree.delete(*self.tree.get_children())
        self.computers_data = {}
        
        online_computers = set()
        if online_data and 'computers' in online_data:
            online_computers = {c['id'] for c in online_data['computers']}
        
        online_count = len(online_computers)
        total_count = len(computers)
        self.online_var.set(f"Онлайн: {online_count} / {total_count}")

        for comp in computers:
            comp_id = comp['id']
            self.computers_data[comp_id] = comp
            
            is_online = comp_id in online_computers
            status_text = "🟢 Онлайн" if is_online else "🔴 Офлайн"
            
            item_id = self.tree.insert('', 'end', 
                text=f"  {comp['hostname']}",
                values=(status_text, ''),
                open=False)
            
            # Add system info as child nodes
            self._add_system_info_nodes(item_id, comp)
            
            # Add placeholder for devices
            self.tree.insert(item_id, 'end', text='📁 Устройства (загрузка...)', values=('', ''))
        
        self.status_var.set(f"Найдено ПК: {len(computers)}")

    def _add_system_info_nodes(self, parent_item, comp):
        """Add system info as child nodes under computer"""
        # IP Address
        ip = comp.get('ip_address', 'Unknown')
        self.tree.insert(parent_item, 'end', text='🌐 IP адрес', values=('', ip))
        
        # OS
        os_name = comp.get('os_name', 'Unknown')
        os_version = comp.get('os_version', '')
        os_info = f'{os_name} {os_version}'.strip()
        self.tree.insert(parent_item, 'end', text='🖥 Операционная система', values=('', os_info))
        
        # CPU
        cpu = comp.get('cpu_info', 'Unknown')
        self.tree.insert(parent_item, 'end', text='🔧 Процессор', values=('', cpu))
        
        # RAM
        ram = comp.get('ram_total_gb', 0)
        ram_text = f'{ram:.1f} GB' if ram else 'Unknown'
        self.tree.insert(parent_item, 'end', text='💾 Оперативная память', values=('', ram_text))
        
        # Last seen
        last_seen = comp.get('last_seen', '')
        if last_seen:
            try:
                dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                last_seen = dt.strftime('%d.%m.%Y %H:%M')
            except:
                pass
        self.tree.insert(parent_item, 'end', text='🕐 Последний раз', values=('', last_seen or 'Unknown'))
        
        # MAC Address
        mac = comp.get('mac_address', 'Unknown')
        self.tree.insert(parent_item, 'end', text='🔗 MAC адрес', values=('', mac))

    def _on_tree_open(self, event):
        item = self.tree.focus()
        if not item:
            return
        
        # Check if this is a computer node (has children with placeholder)
        children = self.tree.get_children(item)
        if children and self.tree.item(children[0], 'text') == 'Загрузка...':
            self.tree.delete(children[0])
            self._load_devices(item)

    def _load_devices(self, computer_item):
        comp_id = None
        for cid, comp in self.computers_data.items():
            if self.tree.item(computer_item, 'text').strip() == comp['hostname']:
                comp_id = cid
                break
        
        if not comp_id:
            return
        
        try:
            devices = self.api_client.get_devices(comp_id)
            self._populate_devices(computer_item, devices)
        except Exception as e:
            logger.error(f"Failed to load devices: {e}")
            self.tree.insert(computer_item, 'end', text=f'Ошибка: {e}', values=('', ''))

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
        
        # Class icons mapping
        class_icons = {
            'Processor': '🔧',
            'Memory': '💾',
            'DisplayAdapter': '🎮',
            'Monitor': '🖥',
            'Motherboard': '📋',
            'DiskDrive': '💿',
            'NetworkAdapter': '🌐',
        }
        
        for cls, devs in sorted(classes.items()):
            icon = class_icons.get(cls, '📦')
            class_item = self.tree.insert(computer_item, 'end', text=f'{icon} {cls}', values=('', ''))
            for dev in devs:
                name = dev.get('device_name', 'Unknown')
                manufacturer = dev.get('manufacturer', '')
                driver_version = dev.get('driver_version', '')
                status = dev.get('status', '')
                device_id = dev.get('device_id', '')
                
                # Build info string
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
                    values=(status, info_text))

    def _on_select(self, event):
        item = self.tree.focus()
        if item:
            # Check if it's a computer (top level)
            parent = self.tree.parent(item)
            if not parent:
                self.selected_computer_id = self._get_computer_id_from_item(item)

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
            self.root.after(0, lambda err=e: self._show_error(f"Ошибка удаления: {err}"))

    def _show_settings(self):
        SettingsWindow(self.root, self.config, self._on_config_change)

    def _on_config_change(self):
        self.api_client = APIClient(self.config)
        self._refresh_data()

    def _show_error(self, msg):
        self.status_var.set("Ошибка")
        messagebox.showerror("Ошибка", msg)

    def _show_about(self):
        messagebox.showinfo("О программе", 
            "Инвентаризация - Обозреватель\n\n"
            "Просмотр и управление инвентаризацией компьютеров сети.\n"
            "Версия 1.0")

    def _on_close(self):
        self.auto_refresh = False
        self.root.destroy()

    def run(self):
        self._refresh_data()
        self.root.mainloop()


class SettingsWindow:
    def __init__(self, parent, config: ExplorerConfig, on_save_callback):
        self.config = config
        self.on_save_callback = on_save_callback
        self.window = tk.Toplevel(parent)
        self.window.title("Настройки Обозревателя")
        self.window.geometry("400x250")
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.grab_set()

        self._create_widgets()
        self._center_window(parent)

    def _create_widgets(self):
        main_frame = ttk.Frame(self.window, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Server IP
        ttk.Label(main_frame, text="IP адрес сервера:").pack(anchor=tk.W, pady=(0, 5))
        self.ip_var = tk.StringVar(value=self.config.get_server_ip())
        ttk.Entry(main_frame, textvariable=self.ip_var, width=30).pack(fill=tk.X, pady=(0, 10))

        # Server Port
        ttk.Label(main_frame, text="Порт сервера (агенты):").pack(anchor=tk.W, pady=(0, 5))
        self.port_var = tk.StringVar(value=str(self.config.get_server_port()))
        ttk.Entry(main_frame, textvariable=self.port_var, width=30).pack(fill=tk.X, pady=(0, 10))

        # API Port
        ttk.Label(main_frame, text="Порт API (веб):").pack(anchor=tk.W, pady=(0, 5))
        self.api_port_var = tk.StringVar(value=str(self.config.get_api_port()))
        ttk.Entry(main_frame, textvariable=self.api_port_var, width=30).pack(fill=tk.X, pady=(0, 15))

        ttk.Frame(main_frame).pack(fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, anchor=tk.E)

        ttk.Button(btn_frame, text="Сохранить", command=self.on_save).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Отмена", command=self.on_cancel).pack(side=tk.RIGHT)

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
            
            for port in (server_port, api_port):
                if not (1 <= port <= 65535):
                    raise ValueError("Порт должен быть 1-65535")
            
            if server_port == api_port:
                raise ValueError("Порты сервера и API должны отличаться")
            
            self.config.set_config(server_ip, server_port, api_port)
            self.on_save_callback()
            self.window.destroy()
        except ValueError as e:
            messagebox.showerror("Ошибка", str(e))

    def on_cancel(self):
        self.window.destroy()


if __name__ == "__main__":
    app = InventoryExplorer()
    app.run()