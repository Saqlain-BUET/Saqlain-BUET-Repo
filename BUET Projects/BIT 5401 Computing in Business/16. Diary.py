import ctypes
import json
import os
import platform
import pprint
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

if os.name == "nt":
    import winreg


IS_FROZEN = getattr(sys, "frozen", False)
APP_PATH = os.path.abspath(sys.executable if IS_FROZEN else __file__)
STORAGE_PATH = os.path.splitext(APP_PATH)[0] + "_data.json" if IS_FROZEN else APP_PATH
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_WIN_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_MODEL_SIZE_LABEL = "~2.0 GB"

START_MARKER = "# " + "=== DIARY DATA START ==="
END_MARKER = "# " + "=== DIARY DATA END ==="


def _init_console():
    """Enables UTF-8 output and ANSI virtual terminal processing on Windows 10/11 & EXEs."""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
                return True
        except Exception:
            return False
    return sys.stdout.isatty()


ANSI_ENABLED = _init_console()


def _c(text, code):
    """Wraps text in ANSI escape codes when supported by the console."""
    if not ANSI_ENABLED:
        return text
    return f"\033[{code}m{text}\033[0m"


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def load_data():
    """Loads saved data from the companion JSON file when running as a compiled .exe."""
    if IS_FROZEN and os.path.isfile(STORAGE_PATH):
        try:
            with open(STORAGE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            DIARY_CONFIG.update(data.get("config", {}))
            DIARY_REMINDERS[:] = data.get("reminders", [])
            DIARY_ENTRIES[:] = data.get("entries", [])
            DIARY_CHAT_HISTORY[:] = data.get("chat_history", [])
        except (OSError, json.JSONDecodeError) as err:
            print(f"[!] Warning: Could not read {os.path.basename(STORAGE_PATH)}: {err}")


def save_data(entries, config=None, reminders=None, chat_history=None):
    """Safely writes data next to the .exe when frozen, or inside the .py file when run as a script."""
    cfg = config if config is not None else DIARY_CONFIG
    rems = reminders if reminders is not None else DIARY_REMINDERS
    chats = chat_history if chat_history is not None else DIARY_CHAT_HISTORY

    if IS_FROZEN:
        payload = {
            "config": cfg,
            "reminders": rems,
            "entries": entries,
            "chat_history": chats,
        }
        target_dir = os.path.dirname(STORAGE_PATH)
        fd, tmp_save = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_save, STORAGE_PATH)
        except Exception:
            if os.path.exists(tmp_save):
                os.remove(tmp_save)
            raise
        return

    with open(APP_PATH, "r", encoding="utf-8") as f:
        source = f.read()

    start_token = f"\n{START_MARKER}\n"
    end_token = f"\n{END_MARKER}\n"

    start_idx = source.find(start_token)
    end_idx = source.find(end_token, start_idx if start_idx != -1 else 0)

    if start_idx == -1 or end_idx == -1:
        raise RuntimeError("Diary data markers at the bottom of the script were not found.")

    formatted_cfg = pprint.pformat(cfg, indent=4, width=100)
    formatted_rems = pprint.pformat(rems, indent=4, width=100)
    formatted_entries = pprint.pformat(entries, indent=4, width=100)
    formatted_chats = pprint.pformat(chats, indent=4, width=100)
    updated_block = (
        f"\n{START_MARKER}\n"
        f"DIARY_CONFIG = {formatted_cfg}\n"
        f"DIARY_REMINDERS = {formatted_rems}\n"
        f"DIARY_ENTRIES = {formatted_entries}\n"
        f"DIARY_CHAT_HISTORY = {formatted_chats}\n"
        f"{END_MARKER}\n"
    )
    updated_source = source[:start_idx] + updated_block + source[end_idx + len(end_token):]

    # Verify valid Python syntax before atomically replacing the file
    compile(updated_source, APP_PATH, "exec")

    script_dir = os.path.dirname(APP_PATH)
    fd, tmp_save = tempfile.mkstemp(dir=script_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(updated_source)
        os.replace(tmp_save, APP_PATH)
    except Exception:
        if os.path.exists(tmp_save):
            os.remove(tmp_save)
        raise


def read_multiline_input(prompt="Write your text (type 'END' on a new line to finish):"):
    print(f"\n{prompt}")
    print("-" * 55)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


# --- On-Demand System Profiler, Program Scanner, Launcher & Process Manager ---
# NOTE: Both caches remain empty at startup and are ONLY populated when explicitly requested.

SYSTEM_PROFILE = {}
INSTALLED_PROGRAMS_CACHE = []

PROTECTED_SYSTEM_PROCESSES = {
    "system",
    "idle",
    "system idle process",
    "csrss.exe",
    "wininit.exe",
    "lsass.exe",
    "services.exe",
    "smss.exe",
    "svchost.exe",
    "winlogon.exe",
    "explorer.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "conhost.exe",
    "registry",
    "memory compression",
}


def is_user_admin():
    """Returns True if the current process has Administrator / root privileges."""
    try:
        if os.name == "nt":
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def request_admin_elevation_and_restart(entries, pending_query=""):
    """
    Saves current state + pending AI question, requests Windows UAC Administrator elevation,
    and relaunches this app as Administrator.
    """
    if os.name != "nt":
        print("\n[!] Automatic elevation is only supported on Windows. Please restart with 'sudo'.")
        return False

    DIARY_CONFIG["pending_admin_query"] = pending_query
    save_data(entries, config=DIARY_CONFIG)

    try:
        if IS_FROZEN:
            exe_target = APP_PATH
            params = ""
        else:
            exe_target = sys.executable
            params = f'"{APP_PATH}"'

        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            exe_target,
            params,
            os.path.dirname(APP_PATH),
            1,
        )
        if rc > 32:
            print(_c("\n[+] Administrator permission granted. Switching to elevated window...", "1;92"))
            time.sleep(0.8)
            sys.exit(0)
        else:
            DIARY_CONFIG.pop("pending_admin_query", None)
            save_data(entries, config=DIARY_CONFIG)
            print(_c("\n[!] Administrator elevation was cancelled or declined.", "1;93"))
            return False
    except Exception as err:
        DIARY_CONFIG.pop("pending_admin_query", None)
        save_data(entries, config=DIARY_CONFIG)
        print(f"\n[!] Could not elevate privileges: {err}")
        return False


def _get_windows_os_details():
    if os.name != "nt":
        return f"{platform.system()} {platform.release()} ({platform.machine()})"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            prod, _ = winreg.QueryValueEx(key, "ProductName")
            build_str, _ = winreg.QueryValueEx(key, "CurrentBuild")
            try:
                disp, _ = winreg.QueryValueEx(key, "DisplayVersion")
            except OSError:
                disp = ""
            if build_str.isdigit() and int(build_str) >= 22000 and "Windows 10" in prod:
                prod = prod.replace("Windows 10", "Windows 11")
            ver_tag = f" {disp}" if disp else ""
            return f"{prod}{ver_tag} (Build {build_str}, {platform.machine()})"
    except OSError:
        return f"{platform.system()} {platform.release()} ({platform.version()})"


def _get_cpu_details():
    cores = os.cpu_count() or 1
    cpu_name = platform.processor() or "Unknown CPU"
    if os.name == "nt":
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                reg_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                if isinstance(reg_name, str) and reg_name.strip():
                    cpu_name = reg_name.strip()
        except OSError:
            pass
    return f"{cpu_name} ({cores} logical processors)"


def _get_ram_details():
    if os.name == "nt":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                tot_gb = stat.ullTotalPhys / (1024 ** 3)
                avail_gb = stat.ullAvailPhys / (1024 ** 3)
                return f"{tot_gb:.2f} GB Total ({avail_gb:.2f} GB Available, {stat.dwMemoryLoad}% in use)"
        except Exception:
            pass
    return "Unknown RAM"


def _get_gpu_details():
    gpus = []
    if os.name == "nt":
        class_guid = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_guid) as base_key:
                idx = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(base_key, idx)
                        idx += 1
                        if not sub_name.isdigit():
                            continue
                        with winreg.OpenKey(base_key, sub_name) as gpu_key:
                            desc, _ = winreg.QueryValueEx(gpu_key, "DriverDesc")
                            vram_str = ""
                            try:
                                qw_mem, _ = winreg.QueryValueEx(gpu_key, "HardwareInformation.qwMemorySize")
                                if isinstance(qw_mem, int) and qw_mem > 0:
                                    vram_str = f" ({qw_mem / (1024 ** 3):.1f} GB VRAM)"
                            except OSError:
                                try:
                                    mem_bytes, _ = winreg.QueryValueEx(gpu_key, "HardwareInformation.MemorySize")
                                    if isinstance(mem_bytes, int) and mem_bytes > 0:
                                        vram_str = f" ({mem_bytes / (1024 ** 3):.1f} GB VRAM)"
                                except OSError:
                                    pass
                            if desc and desc not in gpus:
                                gpus.append(f"{desc}{vram_str}")
                    except OSError:
                        break
        except OSError:
            pass
    return "; ".join(gpus) if gpus else "Standard / Integrated Graphics"


def _get_storage_details():
    drives = []
    if os.name == "nt":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter_idx in range(26):
            if bitmask & (1 << letter_idx):
                drive = f"{chr(65 + letter_idx)}:\\"
                try:
                    if ctypes.windll.kernel32.GetDriveTypeW(drive) == 3:
                        usage = shutil.disk_usage(drive)
                        tot_gb = usage.total / (1024 ** 3)
                        free_gb = usage.free / (1024 ** 3)
                        drives.append(f"{drive[:2]} {free_gb:.1f} GB free / {tot_gb:.1f} GB")
                except OSError:
                    pass
    else:
        try:
            usage = shutil.disk_usage("/")
            drives.append(f"/ {usage.free / (1024 ** 3):.1f} GB free / {usage.total / (1024 ** 3):.1f} GB")
        except OSError:
            pass
    return " | ".join(drives) if drives else "Unknown Storage"


def _get_ollama_runtime_status():
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            running_models = data.get("models", [])
            if not running_models:
                return "Ollama Server Active (no model loaded in VRAM/RAM yet)"
            parts = []
            for m in running_models:
                name = m.get("name", "model")
                size_vram = m.get("size_vram", 0)
                size_tot = m.get("size", 1) or 1
                gpu_pct = int(round((size_vram / size_tot) * 100))
                parts.append(f"{name} ({gpu_pct}% GPU / {100 - gpu_pct}% CPU)")
            return "Active: " + ", ".join(parts)
    except Exception:
        return "Ollama Server Standby"


def collect_system_info():
    """Collects live PC hardware & OS configuration ONLY when called on demand."""
    admin_flag = is_user_admin()
    runtime_kind = "Compiled Windows Executable (.exe)" if IS_FROZEN else f"Python {platform.python_version()} Script (.py)"
    return {
        "acquired_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        "computer_name": platform.node() or os.environ.get("COMPUTERNAME", "Unknown PC"),
        "username": os.environ.get("USERNAME") or os.environ.get("USER") or "Unknown User",
        "privilege_level": "Administrator (Elevated)" if admin_flag else "Standard User (Non-Admin)",
        "is_admin": admin_flag,
        "os": _get_windows_os_details(),
        "cpu": _get_cpu_details(),
        "ram": _get_ram_details(),
        "gpu": _get_gpu_details(),
        "storage": _get_storage_details(),
        "runtime": runtime_kind,
        "ollama_accel": _get_ollama_runtime_status(),
    }


def _run_powershell_query(ps_script, timeout=10):
    if os.name != "nt":
        return False, "PowerShell diagnostics are only available on Windows."
    kwargs = {"capture_output": True, "text": True, "timeout": timeout}
    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            **kwargs,
        )
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if res.returncode != 0 and not out:
            return False, err or f"Exit code {res.returncode}"
        if "access is denied" in err.lower() or "unauthorizedaccessexception" in err.lower():
            return False, "ACCESS_DENIED"
        return True, out or "(No output returned)"
    except Exception as e:
        return False, str(e)


def acquire_system_info_on_demand(topic, requires_admin, entries, user_query=""):
    """
    Acquires system hardware & OS details ONLY when requested by the user.
    Handles Windows UAC Administrator elevation if restricted diagnostics are requested.
    """
    topic_key = (topic or "overview").strip().lower()
    admin_active = is_user_admin()

    if requires_admin and not admin_active:
        print(_c("\n  [!] RESTRICTED SYSTEM / HARDWARE ACCESS REQUESTED", "1;93"))
        print(f"      Topic          : {topic_key}")
        print("      Current Access : Standard User (Administrator rights needed)")
        if _ask_yes_no("      Should I request Windows Administrator permission now? (Y/N): "):
            request_admin_elevation_and_restart(entries, pending_query=user_query)
            return {"status": "elevation_cancelled"}
        return {
            "status": "permission_denied_by_user",
            "message": "User declined Administrator permission.",
        }

    SYSTEM_PROFILE.clear()
    SYSTEM_PROFILE.update(collect_system_info())
    print(_c(f"  [+] SYSTEM INFO ACQUIRED ({SYSTEM_PROFILE['privilege_level']})", "1;92"))

    if os.name != "nt" or topic_key == "overview":
        return {"status": "acquired", "system_info": SYSTEM_PROFILE}

    ps_map = {
        "motherboard_bios": (
            "Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product, SerialNumber | Format-List; "
            "Get-CimInstance Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, ReleaseDate, SerialNumber | Format-List"
        ),
        "cpu_memory": (
            "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, L2CacheSize, L3CacheSize | Format-List; "
            "Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator, Manufacturer, Capacity, Speed, MemoryType | Format-Table -AutoSize"
        ),
        "gpu_display": (
            "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, VideoProcessor, AdapterRAM, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate | Format-List"
        ),
        "storage_health": (
            "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, BusType, HealthStatus, OperationalStatus, Size | Format-Table -AutoSize; "
            "Get-Volume | Where-Object {$_.DriveLetter} | Select-Object DriveLetter, FileSystemLabel, FileSystem, HealthStatus, SizeRemaining, Size | Format-Table -AutoSize"
        ),
        "network": (
            "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object Name, InterfaceDescription, LinkSpeed, MacAddress | Format-Table -AutoSize; "
            "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike '*Loopback*'} | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize"
        ),
        "battery_power": (
            "Get-CimInstance Win32_Battery | Select-Object Name, Status, EstimatedChargeRemaining, EstimatedRunTime, DesignCapacity, FullChargeCapacity | Format-List"
        ),
        "processes": (
            "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 20 ProcessName, Id, @{N='RAM_MB';E={[math]::Round($_.WorkingSet64/1MB,1)}}, CPU | Format-Table -AutoSize"
        ),
    }

    if topic_key in ps_map:
        ok, output = _run_powershell_query(ps_map[topic_key])
        if not ok and output == "ACCESS_DENIED" and not admin_active:
            print(_c("\n  [!] Windows blocked standard access to this hardware detail.", "1;93"))
            if _ask_yes_no("      Grant Administrator permission via Windows UAC to read it? (Y/N): "):
                request_admin_elevation_and_restart(entries, pending_query=user_query)
            return {
                "status": "permission_denied",
                "message": "Administrator permission was not granted.",
                "system_info": SYSTEM_PROFILE,
            }
        return {
            "status": "acquired" if ok else "partial",
            "system_info": SYSTEM_PROFILE,
            "detailed_diagnostics": output,
        }

    return {"status": "acquired", "system_info": SYSTEM_PROFILE}


# --- Real Installed Program Scanner (Registry + Start Menu + App Paths) ---

UNINSTALLER_KEYWORDS = ("unins", "uninstall", "setup", "install", "update", "updater", "msiexec", "helper", "crash")


def _clean_exe_candidate(raw_val):
    if not raw_val or not isinstance(raw_val, str):
        return ""
    expanded = os.path.expandvars(raw_val.strip())
    if "," in expanded and not expanded.startswith('"'):
        expanded = expanded.split(",", 1)[0].strip()
    if expanded.startswith('"'):
        end_q = expanded.find('"', 1)
        if end_q != -1:
            expanded = expanded[1:end_q]
    else:
        m = re.match(r'^([^\s"]+\.exe)\b', expanded, flags=re.IGNORECASE)
        if m:
            expanded = m.group(1)

    expanded = expanded.strip('"').strip()
    if not expanded.lower().endswith(".exe") or not os.path.isfile(expanded):
        return ""
    base = os.path.basename(expanded).lower()
    if any(bad in base for bad in UNINSTALLER_KEYWORDS):
        return ""
    return os.path.abspath(expanded)


def _find_best_exe_in_folder(folder_path, app_name=""):
    if not folder_path or not os.path.isdir(folder_path):
        return ""
    tokens = [t for t in re.findall(r"[a-z0-9]+", app_name.lower()) if len(t) >= 3]
    best_exe = ""
    best_score = -1

    for sub in ("", "bin", "app", "Application", r"Common7\IDE"):
        check_dir = os.path.join(folder_path, sub) if sub else folder_path
        if not os.path.isdir(check_dir):
            continue
        try:
            for entry in os.scandir(check_dir):
                if not entry.is_file() or not entry.name.lower().endswith(".exe"):
                    continue
                low = entry.name.lower()
                if any(bad in low for bad in UNINSTALLER_KEYWORDS):
                    continue
                stem = os.path.splitext(low)[0]
                score = sum(3 for tok in tokens if tok in stem)
                if sub == "":
                    score += 1
                if score > best_score:
                    best_score = score
                    best_exe = entry.path
        except OSError:
            pass
    return os.path.abspath(best_exe) if best_exe else ""


def collect_installed_programs_list():
    """
    Scans the actual Windows machine (Uninstall Registry, App Paths, Start Menu shortcuts)
    to build an accurate, detailed list of installed programs and their executable locations.
    """
    programs_by_key = {}

    def upsert_program(name, version="", publisher="", install_location="", exe_path=""):
        clean_name = (name or "").strip()
        if not clean_name:
            return
        if any(skip in clean_name.lower() for skip in ("uninstall ", "remove ", "repair ", "readme", "website", "help")):
            return

        norm_key = re.sub(r"\s+", " ", clean_name).lower()
        clean_loc = install_location.strip('"').strip() if install_location else ""
        if clean_loc and not os.path.isdir(clean_loc):
            clean_loc = ""
        clean_exe = _clean_exe_candidate(exe_path)
        if not clean_exe and clean_loc:
            clean_exe = _find_best_exe_in_folder(clean_loc, clean_name)
        if clean_exe and not clean_loc:
            clean_loc = os.path.dirname(clean_exe)

        existing = programs_by_key.get(norm_key)
        if not existing:
            programs_by_key[norm_key] = {
                "name": clean_name,
                "version": (version or "").strip(),
                "publisher": (publisher or "").strip(),
                "install_location": clean_loc,
                "exe_path": clean_exe,
            }
        else:
            if not existing["version"] and version:
                existing["version"] = version.strip()
            if not existing["publisher"] and publisher:
                existing["publisher"] = publisher.strip()
            if not existing["install_location"] and clean_loc:
                existing["install_location"] = clean_loc
            if not existing["exe_path"] and clean_exe:
                existing["exe_path"] = clean_exe

    if os.name == "nt":
        uninstall_targets = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, reg_path in uninstall_targets:
            try:
                with winreg.OpenKey(hive, reg_path) as ukey:
                    idx = 0
                    while True:
                        try:
                            sub_name = winreg.EnumKey(ukey, idx)
                            idx += 1
                            with winreg.OpenKey(ukey, sub_name) as app_key:
                                try:
                                    sys_comp, _ = winreg.QueryValueEx(app_key, "SystemComponent")
                                    if sys_comp == 1:
                                        continue
                                except OSError:
                                    pass
                                try:
                                    winreg.QueryValueEx(app_key, "ParentKeyName")
                                    continue
                                except OSError:
                                    pass

                                try:
                                    disp_name, _ = winreg.QueryValueEx(app_key, "DisplayName")
                                except OSError:
                                    continue
                                if not isinstance(disp_name, str) or not disp_name.strip():
                                    continue

                                version = ""
                                publisher = ""
                                inst_loc = ""
                                icon_exe = ""
                                try:
                                    version, _ = winreg.QueryValueEx(app_key, "DisplayVersion")
                                except OSError:
                                    pass
                                try:
                                    publisher, _ = winreg.QueryValueEx(app_key, "Publisher")
                                except OSError:
                                    pass
                                try:
                                    inst_loc, _ = winreg.QueryValueEx(app_key, "InstallLocation")
                                except OSError:
                                    pass
                                try:
                                    icon_val, _ = winreg.QueryValueEx(app_key, "DisplayIcon")
                                    icon_exe = _clean_exe_candidate(icon_val)
                                except OSError:
                                    pass

                                upsert_program(
                                    name=disp_name,
                                    version=str(version),
                                    publisher=str(publisher),
                                    install_location=str(inst_loc),
                                    exe_path=icon_exe,
                                )
                        except OSError:
                            break
            except OSError:
                pass

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            app_paths_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
            try:
                with winreg.OpenKey(hive, app_paths_key) as ap_key:
                    idx = 0
                    while True:
                        try:
                            exe_sub = winreg.EnumKey(ap_key, idx)
                            idx += 1
                            with winreg.OpenKey(ap_key, exe_sub) as item_key:
                                val, _ = winreg.QueryValueEx(item_key, None)
                                exe_full = _clean_exe_candidate(str(val))
                                if exe_full:
                                    stem_title = os.path.splitext(os.path.basename(exe_full))[0]
                                    matched_existing = False
                                    for rec in programs_by_key.values():
                                        if rec["install_location"] and os.path.dirname(exe_full).lower().startswith(
                                            rec["install_location"].lower()
                                        ):
                                            if not rec["exe_path"]:
                                                rec["exe_path"] = exe_full
                                            matched_existing = True
                                    if not matched_existing:
                                        upsert_program(name=stem_title, exe_path=exe_full)
                        except OSError:
                            break
            except OSError:
                pass

        sm_dirs = [
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
        ]
        for sm_dir in sm_dirs:
            if not sm_dir or not os.path.isdir(sm_dir):
                continue
            for root_dir, _, files in os.walk(sm_dir):
                for fname in files:
                    if not fname.lower().endswith(".lnk"):
                        continue
                    lnk_full = os.path.join(root_dir, fname)
                    shortcut_title = os.path.splitext(fname)[0]
                    for target in _extract_exe_targets_from_lnk(lnk_full):
                        clean_exe = _clean_exe_candidate(target)
                        if clean_exe:
                            upsert_program(name=shortcut_title, exe_path=clean_exe)
                            break

        win_dir = os.environ.get("WINDIR", r"C:\Windows")
        sys32 = os.path.join(win_dir, "System32")
        for builtin_name, builtin_exe in [
            ("Notepad", os.path.join(sys32, "notepad.exe")),
            ("Calculator", os.path.join(sys32, "calc.exe")),
            ("Paint", os.path.join(sys32, "mspaint.exe")),
            ("Command Prompt", os.path.join(sys32, "cmd.exe")),
            ("Windows PowerShell", os.path.join(sys32, "WindowsPowerShell", "v1.0", "powershell.exe")),
            ("Task Manager", os.path.join(sys32, "Taskmgr.exe")),
        ]:
            if os.path.isfile(builtin_exe):
                upsert_program(name=builtin_name, publisher="Microsoft Corporation", exe_path=builtin_exe)
    else:
        for search_dir in ("/usr/bin", "/usr/local/bin", "/Applications"):
            if os.path.isdir(search_dir):
                try:
                    for entry in os.scandir(search_dir):
                        upsert_program(name=entry.name, exe_path=entry.path)
                except OSError:
                    pass

    return sorted(programs_by_key.values(), key=lambda p: p["name"].lower())


def scan_installed_programs_on_demand(filter_keyword=""):
    """
    Builds the detailed list of installed programs ONLY when requested,
    prints the complete verified list to the console, and caches it for launching.
    """
    print(_c("\n  [i] Scanning Windows Registry, App Paths & Start Menu for installed programs...", "96"))
    full_list = collect_installed_programs_list()
    INSTALLED_PROGRAMS_CACHE[:] = full_list

    kw = (filter_keyword or "").strip().lower()
    display_list = [
        p for p in full_list
        if not kw or kw in f"{p['name']} {p['publisher']} {p['exe_path']} {p['install_location']}".lower()
    ]

    print(_c(f"\n  [+] REAL INSTALLED PROGRAMS FOUND ({len(display_list)} shown / {len(full_list)} total):", "1;92"))
    print("  " + "-" * 78)
    for idx, prog in enumerate(display_list, start=1):
        ver = f" v{prog['version']}" if prog["version"] else ""
        loc = prog["exe_path"] or prog["install_location"] or "(Location not registered)"
        print(f"  {idx:>3}. {prog['name']}{ver}")
        print(f"       Location: {loc}")
    print("  " + "-" * 78)

    ai_items = [
        {
            "name": p["name"],
            "version": p["version"],
            "publisher": p["publisher"],
            "location": p["exe_path"] or p["install_location"] or "Not specified",
        }
        for p in display_list[:120]
    ]
    return {
        "status": "scanned",
        "total_installed": len(full_list),
        "shown_count": len(ai_items),
        "note": "Complete list was printed directly to the user's terminal. Only mention programs from this exact list.",
        "programs": ai_items,
    }


def launch_installed_program(program_name):
    """Finds a real installed program's .exe path and launches it."""
    target = (program_name or "").strip()
    if not target:
        return {"status": "error", "message": "No program name provided."}

    if not INSTALLED_PROGRAMS_CACHE:
        INSTALLED_PROGRAMS_CACHE[:] = collect_installed_programs_list()

    q = target.lower()
    exact_matches = [
        p for p in INSTALLED_PROGRAMS_CACHE
        if p["name"].lower() == q or os.path.splitext(os.path.basename(p["exe_path"]))[0].lower() == q
    ]
    partial_matches = [
        p for p in INSTALLED_PROGRAMS_CACHE
        if q in p["name"].lower() or (p["exe_path"] and q in os.path.basename(p["exe_path"]).lower())
    ]

    candidates = exact_matches or partial_matches
    launchable = [p for p in candidates if p.get("exe_path") and os.path.isfile(p["exe_path"])]

    if not launchable:
        which_exe = shutil.which(target) or shutil.which(f"{target}.exe")
        if which_exe and os.path.isfile(which_exe):
            launchable = [{"name": target, "exe_path": os.path.abspath(which_exe)}]

    if not launchable:
        if candidates:
            return {
                "status": "no_executable_found",
                "message": f"'{candidates[0]['name']}' is listed in Registry, but has no launchable .exe path.",
            }
        return {
            "status": "not_installed",
            "message": f"'{target}' was not found among the installed programs on this PC.",
        }

    if not exact_matches and len({p["exe_path"].lower() for p in launchable}) > 1:
        options = [{"name": p["name"], "exe_path": p["exe_path"]} for p in launchable[:8]]
        return {
            "status": "multiple_matches",
            "message": f"Multiple installed programs matched '{target}'. Ask the user which one to open.",
            "matches": options,
        }

    chosen = launchable[0]
    exe_path = chosen["exe_path"]
    try:
        if os.name == "nt":
            os.startfile(exe_path)
        else:
            subprocess.Popen([exe_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(_c(f"\n  [+] LAUNCHED PROGRAM: {chosen['name']} -> {exe_path}", "1;92"))
        return {
            "status": "launched",
            "program": chosen["name"],
            "exe_path": exe_path,
        }
    except Exception as err:
        return {
            "status": "launch_failed",
            "program": chosen["name"],
            "exe_path": exe_path,
            "error": str(err),
        }


def terminate_running_process(process_name="", pid=None, entries=None, user_query=""):
    """Ends a running process by name or PID upon user command, requesting Admin if denied."""
    if os.name != "nt":
        return {"status": "unsupported", "message": "Windows process control is active on Windows."}

    target_name = (process_name or "").strip()
    target_pid = int(pid) if str(pid).isdigit() else None

    if not target_name and target_pid is None:
        return {"status": "error", "message": "Specify a process name or PID to terminate."}

    ps_list = (
        "Get-Process | Select-Object ProcessName, Id, MainWindowTitle, Path | ConvertTo-Json -Compress"
    )
    ok, raw_json = _run_powershell_query(ps_list)
    if not ok:
        return {"status": "error", "message": f"Could not inspect running processes: {raw_json}"}

    try:
        parsed = json.loads(raw_json)
        procs = parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
    except json.JSONDecodeError:
        return {"status": "error", "message": "Failed to parse process list."}

    my_pid = os.getpid()
    matched = []
    q_stem = re.sub(r"\.exe$", "", target_name, flags=re.IGNORECASE).lower()

    for p in procs:
        p_id = p.get("Id")
        p_name = str(p.get("ProcessName") or "")
        p_title = str(p.get("MainWindowTitle") or "")
        if p_id == my_pid:
            continue
        if target_pid is not None and p_id == target_pid:
            matched.append(p)
        elif q_stem and (
            p_name.lower() == q_stem
            or q_stem in p_name.lower()
            or (p_title and q_stem in p_title.lower())
        ):
            matched.append(p)

    if not matched:
        return {
            "status": "not_running",
            "message": f"No running process matched '{target_name or target_pid}'.",
        }

    safe_targets = []
    for p in matched:
        pname_exe = f"{p.get('ProcessName', '')}.exe".lower()
        pname_raw = str(p.get("ProcessName", "")).lower()
        if pname_exe in PROTECTED_SYSTEM_PROCESSES or pname_raw in PROTECTED_SYSTEM_PROCESSES:
            return {
                "status": "blocked_critical_process",
                "message": f"Refused to end critical Windows system process '{p.get('ProcessName')}' (PID {p.get('Id')}).",
            }
        safe_targets.append(p)

    distinct_names = {p.get("ProcessName", "").lower() for p in safe_targets}
    if len(distinct_names) > 1 and not any(n == q_stem for n in distinct_names):
        return {
            "status": "multiple_processes",
            "message": f"Multiple distinct processes matched '{target_name}'. Ask the user which one to end.",
            "processes": [
                {"name": p.get("ProcessName"), "pid": p.get("Id"), "window": p.get("MainWindowTitle")}
                for p in safe_targets[:10]
            ],
        }

    if any(n == q_stem for n in distinct_names):
        safe_targets = [p for p in safe_targets if p.get("ProcessName", "").lower() == q_stem]

    terminated = []
    access_denied = False
    errors = []

    for p in safe_targets:
        p_id = p.get("Id")
        p_name = p.get("ProcessName")
        kill_ok, kill_out = _run_powershell_query(
            f"Stop-Process -Id {p_id} -Force -ErrorAction Stop"
        )
        if kill_ok:
            terminated.append(f"{p_name} (PID {p_id})")
        else:
            if "ACCESS_DENIED" in kill_out or "access is denied" in kill_out.lower():
                access_denied = True
            else:
                errors.append(f"{p_name} (PID {p_id}): {kill_out}")

    if access_denied and not is_user_admin():
        print(_c(f"\n  [!] Administrator permission is required to terminate '{target_name}'.", "1;93"))
        if _ask_yes_no("      Grant Windows Administrator permission now to end it? (Y/N): "):
            request_admin_elevation_and_restart(entries or [], pending_query=user_query)
        return {
            "status": "permission_denied",
            "message": "Windows denied access to end that process without Administrator privileges.",
        }

    if terminated:
        print(_c(f"\n  [+] TERMINATED PROCESS: {', '.join(terminated)}", "1;92"))
    return {
        "status": "terminated" if terminated else "failed",
        "terminated": terminated,
        "errors": errors,
    }


# --- Smart Natural-Language Reminder Parser & Free-Slot Scheduler ---

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

WEEKDAY_MAP = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _normalize_year(year_str):
    y = int(year_str)
    return 2000 + y if y < 100 else y


def _extract_date_from_text(text, now):
    """Finds an explicit or relative date in text. Returns (date_obj or None, matched_span or None)."""
    m = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            return d, m.span()
        except ValueError:
            pass

    month_pat = "|".join(MONTH_MAP.keys())
    m = re.search(
        rf"\b(\d{{1,2}})[\s\-/\.]({month_pat})(?:[\s\-/\.,]+(\d{{2,4}}))?\b",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        day = int(m.group(1))
        month = MONTH_MAP[m.group(2).lower()]
        year = _normalize_year(m.group(3)) if m.group(3) else now.year
        try:
            d = datetime(year, month, day).date()
            if not m.group(3) and d < now.date():
                d = datetime(year + 1, month, day).date()
            return d, m.span()
        except ValueError:
            pass

    m = re.search(
        rf"\b({month_pat})[\s\-/\.]+(\d{{1,2}})(?:st|nd|rd|th)?(?:[\s\-/\.,]+(\d{{2,4}}))?\b",
        text,
        flags=re.IGNORECASE,
    )
    if m:
        month = MONTH_MAP[m.group(1).lower()]
        day = int(m.group(2))
        year = _normalize_year(m.group(3)) if m.group(3) else now.year
        try:
            d = datetime(year, month, day).date()
            if not m.group(3) and d < now.date():
                d = datetime(year + 1, month, day).date()
            return d, m.span()
        except ValueError:
            pass

    m = re.search(r"\b(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2,4})\b", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), _normalize_year(m.group(3))
        try:
            return datetime(year, month, day).date(), m.span()
        except ValueError:
            pass

    m = re.search(r"\bday after tomorrow\b", text, flags=re.IGNORECASE)
    if m:
        return now.date() + timedelta(days=2), m.span()

    m = re.search(r"\b(tomorrow|tmrw|tmr)\b", text, flags=re.IGNORECASE)
    if m:
        return now.date() + timedelta(days=1), m.span()

    m = re.search(r"\btoday\b", text, flags=re.IGNORECASE)
    if m:
        return now.date(), m.span()

    m = re.search(r"\bin\s+(\d+)\s+days?\b", text, flags=re.IGNORECASE)
    if m:
        return now.date() + timedelta(days=int(m.group(1))), m.span()

    day_pat = "|".join(WEEKDAY_MAP.keys())
    m = re.search(rf"\b(?:next\s+)?({day_pat})\b", text, flags=re.IGNORECASE)
    if m:
        target_wd = WEEKDAY_MAP[m.group(1).lower()]
        days_ahead = (target_wd - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return now.date() + timedelta(days=days_ahead), m.span()

    return None, None


def _extract_time_from_text(text):
    """Finds an explicit time in text. Returns ('HH:MM' 24h string or None, matched_span or None)."""
    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, flags=re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = m.group(3).lower()
        if 1 <= hour <= 12 and 0 <= minute <= 59:
            if meridiem == "pm" and hour != 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            return f"{hour:02d}:{minute:02d}", m.span()

    m = re.search(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b", text, flags=re.IGNORECASE)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", m.span()

    m = re.search(r"\bnoon\b", text, flags=re.IGNORECASE)
    if m:
        return "12:00", m.span()

    m = re.search(r"\bmidnight\b", text, flags=re.IGNORECASE)
    if m:
        return "23:59", m.span()

    m = re.search(r"\b(eod|end of day)\b", text, flags=re.IGNORECASE)
    if m:
        return "21:00", m.span()

    return None, None


def find_free_slot_for_date(target_date, reminders, entries, now):
    """Finds an open time slot on target_date by checking existing reminders and diary notes."""
    date_iso = target_date.strftime("%Y-%m-%d")
    busy_minutes = []

    for r in reminders:
        if r.get("due_date") == date_iso and r.get("due_time"):
            try:
                hh, mm = map(int, r["due_time"].split(":"))
                busy_minutes.append(hh * 60 + mm)
            except ValueError:
                pass

    date_aliases = {
        date_iso,
        target_date.strftime("%d-%b-%y").lower(),
        target_date.strftime("%d-%b-%Y").lower(),
        target_date.strftime("%d %b").lower(),
        target_date.strftime("%b %d").lower(),
    }
    for e in entries:
        blob = f"{e.get('date', '')} {e.get('title', '')} {e.get('content', '')}".lower()
        if any(alias in blob for alias in date_aliases):
            for hh_str, mm_str, mer in re.findall(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", blob):
                h = int(hh_str)
                m = int(mm_str or 0)
                if 1 <= h <= 12:
                    if mer == "pm" and h != 12:
                        h += 12
                    elif mer == "am" and h == 12:
                        h = 0
                    busy_minutes.append(h * 60 + m)

    candidate_hours = [10, 11, 14, 15, 16, 17, 12, 18, 19, 20, 9]
    current_minutes = now.hour * 60 + now.minute

    for hour in candidate_hours:
        slot_min = hour * 60
        if target_date == now.date() and slot_min <= current_minutes + 15:
            continue
        if all(abs(slot_min - b) >= 60 for b in busy_minutes):
            return f"{hour:02d}:00"

    return "21:00"


def format_12h_time(time_24):
    try:
        dt = datetime.strptime(time_24, "%H:%M")
        return dt.strftime("%I:%M %p")
    except ValueError:
        return time_24


def parse_and_create_reminder(raw_text, reminders, entries):
    """Parses natural language into a structured reminder with smart date/time rules."""
    now = datetime.now()
    working = raw_text.strip()

    parsed_date, date_span = _extract_date_from_text(working, now)
    if date_span:
        working = (working[:date_span[0]] + " " + working[date_span[1]:]).strip()

    parsed_time, time_span = _extract_time_from_text(working)
    if time_span:
        working = (working[:time_span[0]] + " " + working[time_span[1]:]).strip()

    cleaned_task = re.sub(
        r"^(?:please\s+)?(?:remind\s+me\s+(?:to\s+)?|reminder\s*(?::|-|to)?\s*|add\s+(?:a\s+)?(?:reminder|task)\s*(?:to|for)?\s*|schedule\s+(?:a\s+)?(?:task|reminder)?\s*(?:to|for)?\s*)",
        "",
        working,
        flags=re.IGNORECASE,
    ).strip()
    cleaned_task = re.sub(r"\b(?:on|at|by|for)\s*$", "", cleaned_task, flags=re.IGNORECASE).strip(" ,.-")
    if cleaned_task:
        cleaned_task = cleaned_task[0].upper() + cleaned_task[1:]
    else:
        cleaned_task = raw_text.strip()

    if parsed_date and parsed_time:
        due_date = parsed_date
        due_time = parsed_time
        schedule_note = "Scheduled as requested"
    elif not parsed_date and not parsed_time:
        due_date = now.date()
        due_time = "21:00" if now.hour < 21 else "23:59"
        schedule_note = "EOD Today (no date/time given)"
    elif parsed_date and not parsed_time:
        due_date = parsed_date
        due_time = find_free_slot_for_date(due_date, reminders, entries, now)
        schedule_note = "Auto-picked free slot in your schedule"
    else:
        due_time = parsed_time
        hh, mm = map(int, due_time.split(":"))
        if (hh, mm) > (now.hour, now.minute):
            due_date = now.date()
        else:
            due_date = now.date() + timedelta(days=1)
        schedule_note = "Scheduled for next upcoming time"

    reminder_record = {
        "id": now.strftime("%Y%m%d%H%M%S%f"),
        "task": cleaned_task,
        "raw_input": raw_text.strip(),
        "due_date": due_date.strftime("%Y-%m-%d"),
        "due_time": due_time,
        "schedule_note": schedule_note,
        "done": False,
        "created_at": now.strftime("%Y-%m-%d %I:%M %p"),
        "completed_at": None,
    }
    reminders.append(reminder_record)
    save_data(entries, reminders=reminders)
    return reminder_record


def add_reminder_flow(reminders, entries):
    clear_screen()
    now = datetime.now()
    print(f"=== NEW REMINDER  (Now: {now.strftime('%Y-%m-%d %a %I:%M %p')}) ===")
    print("Examples:")
    print("  • remind me to go to Client tomorrow 9 AM")
    print("  • Go to Gym  (auto-sets for Today EOD)")
    print("  • Tax review on 11-dec-26  (auto-picks a free slot on that day)")
    print("-" * 60)

    raw_text = input("What should I remind you about?: ").strip()
    if not raw_text:
        print("\n[!] Empty reminder discarded.")
        input("Press Enter to return to menu...")
        return

    rec = parse_and_create_reminder(raw_text, reminders, entries)
    day_name = datetime.strptime(rec["due_date"], "%Y-%m-%d").strftime("%A")
    print("\n[+] Reminder Saved!")
    print(f"    Task:     {rec['task']}")
    print(f"    Date:     {rec['due_date']} ({day_name})")
    print(f"    Time:     {format_12h_time(rec['due_time'])}  [{rec['schedule_note']}]")
    input("\nPress Enter to continue...")


def get_sorted_pending_reminders(reminders):
    pending = [r for r in reminders if not r.get("done")]
    pending.sort(key=lambda r: (r.get("due_date", "9999-12-31"), r.get("due_time", "23:59")))
    return pending


def show_startup_reminders(reminders, entries):
    """Displays pending tasks on launch; Enter closes, typing task numbers marks them complete."""
    while True:
        pending = get_sorted_pending_reminders(reminders)
        if not pending:
            return

        clear_screen()
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        now_time_str = now.strftime("%H:%M")

        print("=================================================================")
        print(f"   PENDING REMINDERS & TASKS  ({len(pending)} pending)")
        print("=================================================================")

        for idx, r in enumerate(pending, start=1):
            d_str = r.get("due_date", "")
            t_str = r.get("due_time", "21:00")
            day_short = datetime.strptime(d_str, "%Y-%m-%d").strftime("%a") if d_str else ""

            if d_str < today_str or (d_str == today_str and t_str < now_time_str):
                badge = "[OVERDUE] "
            elif d_str == today_str:
                badge = "[TODAY]   "
            else:
                badge = "[UPCOMING]"

            time_label = format_12h_time(t_str)
            if "EOD" in r.get("schedule_note", ""):
                time_label += " (EOD)"

            print(f"  {idx:>2}. {badge} {d_str} ({day_short}) {time_label:<15} | {r['task']}")

        print("-----------------------------------------------------------------")
        print("Type task number(s) to mark DONE (e.g. 1 or 1,2), or press Enter to close:")
        choice = input("> ").strip()

        if not choice:
            return

        tokens = [tok for tok in re.split(r"[\s,]+", choice) if tok]
        valid_indices = []
        for tok in tokens:
            if tok.isdigit() and 1 <= int(tok) <= len(pending):
                valid_indices.append(int(tok) - 1)

        if not valid_indices:
            print("[!] Invalid task number.")
            time.sleep(0.8)
            continue

        completed_now = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        marked_names = []
        for i in sorted(set(valid_indices)):
            pending[i]["done"] = True
            pending[i]["completed_at"] = completed_now
            marked_names.append(pending[i]["task"])

        save_data(entries, reminders=reminders)
        print(f"[+] Marked complete: {', '.join(marked_names)}")
        time.sleep(0.9)


def manage_reminders_menu(reminders, entries):
    """Full reminder & task manager accessible anytime from the main menu."""
    while True:
        clear_screen()
        pending = get_sorted_pending_reminders(reminders)
        completed = [r for r in reminders if r.get("done")]

        print("=================================================================")
        print(f"   REMINDERS & TASKS  ({len(pending)} pending | {len(completed)} completed)")
        print("=================================================================")
        if pending:
            print("Pending:")
            for idx, r in enumerate(pending, start=1):
                day_short = datetime.strptime(r["due_date"], "%Y-%m-%d").strftime("%a")
                print(f"  {idx:>2}. [ ] {r['due_date']} ({day_short}) {format_12h_time(r['due_time'])} - {r['task']}")
        else:
            print("No pending reminders.")

        if completed:
            print("\nRecently Completed:")
            for r in completed[-5:]:
                print(f"   ✓  [x] {r['due_date']} - {r['task']} (Done: {r.get('completed_at', '')})")

        print("\n-----------------------------------------------------------------")
        print("  [A] Add New Reminder   |  [1-9] Mark Task Done   |  [C] Clear Done   |  [Enter] Back")
        cmd = input("> ").strip()

        if not cmd:
            return
        if cmd.upper() == "A":
            add_reminder_flow(reminders, entries)
        elif cmd.upper() == "C":
            reminders[:] = [r for r in reminders if not r.get("done")]
            save_data(entries, reminders=reminders)
            print("[+] Cleared completed reminders.")
            time.sleep(0.8)
        else:
            tokens = [tok for tok in re.split(r"[\s,]+", cmd) if tok]
            valid = [int(t) - 1 for t in tokens if t.isdigit() and 1 <= int(t) <= len(pending)]
            if valid:
                done_ts = datetime.now().strftime("%Y-%m-%d %I:%M %p")
                for i in set(valid):
                    pending[i]["done"] = True
                    pending[i]["completed_at"] = done_ts
                save_data(entries, reminders=reminders)
                print("[+] Marked selected task(s) as complete.")
                time.sleep(0.8)
            else:
                print("[!] Invalid option.")
                time.sleep(0.7)


# --- Diary Entry Creation, Categorization (Manual vs Auto) & Editing ---

def get_entry_source(entry):
    """Returns 'auto' for AI-generated entries or 'manual' for user-written entries."""
    src = str(entry.get("source", "manual")).strip().lower()
    return "auto" if src == "auto" else "manual"


def create_journal_entry_record(entries, title, content, source="manual"):
    """Creates and persists a diary journal entry tagged as 'manual' or 'auto'."""
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %I:%M %p")
    auto_title = now.strftime("%Y-%m-%d")
    clean_title = (title or "").strip()
    if not clean_title or clean_title.lower() == "auto":
        clean_title = auto_title

    clean_source = "auto" if str(source).strip().lower() == "auto" else "manual"
    new_record = {
        "id": now.strftime("%Y%m%d%H%M%S%f"),
        "date": now_str,
        "title": clean_title,
        "content": content.strip(),
        "source": clean_source,
    }
    entries.insert(0, new_record)
    save_data(entries)
    return new_record


def add_entry(entries, reminders):
    clear_screen()
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %I:%M %p")

    print(f"=== NEW MANUAL DIARY ENTRY ({now_str}) ===")
    raw_title = input("Press Enter to generate auto Title, Give a Title, or type 'RRR' for Reminder: ").strip()

    if raw_title.upper() == "RRR":
        add_reminder_flow(reminders, entries)
        return

    content = read_multiline_input("Write your entry (type 'END' on a new line to finish):")

    if not content:
        print("\n[!] Empty entry discarded.")
        input("Press Enter to return to menu...")
        return

    rec = create_journal_entry_record(entries, raw_title, content, source="manual")
    print(f"\n[+] Manual Entry '{rec['title']}' saved inside {os.path.basename(STORAGE_PATH)}.")
    input("Press Enter to continue...")


def display_entry(entry):
    clear_screen()
    cat_label = "Auto Entry (Created by AI)" if get_entry_source(entry) == "auto" else "Manual Entry"
    print("=" * 60)
    print(f"Category : {cat_label}")
    print(f"Date     : {entry['date']}")
    print(f"Title    : {entry['title']}")
    print("-" * 60)
    print(entry["content"])
    print("=" * 60)


def browse_entries_by_category(entries):
    """Lets the user choose between Manual Entries, Auto Entries (AI), or All Entries."""
    while True:
        manual_list = [e for e in entries if get_entry_source(e) == "manual"]
        auto_list = [e for e in entries if get_entry_source(e) == "auto"]

        clear_screen()
        print("========================================")
        print("      JOURNAL / ENTRIES CATEGORIES")
        print("========================================")
        print(f"  1. Manual Entry  ({len(manual_list)} entries)")
        print(f"  2. Auto Entry    ({len(auto_list)} entries)  [Created by AI]")
        print(f"  3. All Entries   ({len(entries)} entries)")
        print("  0. Back to Main Menu")
        print("----------------------------------------")

        cat_choice = input("Select category (0-3): ").strip()
        if not cat_choice or cat_choice == "0":
            return
        elif cat_choice == "1":
            browse_entries(entries, subset=manual_list, header="MANUAL ENTRIES", show_badge=False)
        elif cat_choice == "2":
            browse_entries(entries, subset=auto_list, header="AUTO ENTRIES (CREATED BY AI)", show_badge=False)
        elif cat_choice == "3":
            browse_entries(entries, subset=entries, header="ALL DIARY ENTRIES", show_badge=True)
        else:
            print("[!] Invalid selection.")
            time.sleep(0.7)


def browse_entries(entries, subset=None, header="ALL DIARY ENTRIES", show_badge=True):
    while True:
        if subset is not None:
            if header == "MANUAL ENTRIES":
                target = [e for e in entries if get_entry_source(e) == "manual"]
            elif header == "AUTO ENTRIES (CREATED BY AI)":
                target = [e for e in entries if get_entry_source(e) == "auto"]
            elif header == "ALL DIARY ENTRIES":
                target = entries
            else:
                target = [e for e in subset if e in entries]
        else:
            target = entries

        clear_screen()
        print(f"=== {header} ({len(target)}) ===\n")

        if not target:
            print("No entries found in this category.")
            input("\nPress Enter to return...")
            return

        for idx, entry in enumerate(target, start=1):
            short_date = entry["date"].split(" ")[0]
            badge = ("[Auto]   " if get_entry_source(entry) == "auto" else "[Manual] ") if show_badge else ""
            if entry["title"] == short_date:
                print(f"  {idx:>2}. {badge}[{entry['date']}]")
            else:
                print(f"  {idx:>2}. {badge}[{short_date}] {entry['title']}")

        print("\nEnter entry number to read, or press Enter to go back:")
        choice = input("> ").strip()
        if not choice:
            return

        if choice.isdigit() and 1 <= int(choice) <= len(target):
            selected = target[int(choice) - 1]
            entry_actions(entries, selected)
        else:
            print("[!] Invalid selection.")
            input("Press Enter to continue...")


def edit_lines_mode(entries, entry):
    """Allows editing, inserting, or deleting individual lines without rewriting the entry."""
    while True:
        clear_screen()
        lines = entry["content"].splitlines()
        print(f"=== LINE-BY-LINE EDITOR: {entry['title']} ===")
        print("-" * 60)
        if not lines:
            print("  (Entry has no lines yet)")
        else:
            for idx, line in enumerate(lines, start=1):
                print(f"  {idx:>2} | {line}")
        print("-" * 60)
        print("Enter a line number to edit, 'I' to insert a new line, or Enter to finish:")
        cmd = input("> ").strip().upper()

        if not cmd or cmd == "DONE":
            break

        if cmd == "I":
            pos_str = input(f"Insert before line number (1-{len(lines) + 1}, Enter for end): ").strip()
            pos = len(lines) + 1 if not pos_str else (int(pos_str) if pos_str.isdigit() else -1)
            if 1 <= pos <= len(lines) + 1:
                new_line = input("New line text: ")
                lines.insert(pos - 1, new_line)
                entry["content"] = "\n".join(lines)
                save_data(entries)
                print("[+] Line inserted and saved.")
            else:
                print("[!] Invalid line number.")
            time.sleep(0.7)

        elif cmd.isdigit() and 1 <= int(cmd) <= len(lines):
            idx = int(cmd) - 1
            print(f"\nCurrent line {idx + 1}: {lines[idx]}")
            print("Type replacement text, '/del' to delete this line, or press Enter to cancel:")
            replacement = input("> ")
            if replacement.strip().lower() == "/del":
                lines.pop(idx)
                entry["content"] = "\n".join(lines)
                save_data(entries)
                print("[+] Line deleted and saved.")
            elif replacement != "":
                lines[idx] = replacement
                entry["content"] = "\n".join(lines)
                save_data(entries)
                print("[+] Line updated and saved.")
            time.sleep(0.7)
        else:
            print("[!] Invalid input.")
            time.sleep(0.7)


# --- Automatic System App & IDE Scanner (for Diary External Editor Menu) ---

FRIENDLY_NAMES = {
    "notepad.exe": "Notepad",
    "notepad++.exe": "Notepad++",
    "devenv.exe": "Visual Studio",
    "code.exe": "Visual Studio Code",
    "code - insiders.exe": "VS Code Insiders",
    "cursor.exe": "Cursor",
    "windsurf.exe": "Windsurf",
    "subl.exe": "Sublime Text",
    "atom.exe": "Atom",
    "zed.exe": "Zed",
    "pycharm64.exe": "PyCharm",
    "pycharm.exe": "PyCharm",
    "idea64.exe": "IntelliJ IDEA",
    "idea.exe": "IntelliJ IDEA",
    "webstorm64.exe": "WebStorm",
    "clion64.exe": "CLion",
    "rider64.exe": "Rider",
    "goland64.exe": "GoLand",
    "phpstorm64.exe": "PhpStorm",
    "datagrip64.exe": "DataGrip",
    "rstudio.exe": "RStudio",
    "winword.exe": "Microsoft Word",
    "wordpad.exe": "WordPad",
    "soffice.exe": "LibreOffice Writer",
    "swriter.exe": "LibreOffice Writer",
    "wps.exe": "WPS Writer",
    "typora.exe": "Typora",
    "gvim.exe": "gVim",
    "nvim-qt.exe": "Neovim Qt",
    "emacs.exe": "Emacs",
    "uedit64.exe": "UltraEdit",
    "editplus.exe": "EditPlus",
    "geany.exe": "Geany",
    "brackets.exe": "Brackets",
    "kate.exe": "Kate",
}

EDITOR_LAUNCH_FLAGS = {
    "devenv.exe": ["/Edit"],
    "code.exe": ["--wait"],
    "code - insiders.exe": ["--wait"],
    "cursor.exe": ["--wait"],
    "windsurf.exe": ["--wait"],
    "subl.exe": ["--wait"],
    "atom.exe": ["--wait"],
    "zed.exe": ["--wait"],
    "notepad++.exe": ["-multiInst", "-nosession"],
    "gvim.exe": ["-f"],
    "swriter.exe": ["--writer"],
    "soffice.exe": ["--writer"],
}

IDE_HANDOFF_EXES = {
    "devenv.exe",
    "pycharm64.exe",
    "pycharm.exe",
    "idea64.exe",
    "idea.exe",
    "webstorm64.exe",
    "clion64.exe",
    "rider64.exe",
    "goland64.exe",
    "phpstorm64.exe",
    "datagrip64.exe",
}

NON_LOCAL_EDITOR_BINARIES = {
    "notion.exe",
    "obsidian.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "iexplore.exe",
    "acrord32.exe",
    "acrobat.exe",
    "foxitpdfreader.exe",
    "sumatrapdf.exe",
    "rundll32.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "explorer.exe",
    "msiexec.exe",
    "openwith.exe",
    "pickerhost.exe",
    "launchwinapp.exe",
    "dllhost.exe",
    "conhost.exe",
    "python.exe",
    "pythonw.exe",
}


def _extract_exe_from_command(raw_cmd):
    if not raw_cmd:
        return None
    expanded = os.path.expandvars(raw_cmd.strip())
    if expanded.startswith('"'):
        end_quote = expanded.find('"', 1)
        if end_quote != -1:
            candidate = expanded[1:end_quote]
            if os.path.isfile(candidate):
                return candidate
    else:
        match = re.match(r'^([^\s",]+\.exe)\b', expanded, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1)
            if os.path.isfile(candidate):
                return candidate
            resolved = shutil.which(candidate)
            if resolved and os.path.isfile(resolved):
                return resolved
    return None


def _get_windows_exe_friendly_name(exe_path):
    base = os.path.basename(exe_path).lower()
    if base in FRIENDLY_NAMES:
        return FRIENDLY_NAMES[base]

    if os.name == "nt":
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"Applications\\{os.path.basename(exe_path)}") as key:
                val, _ = winreg.QueryValueEx(key, "FriendlyAppName")
                if isinstance(val, str) and val.strip() and not val.startswith("@"):
                    return val.strip()
        except OSError:
            pass

        try:
            version_dll = ctypes.windll.version
            size = version_dll.GetFileVersionInfoSizeW(exe_path, None)
            if size:
                buf = ctypes.create_string_buffer(size)
                if version_dll.GetFileVersionInfoW(exe_path, 0, size, buf):
                    ptr = ctypes.c_void_p()
                    length = ctypes.c_uint()
                    if version_dll.VerQueryValueW(
                        buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(length)
                    ) and length.value >= 4:
                        lang, codepage = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint16 * 2)).contents
                        for field in ("ProductName", "FileDescription"):
                            sub_block = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{field}"
                            if version_dll.VerQueryValueW(
                                buf, sub_block, ctypes.byref(ptr), ctypes.byref(length)
                            ) and length.value > 1:
                                desc = ctypes.wstring_at(ptr).strip()
                                if desc and len(desc) <= 45:
                                    return desc
        except Exception:
            pass

    stem = os.path.splitext(os.path.basename(exe_path))[0]
    return stem.replace("_", " ").replace("-", " ").title()


def _resolve_progid_command(progid):
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT):
        prefix = "Software\\Classes\\" if root == winreg.HKEY_CURRENT_USER else ""
        for verb in ("open", "edit"):
            subkey = f"{prefix}{progid}\\shell\\{verb}\\command"
            try:
                with winreg.OpenKey(root, subkey) as key:
                    cmd_val, _ = winreg.QueryValueEx(key, None)
                    exe = _extract_exe_from_command(cmd_val)
                    if exe:
                        return exe
            except OSError:
                continue
    return None


def _extract_exe_targets_from_lnk(lnk_path):
    targets = []
    try:
        with open(lnk_path, "rb") as f:
            blob = f.read()
        for m in re.findall(rb'[A-Za-z]:\\[^\x00-\x1f"*?<>|]+\.exe', blob, flags=re.IGNORECASE):
            try:
                targets.append(m.decode("latin-1"))
            except Exception:
                pass
        for m in re.findall(
            rb'(?:[A-Za-z]\x00:\x00\\\x00(?:[^\x00-\x1f"*?<>|]\x00)+\.\x00e\x00x\x00e\x00)',
            blob,
            flags=re.IGNORECASE,
        ):
            try:
                targets.append(m.decode("utf-16le"))
            except Exception:
                pass
    except OSError:
        pass
    return targets


def discover_compatible_editors():
    """Scans the current machine for all installed local editors, IDEs, and word processors."""
    found_by_path = {}
    target_exes = set(FRIENDLY_NAMES.keys())

    def register_exe(exe_path, restrict_to_known=False):
        if not exe_path or not os.path.isfile(exe_path):
            return
        norm = os.path.normcase(os.path.abspath(exe_path))
        base = os.path.basename(norm)
        if not base.endswith(".exe") or base in NON_LOCAL_EDITOR_BINARIES:
            return
        if restrict_to_known and base not in target_exes:
            return
        if norm in found_by_path:
            return
        name = _get_windows_exe_friendly_name(exe_path)
        flags = EDITOR_LAUNCH_FLAGS.get(base, [])
        found_by_path[norm] = {
            "name": name,
            "cmd": [os.path.abspath(exe_path)] + flags,
        }

    if os.name == "nt":
        for ext in (".txt", ".log"):
            reg_locations = [
                (winreg.HKEY_CLASSES_ROOT, f"{ext}\\OpenWithProgids"),
                (
                    winreg.HKEY_CURRENT_USER,
                    f"Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\{ext}\\OpenWithProgids",
                ),
            ]
            for hive, path in reg_locations:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        idx = 0
                        while True:
                            try:
                                progid, _, _ = winreg.EnumValue(key, idx)
                                idx += 1
                                register_exe(_resolve_progid_command(progid))
                            except OSError:
                                break
                except OSError:
                    pass

            list_locations = [
                (winreg.HKEY_CLASSES_ROOT, f"{ext}\\OpenWithList"),
                (
                    winreg.HKEY_CURRENT_USER,
                    f"Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\{ext}\\OpenWithList",
                ),
            ]
            for hive, path in list_locations:
                try:
                    with winreg.OpenKey(hive, path) as key:
                        s_idx = 0
                        while True:
                            try:
                                sub_exe = winreg.EnumKey(key, s_idx)
                                s_idx += 1
                                exe = _resolve_progid_command(f"Applications\\{sub_exe}") or shutil.which(sub_exe)
                                register_exe(exe)
                            except OSError:
                                break
                        v_idx = 0
                        while True:
                            try:
                                v_name, v_data, _ = winreg.EnumValue(key, v_idx)
                                v_idx += 1
                                if v_name.lower() != "mrulist" and isinstance(v_data, str):
                                    exe = _resolve_progid_command(f"Applications\\{v_data}") or shutil.which(v_data)
                                    register_exe(exe)
                            except OSError:
                                break
                except OSError:
                    pass

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for exe_name in target_exes:
                try:
                    with winreg.OpenKey(
                        hive, f"Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{exe_name}"
                    ) as key:
                        val, _ = winreg.QueryValueEx(key, None)
                        register_exe(val.strip('"'))
                except OSError:
                    pass

            uninstall_paths = [
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ]
            for uninst_root in uninstall_paths:
                try:
                    with winreg.OpenKey(hive, uninst_root) as ukey:
                        idx = 0
                        while True:
                            try:
                                subkey_name = winreg.EnumKey(ukey, idx)
                                idx += 1
                                with winreg.OpenKey(ukey, subkey_name) as app_key:
                                    try:
                                        icon_val, _ = winreg.QueryValueEx(app_key, "DisplayIcon")
                                        exe = _extract_exe_from_command(icon_val)
                                        register_exe(exe, restrict_to_known=True)
                                    except OSError:
                                        pass
                                    try:
                                        inst_loc, _ = winreg.QueryValueEx(app_key, "InstallLocation")
                                        if isinstance(inst_loc, str) and os.path.isdir(inst_loc):
                                            for exe_name in target_exes:
                                                for sub in ("", "bin", r"Common7\IDE"):
                                                    cand = os.path.join(inst_loc, sub, exe_name)
                                                    if os.path.isfile(cand):
                                                        register_exe(cand)
                                    except OSError:
                                        pass
                            except OSError:
                                break
                except OSError:
                    pass

        prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app = os.environ.get("LOCALAPPDATA", "")

        vswhere = os.path.join(prog_files_x86, "Microsoft Visual Studio", "Installer", "vswhere.exe")
        if os.path.isfile(vswhere):
            try:
                out = subprocess.check_output(
                    [vswhere, "-products", "*", "-property", "productPath"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3,
                )
                for line in out.splitlines():
                    register_exe(line.strip())
            except Exception:
                pass

        for base_pf in (prog_files, prog_files_x86):
            vs_root = os.path.join(base_pf, "Microsoft Visual Studio")
            if os.path.isdir(vs_root):
                for year_dir in os.scandir(vs_root):
                    if year_dir.is_dir():
                        for ed_dir in os.scandir(year_dir.path):
                            if ed_dir.is_dir():
                                register_exe(os.path.join(ed_dir.path, "Common7", "IDE", "devenv.exe"))

        start_menu_dirs = [
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
        ]
        for sm_dir in start_menu_dirs:
            if not sm_dir or not os.path.isdir(sm_dir):
                continue
            for root_dir, _, files in os.walk(sm_dir):
                for fname in files:
                    if fname.lower().endswith(".lnk"):
                        for target in _extract_exe_targets_from_lnk(os.path.join(root_dir, fname)):
                            register_exe(target, restrict_to_known=True)

        for exe_name in target_exes:
            which_path = shutil.which(exe_name)
            if which_path:
                register_exe(which_path)

        search_roots = [
            os.path.join(local_app, "Programs"),
            os.path.join(local_app, "JetBrains"),
            prog_files,
            prog_files_x86,
        ]
        for root_dir in search_roots:
            if not root_dir or not os.path.isdir(root_dir):
                continue
            try:
                for entry in os.scandir(root_dir):
                    if not entry.is_dir():
                        continue
                    for sub_rel in ("", "bin"):
                        check_dir = os.path.join(entry.path, sub_rel) if sub_rel else entry.path
                        if os.path.isdir(check_dir):
                            for exe_name in target_exes:
                                cand = os.path.join(check_dir, exe_name)
                                if os.path.isfile(cand):
                                    register_exe(cand)
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_dir():
                                for sub_rel in ("", "bin"):
                                    check_dir = os.path.join(sub.path, sub_rel) if sub_rel else sub.path
                                    if os.path.isdir(check_dir):
                                        for exe_name in target_exes:
                                            cand = os.path.join(check_dir, exe_name)
                                            if os.path.isfile(cand):
                                                register_exe(cand)
                    except OSError:
                        pass
            except OSError:
                pass
    else:
        for candidate, label in [
            ("nano", "Nano"),
            ("vim", "Vim"),
            ("nvim", "Neovim"),
            ("code", "Visual Studio Code"),
            ("pycharm", "PyCharm"),
            ("subl", "Sublime Text"),
            ("gedit", "Gedit"),
            ("kate", "Kate"),
            ("libreoffice", "LibreOffice Writer"),
        ]:
            bin_path = shutil.which(candidate)
            if bin_path:
                cmd = [bin_path, "--wait"] if candidate in ("code", "subl") else [bin_path]
                found_by_path[bin_path] = {"name": label, "cmd": cmd}

    deduped = {}
    for item in found_by_path.values():
        key = item["name"].lower()
        if key not in deduped or "windowsapps" in deduped[key]["cmd"][0].lower():
            deduped[key] = item

    return sorted(deduped.values(), key=lambda x: x["name"].lower())


def get_active_editor():
    cmd = DIARY_CONFIG.get("editor_cmd")
    if cmd:
        exe_base = os.path.basename(cmd[0]).lower()
        if exe_base not in NON_LOCAL_EDITOR_BINARIES:
            return DIARY_CONFIG.get("editor_name", "External Editor"), cmd
    if os.name == "nt":
        return "Notepad", ["notepad.exe"]
    default_unix = os.environ.get("EDITOR", "nano")
    return default_unix, [default_unix]


def configure_external_editor(entries):
    clear_screen()
    print("Scanning your computer for compatible editors, IDEs, and document apps...")
    editors = discover_compatible_editors()

    clear_screen()
    current_name, _ = get_active_editor()
    print("========================================")
    print("       CHOOSE EXTERNAL EDITOR APP")
    print("========================================")
    print(f"Current App: {current_name}\n")

    if not editors:
        print("No compatible editors were detected on this computer.")
        input("\nPress Enter to go back...")
        return False

    print("Apps & IDEs found on this computer that can open & edit your diary:")
    for idx, ed in enumerate(editors, start=1):
        active_tag = "  <-- (Currently Selected)" if ed["name"].lower() == current_name.lower() else ""
        print(f"  {idx:>2}. {ed['name']}{active_tag}")
    print("   0. Cancel / Back")
    print("----------------------------------------")

    choice = input(f"Select an app (0-{len(editors)}): ").strip()
    if not choice or choice == "0":
        return False

    if choice.isdigit() and 1 <= int(choice) <= len(editors):
        chosen = editors[int(choice) - 1]
        DIARY_CONFIG["editor_name"] = chosen["name"]
        DIARY_CONFIG["editor_cmd"] = chosen["cmd"]
        save_data(entries, DIARY_CONFIG)
        print(f"\n[+] Diary editor set to: {chosen['name']}")
        input("Press Enter to continue...")
        return True

    print("[!] Invalid selection.")
    input("Press Enter to continue...")
    return False


def edit_in_external_editor(entries, entry):
    editor_name, editor_cmd = get_active_editor()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="diary_edit_", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(entry["content"])
            temp_path = tmp.name

        initial_mtime = os.path.getmtime(temp_path)
        print(f"\nOpening in {editor_name}... Edit your entry and save (Ctrl+S).")
        try:
            subprocess.run(editor_cmd + [temp_path], check=False)
        except FileNotFoundError:
            print(f"[!] Could not launch {editor_name}. Please choose another app.")
            input("Press Enter to continue...")
            return

        exe_base = os.path.basename(editor_cmd[0]).lower()
        file_untouched = os.path.getmtime(temp_path) == initial_mtime
        if "--wait" not in editor_cmd and (exe_base in IDE_HANDOFF_EXES or file_untouched):
            input(f"Press Enter here after you save (Ctrl+S) and close the tab in {editor_name}...")

        with open(temp_path, "r", encoding="utf-8") as tmp:
            updated_text = tmp.read().strip()

        if updated_text and updated_text != entry["content"]:
            entry["content"] = updated_text
            save_data(entries)
            print(f"[+] Changes saved from {editor_name}.")
        elif not updated_text:
            print("[!] File was empty; original entry kept.")
        else:
            print("[i] No changes detected.")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    input("Press Enter to continue...")


def edit_entry(entries, entry):
    while True:
        clear_screen()
        editor_name, _ = get_active_editor()
        lines = entry["content"].splitlines()
        print(f"=== EDITING: {entry['title']} ({entry['date']}) ===")
        print("-" * 60)
        for idx, line in enumerate(lines, start=1):
            print(f"  {idx:>2} | {line}")
        print("-" * 60)
        print("  1. Append new text to the end (keeps existing content)")
        print("  2. Edit / Insert / Delete specific lines")
        print("  3. Find & Replace text")
        print(f"  4. Open in {editor_name}")
        print("  5. Choose a different editor app")
        print("  6. Change Title")
        print("  7. Overwrite entire content from scratch")
        print("  0. Done Editing")
        print("-" * 60)

        choice = input("Select edit mode (0-7): ").strip()

        if choice == "1":
            add_ts = input("Add current time stamp before appended text? (y/N): ").strip().lower()
            extra = read_multiline_input("Write text to append (type 'END' on a new line to finish):")
            if extra:
                prefix = f"\n\n[{datetime.now().strftime('%Y-%m-%d %I:%M %p')}]\n" if add_ts == "y" else "\n\n"
                entry["content"] = entry["content"].rstrip() + prefix + extra
                save_data(entries)
                print("\n[+] Text appended and saved.")
            else:
                print("\n[!] Nothing entered.")
            input("Press Enter to continue...")

        elif choice == "2":
            edit_lines_mode(entries, entry)

        elif choice == "3":
            find_str = input("Text to find: ")
            if not find_str:
                continue
            if find_str not in entry["content"]:
                print(f"[!] '{find_str}' was not found in this entry.")
                input("Press Enter to continue...")
                continue
            replace_str = input("Replace with: ")
            count = entry["content"].count(find_str)
            entry["content"] = entry["content"].replace(find_str, replace_str)
            save_data(entries)
            print(f"[+] Replaced {count} occurrence(s) and saved.")
            input("Press Enter to continue...")

        elif choice == "4":
            edit_in_external_editor(entries, entry)

        elif choice == "5":
            configure_external_editor(entries)

        elif choice == "6":
            new_title = input(f"New Title (Enter to keep '{entry['title']}'): ").strip()
            if new_title:
                entry["title"] = new_title
                save_data(entries)
                print("[+] Title updated.")
                input("Press Enter to continue...")

        elif choice == "7":
            confirm = input("This will replace all existing text. Continue? (y/N): ").strip().lower()
            if confirm == "y":
                new_content = read_multiline_input("Write replacement entry (type 'END' on a new line to save):")
                if new_content:
                    entry["content"] = new_content
                    save_data(entries)
                    print("\n[+] Entry overwritten and saved.")
                else:
                    print("\n[!] Empty input; original content kept.")
                input("Press Enter to continue...")

        elif choice == "0" or choice == "":
            return


def entry_actions(entries, entry):
    while True:
        display_entry(entry)
        print("\nOptions: [E] Edit Entry  |  [D] Delete Entry  |  [B] Back")
        action = input("> ").strip().upper()

        if action == "E":
            edit_entry(entries, entry)

        elif action == "D":
            confirm = input("Delete this entry permanently? (y/N): ").strip().lower()
            if confirm == "y":
                entries[:] = [e for e in entries if e["id"] != entry["id"]]
                save_data(entries)
                print("[+] Entry deleted.")
                input("Press Enter to continue...")
                return

        elif action == "B" or action == "":
            return


def search_entries(entries):
    clear_screen()
    print("=== SEARCH DIARY ===")
    query = input("Keyword (date, title, or text): ").strip().lower()
    if not query:
        return

    matches = [
        e for e in entries
        if query in f"{e['date']} {e['title']} {e['content']}".lower()
    ]
    browse_entries(entries, subset=matches, header=f"SEARCH RESULTS FOR '{query}'", show_badge=True)


# --- Sub-Variant 1B-1: Single-Graph In-Place Heavy Containment Reactor HUD ---

HUD_HEIGHT = 11


def _fit_col(text, width):
    """Pads or trims plain text to an exact visible column width."""
    if len(text) > width:
        return text[:width]
    return text.ljust(width)


def _format_bytes(num_bytes):
    if num_bytes <= 0:
        return "0.0 MB"
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def _format_eta(seconds):
    if seconds is None or seconds < 0 or seconds > 35999:
        return "--:--"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02d}:{secs:02d}"


def _win32_rewind_cursor(num_lines):
    """Moves Windows console cursor up num_lines rows directly via Win32 API."""
    if os.name != "nt":
        return False
    try:
        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", ctypes.c_short),
                ("Top", ctypes.c_short),
                ("Right", ctypes.c_short),
                ("Bottom", ctypes.c_short),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", ctypes.c_ushort),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        csbi = CONSOLE_SCREEN_BUFFER_INFO()
        if kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(csbi)):
            new_y = max(0, csbi.dwCursorPosition.Y - num_lines)
            kernel32.SetConsoleCursorPosition(handle, COORD(0, new_y))
            return True
    except Exception:
        pass
    return False


def _build_reactor_frame_lines(
    frame_idx,
    status_badge,
    target_name,
    downloaded_bytes,
    total_bytes,
    speed_bps,
    eta_seconds,
    step_states,
    compact=False,
):
    """Builds the 11-line Heavy Containment Reactor HUD (67 cols wide, or 50 cols compact)."""
    if total_bytes and total_bytes > 0:
        pct = min(100.0, (downloaded_bytes / total_bytes) * 100.0)
    else:
        pct = float((frame_idx * 8) % 101)

    outer_blades = [
        (r"  \ \ | / /  ", r"  / / | \ \  "),
        (r"  - - + - -  ", r"  = = * = =  "),
        (r"  / / | \ \  ", r"  \ \ | / /  "),
        (r"  = = * = =  ", r"  - - + - -  "),
    ]
    top_blade, bot_blade = outer_blades[frame_idx % len(outer_blades)]
    node_char = "╬" if frame_idx % 2 == 0 else "╪"
    core_char = "◉" if frame_idx % 2 == 0 else "◈"

    bar_width = 12 if compact else 20
    filled_exact = (pct / 100.0) * bar_width
    full_blocks = int(filled_exact)
    rem = filled_exact - full_blocks
    if full_blocks >= bar_width:
        bar_str = "█" * bar_width
    else:
        tip = "▓" if rem >= 0.66 else ("▒" if rem >= 0.33 else "░")
        empty_blocks = max(0, bar_width - full_blocks - 1)
        bar_str = ("█" * full_blocks) + tip + ("░" * empty_blocks)

    wave_seed = "▁▂▄▆█▇▅▃▂▁▂▃▅▇█▆▄▂▁▂▄▆█▅▃▂"
    shift = frame_idx % len(wave_seed)
    wave_full = wave_seed[shift:] + wave_seed[:shift]

    if total_bytes and total_bytes > 0:
        vol_plain = f"{_format_bytes(downloaded_bytes)} / {_format_bytes(total_bytes)}"
    else:
        vol_plain = "Configuring system runtime"

    if speed_bps and speed_bps > 0:
        rate_plain = f"{speed_bps / (1024 * 1024):.1f} MB/s • ETA {_format_eta(eta_seconds)}"
    else:
        rate_plain = "Active • ETA " + _format_eta(eta_seconds)

    b = lambda s: _c(s, "96")

    if compact:
        r1_col = _c(" TARGET: ", "96") + _c(_fit_col(target_name, 18), "1;97")
        r2_col = _c(" VOLUME: ", "96") + _c(_fit_col(vol_plain, 18), "97")
        r3_col = _c(" RATE  : ", "96") + _c(_fit_col(rate_plain, 18), "93")
        pct_i = int(round(pct))
        r4_col = _c(" CHARGE: [", "96") + _c(bar_str, "92") + _c(f"]{pct_i:>3d}%", "1;92")
        r5_col = _c(" WAVE  : ", "96") + _c(wave_full[:18], "96")

        l1_col = _c("    . - * | * - .   ", "96")
        l2_col = _c("  /", "96") + _c(top_blade, "1;93") + _c("\\     ", "96")
        l3_col = _c(f" ══{node_char}══ ", "96") + _c(f"[{core_char}]", "1;92") + _c(f" ══{node_char}══  ", "96")
        l4_col = _c("  \\", "96") + _c(bot_blade, "1;93") + _c("/     ", "96")
        l5_col = _c("    ' - * | * - '   ", "96")

        hdr_r = f"{status_badge.strip()} [{pct:>5.1f}%]"
        hdr_l = " ◉ ARC-REACTOR // MK-II"
        pad_h = max(1, 48 - len(hdr_l) - len(hdr_r) - 1)
        header_col = _c(" ◉ ", "1;92") + _c("ARC-REACTOR // MK-II", "1;97") + (" " * pad_h) + _c(hdr_r, "1;96") + " "

        short_lbls = ["Link", "Stream", "Install", "Model"]
        p_plain, p_col = [], []
        for st, lbl in zip(step_states, short_lbls):
            if st == "done":
                p_plain.append(f"[✓]{lbl}")
                p_col.append(_c("[✓]", "1;92") + _c(lbl, "92"))
            elif st == "active":
                p_plain.append(f"[»]{lbl}")
                p_col.append(_c("[»]", "1;93") + _c(lbl, "1;97"))
            else:
                p_plain.append(f"[ ]{lbl}")
                p_col.append(_c(f"[ ]{lbl}", "90"))
        joined = " " + "  ".join(p_plain)
        footer_col = " " + "  ".join(p_col) + (" " * max(0, 48 - len(joined)))

        return [
            b("╔════════════════════════════════════════════════╗"),
            b("║") + header_col + b("║"),
            b("╠════════════════════╦═══════════════════════════╣"),
            b("║") + l1_col + b("║") + r1_col + b("║"),
            b("║") + l2_col + b("║") + r2_col + b("║"),
            b("║") + l3_col + b("║") + r3_col + b("║"),
            b("║") + l4_col + b("║") + r4_col + b("║"),
            b("║") + l5_col + b("║") + r5_col + b("║"),
            b("╠════════════════════╩═══════════════════════════╣"),
            b("║") + footer_col + b("║"),
            b("╚════════════════════════════════════════════════╝"),
        ]

    r1_col = _c("  TARGET : ", "96") + _c(_fit_col(target_name, 28), "1;97")
    r2_col = _c("  VOLUME : ", "96") + _c(_fit_col(vol_plain, 28), "97")
    r3_col = _c("  RATE   : ", "96") + _c(_fit_col(rate_plain, 28), "93")
    pct_i = int(round(pct))
    r4_col = _c("  CHARGE : [", "96") + _c(bar_str, "92") + _c(f"] {pct_i:>3d}%", "1;92")
    r5_col = _c("  WAVE   : ", "96") + _c(_fit_col(wave_full, 28), "96")

    l1_col = _c("      . - * | * - .      ", "96")
    l2_col = _c("    /", "96") + _c(top_blade, "1;93") + _c("\\    ", "96")
    l3_col = _c(f"  ═══{node_char}═══ ", "96") + _c(f"[ {core_char} ]", "1;92") + _c(f" ═══{node_char}═══  ", "96")
    l4_col = _c("    \\", "96") + _c(bot_blade, "1;93") + _c("/    ", "96")
    l5_col = _c("      ' - * | * - '      ", "96")

    header_right = f"{status_badge.strip()} [{pct:>5.1f}%]"
    header_left = "  ◉ ARC-REACTOR CHAMBER // MK-II"
    pad_hdr = max(1, 65 - len(header_left) - len(header_right) - 2)
    header_col = (
        _c("  ◉ ", "1;92")
        + _c("ARC-REACTOR CHAMBER // MK-II", "1;97")
        + (" " * pad_hdr)
        + _c(header_right, "1;96")
        + "  "
    )

    labels = ["Link Verified", "Streaming", "Install", "AI Model"]
    pills_plain = []
    pills_col = []
    for st, lbl in zip(step_states, labels):
        if st == "done":
            pills_plain.append(f"[✓] {lbl}")
            pills_col.append(_c("[✓] ", "1;92") + _c(lbl, "92"))
        elif st == "active":
            pills_plain.append(f"[»] {lbl}")
            pills_col.append(_c("[»] ", "1;93") + _c(lbl, "1;97"))
        else:
            pills_plain.append(f"[ ] {lbl}")
            pills_col.append(_c(f"[ ] {lbl}", "90"))

    plain_joined = "  " + "   ".join(pills_plain)
    trail_spaces = " " * max(0, 65 - len(plain_joined))
    footer_col = "  " + "   ".join(pills_col) + trail_spaces

    return [
        b("╔═════════════════════════════════════════════════════════════════╗"),
        b("║") + header_col + b("║"),
        b("╠═════════════════════════╦═══════════════════════════════════════╣"),
        b("║") + l1_col + b("║") + r1_col + b("║"),
        b("║") + l2_col + b("║") + r2_col + b("║"),
        b("║") + l3_col + b("║") + r3_col + b("║"),
        b("║") + l4_col + b("║") + r4_col + b("║"),
        b("║") + l5_col + b("║") + r5_col + b("║"),
        b("╠═════════════════════════╩═══════════════════════════════════════╣"),
        b("║") + footer_col + b("║"),
        b("╚═════════════════════════════════════════════════════════════════╝"),
    ]


def render_reactor_hud(
    frame_idx,
    status_badge,
    target_name,
    downloaded_bytes,
    total_bytes,
    speed_bps,
    eta_seconds,
    step_states,
    first_frame=False,
):
    """Updates a single Heavy Containment Reactor graph strictly in place."""
    cols = shutil.get_terminal_size((80, 24)).columns
    compact = cols < 68
    frame_lines = _build_reactor_frame_lines(
        frame_idx,
        status_badge,
        target_name,
        downloaded_bytes,
        total_bytes,
        speed_bps,
        eta_seconds,
        step_states,
        compact=compact,
    )

    if first_frame:
        clear_screen()
        if ANSI_ENABLED:
            sys.stdout.write("\033[?25l")
        sys.stdout.write("\n".join(frame_lines) + "\n")
        sys.stdout.flush()
        return

    if ANSI_ENABLED:
        sys.stdout.write(f"\033[{HUD_HEIGHT}A\r")
        for line in frame_lines:
            sys.stdout.write(line + "\033[K\n")
        sys.stdout.flush()
    elif _win32_rewind_cursor(HUD_HEIGHT):
        sys.stdout.write("\n".join(frame_lines) + "\n")
        sys.stdout.flush()


def _restore_cursor():
    if ANSI_ENABLED:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def find_ollama_executable():
    """Finds the ollama binary and adds its directory to PATH if newly installed."""
    found = shutil.which("ollama")
    if found and os.path.isfile(found):
        return found

    if os.name == "nt":
        candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Ollama", "ollama.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Ollama", "ollama.exe"),
        ]
        for cand in candidates:
            if cand and os.path.isfile(cand):
                folder = os.path.dirname(cand)
                if folder.lower() not in os.environ.get("PATH", "").lower():
                    os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
                return cand
    else:
        for cand in ("/usr/local/bin/ollama", "/opt/homebrew/bin/ollama", "/usr/bin/ollama"):
            if os.path.isfile(cand):
                return cand
    return None


def get_ollama_models():
    """Returns (is_running, list_of_model_names) from the local Ollama server."""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"] for m in data.get("models", []) if "name" in m]
            return True, models
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False, []


def try_wake_ollama():
    """Starts 'ollama serve' silently in the background if Ollama is installed."""
    ollama_bin = find_ollama_executable()
    if not ollama_bin:
        return False
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen([ollama_bin, "serve"], **kwargs)
        for _ in range(10):
            time.sleep(0.5)
            running, _ = get_ollama_models()
            if running:
                return True
    except OSError:
        pass
    return False


def probe_download_link(url, timeout=8):
    """
    Checks internet connectivity and verifies the remote installer link & exact byte size.
    Returns (ok, final_url, size_bytes, status_message).
    """
    headers = {"User-Agent": "PersonalDiary-OllamaSetup/1.0"}
    try:
        req = urllib.request.Request(url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            code = getattr(resp, "status", 200)
            cl = resp.headers.get("Content-Length")
            size_bytes = int(cl) if cl and cl.isdigit() else 0
            if size_bytes > 0:
                return True, final_url, size_bytes, f"ONLINE ({code} OK)"
    except urllib.error.HTTPError as e:
        if e.code not in (403, 405):
            return False, url, 0, f"HTTP Error {e.code}: {e.reason}"
    except Exception as err:
        return False, url, 0, f"Connection failed: {err}"

    try:
        range_headers = dict(headers)
        range_headers["Range"] = "bytes=0-0"
        req = urllib.request.Request(url, headers=range_headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            code = getattr(resp, "status", 200)
            cr = resp.headers.get("Content-Range", "")
            m = re.search(r"/(\d+)$", cr)
            if m:
                return True, final_url, int(m.group(1)), f"ONLINE ({code} OK)"
            cl = resp.headers.get("Content-Length")
            size_bytes = int(cl) if cl and cl.isdigit() else 0
            return True, final_url, size_bytes, f"ONLINE ({code} OK)"
    except Exception as err:
        return False, url, 0, f"Connection failed: {err}"


def download_file_with_reactor(url, dest_path, display_name, expected_size=0):
    """Downloads a file in chunks while updating the single Heavy Containment Reactor HUD in place."""
    headers = {"User-Agent": "PersonalDiary-OllamaSetup/1.0"}
    req = urllib.request.Request(url, headers=headers, method="GET")

    part_path = dest_path + ".part"
    downloaded = 0
    frame_idx = 0
    start_time = time.time()
    last_render = 0.0
    smooth_speed = 0.0
    last_sample_time = start_time
    last_sample_bytes = 0

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            cl = resp.headers.get("Content-Length")
            total_bytes = int(cl) if cl and cl.isdigit() else expected_size

            render_reactor_hud(
                frame_idx=0,
                status_badge="STREAMING",
                target_name=display_name,
                downloaded_bytes=0,
                total_bytes=total_bytes,
                speed_bps=0.0,
                eta_seconds=None,
                step_states=("done", "active", "pending", "pending"),
                first_frame=True,
            )

            with open(part_path, "wb") as out_f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    dt_sample = now - last_sample_time
                    if dt_sample >= 0.30:
                        inst_speed = (downloaded - last_sample_bytes) / dt_sample
                        smooth_speed = inst_speed if smooth_speed == 0 else (0.35 * inst_speed + 0.65 * smooth_speed)
                        last_sample_time = now
                        last_sample_bytes = downloaded

                    if now - last_render >= 0.25:
                        frame_idx += 1
                        eta = ((total_bytes - downloaded) / smooth_speed) if (total_bytes > 0 and smooth_speed > 0) else None
                        render_reactor_hud(
                            frame_idx=frame_idx,
                            status_badge="STREAMING",
                            target_name=display_name,
                            downloaded_bytes=downloaded,
                            total_bytes=total_bytes,
                            speed_bps=smooth_speed,
                            eta_seconds=eta,
                            step_states=("done", "active", "pending", "pending"),
                            first_frame=False,
                        )
                        last_render = now

        os.replace(part_path, dest_path)
        total_final = max(downloaded, total_bytes)
        render_reactor_hud(
            frame_idx=frame_idx + 1,
            status_badge="COMPLETE",
            target_name=display_name,
            downloaded_bytes=total_final,
            total_bytes=total_final,
            speed_bps=smooth_speed,
            eta_seconds=0,
            step_states=("done", "done", "pending", "pending"),
            first_frame=False,
        )
        time.sleep(0.7)
        return True, ""
    except Exception as err:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        return False, str(err)
    finally:
        _restore_cursor()


def install_ollama_with_reactor(installer_path):
    """Runs the downloaded Ollama installer while updating the single Reactor HUD in place."""
    frame_idx = 0
    start_time = time.time()
    try:
        proc = subprocess.Popen(
            [installer_path, "/VERYSILENT", "/NORESTART"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as err:
        return False, str(err)

    try:
        render_reactor_hud(
            frame_idx=0,
            status_badge="INSTALLING",
            target_name="Ollama Runtime (x64)",
            downloaded_bytes=0,
            total_bytes=0,
            speed_bps=0.0,
            eta_seconds=None,
            step_states=("done", "done", "active", "pending"),
            first_frame=True,
        )
        while proc.poll() is None:
            time.sleep(0.25)
            frame_idx += 1
            elapsed = int(time.time() - start_time)
            render_reactor_hud(
                frame_idx=frame_idx,
                status_badge="INSTALLING",
                target_name=f"Ollama Engine ({elapsed}s)",
                downloaded_bytes=0,
                total_bytes=0,
                speed_bps=0.0,
                eta_seconds=None,
                step_states=("done", "done", "active", "pending"),
                first_frame=False,
            )

        for _ in range(12):
            frame_idx += 1
            render_reactor_hud(
                frame_idx=frame_idx,
                status_badge="STARTING",
                target_name="Ollama Local Service",
                downloaded_bytes=0,
                total_bytes=0,
                speed_bps=0.0,
                eta_seconds=None,
                step_states=("done", "done", "active", "pending"),
                first_frame=False,
            )
            if find_ollama_executable():
                running, _ = get_ollama_models()
                if running or try_wake_ollama():
                    render_reactor_hud(
                        frame_idx=frame_idx + 1,
                        status_badge="ONLINE",
                        target_name="Ollama Local Service",
                        downloaded_bytes=1,
                        total_bytes=1,
                        speed_bps=0.0,
                        eta_seconds=0,
                        step_states=("done", "done", "done", "pending"),
                        first_frame=False,
                    )
                    time.sleep(0.7)
                    return True, ""
            time.sleep(0.5)

        if find_ollama_executable():
            return True, ""
        return False, f"Installer exited with code {proc.returncode}."
    finally:
        _restore_cursor()


def pull_ollama_model_with_reactor(model_name):
    """Pulls an offline model via Ollama's streaming API while updating the single Reactor HUD in place."""
    payload = json.dumps({"name": model_name, "stream": True}).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/pull",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    layer_totals = {}
    layer_completed = {}
    frame_idx = 0
    start_time = time.time()
    last_render = 0.0
    smooth_speed = 0.0
    last_sample_time = start_time
    last_sample_bytes = 0

    try:
        render_reactor_hud(
            frame_idx=0,
            status_badge="PULLING",
            target_name=f"Model: {model_name}",
            downloaded_bytes=0,
            total_bytes=0,
            speed_bps=0.0,
            eta_seconds=None,
            step_states=("done", "done", "done", "active"),
            first_frame=True,
        )

        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                event = json.loads(line)
                if "error" in event:
                    return False, event["error"]

                digest = event.get("digest", "")
                tot = event.get("total", 0)
                comp = event.get("completed", 0)
                if digest and tot > 0:
                    layer_totals[digest] = tot
                    layer_completed[digest] = max(layer_completed.get(digest, 0), comp)

                total_bytes = sum(layer_totals.values())
                done_bytes = sum(layer_completed.values())

                now = time.time()
                dt_sample = now - last_sample_time
                if dt_sample >= 0.30 and done_bytes >= last_sample_bytes:
                    inst_speed = (done_bytes - last_sample_bytes) / dt_sample
                    smooth_speed = inst_speed if smooth_speed == 0 else (0.35 * inst_speed + 0.65 * smooth_speed)
                    last_sample_time = now
                    last_sample_bytes = done_bytes

                if now - last_render >= 0.25:
                    frame_idx += 1
                    eta = ((total_bytes - done_bytes) / smooth_speed) if (total_bytes > 0 and smooth_speed > 0) else None
                    render_reactor_hud(
                        frame_idx=frame_idx,
                        status_badge="PULLING",
                        target_name=f"Model: {model_name}",
                        downloaded_bytes=done_bytes,
                        total_bytes=total_bytes,
                        speed_bps=smooth_speed,
                        eta_seconds=eta,
                        step_states=("done", "done", "done", "active"),
                        first_frame=False,
                    )
                    last_render = now

                if event.get("status") == "success":
                    break

        final_total = max(sum(layer_totals.values()), 1)
        render_reactor_hud(
            frame_idx=frame_idx + 1,
            status_badge="READY",
            target_name=f"Model: {model_name}",
            downloaded_bytes=final_total,
            total_bytes=final_total,
            speed_bps=smooth_speed,
            eta_seconds=0,
            step_states=("done", "done", "done", "done"),
            first_frame=False,
        )
        time.sleep(1.0)
        return True, ""
    except Exception as err:
        return False, str(err)
    finally:
        _restore_cursor()


def _ask_yes_no(prompt_text):
    ans = input(prompt_text).strip().lower()
    return ans in {"y", "yes"}


def run_permission_ai_setup():
    """Guided, permission-first Ollama setup wizard with Heavy Containment Reactor HUD."""
    while True:
        ollama_bin = find_ollama_executable()
        running, models = get_ollama_models()
        if not running and ollama_bin:
            if try_wake_ollama():
                running, models = get_ollama_models()

        if running and models:
            return True

        if not running and not ollama_bin:
            clear_screen()
            print(_c("╔═════════════════════════════════════════════════════════════════╗", "96"))
            print(_c("║  ◈ OFFLINE AI SETUP  —  PERMISSION & LINK VERIFICATION          ║", "1;97"))
            print(_c("╚═════════════════════════════════════════════════════════════════╝", "96"))
            print("  Offline AI engine (Ollama) is not installed on this computer.")

            if os.name != "nt":
                print("\n  Automatic installer download is built for Windows.")
                print("  On macOS/Linux, install Ollama from: https://ollama.com")
                print(f"  Then run: ollama pull {DEFAULT_OLLAMA_MODEL}\n")
                choice = input("  Press Enter to re-check, or type 'B' to go back: ").strip().upper()
                if choice == "B":
                    return False
                continue

            print("\n  Checking internet connection & verifying Ollama download link...")
            ok, final_url, size_bytes, status_msg = probe_download_link(OLLAMA_WIN_INSTALLER_URL)

            if not ok:
                print(f"  Internet / Link Status : {_c('[UNAVAILABLE]', '1;91')} ({status_msg})")
                print(f"  Checked URL            : {OLLAMA_WIN_INSTALLER_URL}\n")
                print("  Please check your internet connection and try again.")
                choice = input("\n  Press Enter to retry link check, or type 'B' to go back: ").strip().upper()
                if choice == "B":
                    return False
                continue

            size_label = _format_bytes(size_bytes) if size_bytes > 0 else "Unknown size"
            print(f"  Internet / Link Status : {_c(f'[{status_msg}]', '1;92')}")
            print(f"  Download Link          : {OLLAMA_WIN_INSTALLER_URL}")
            print(f"  Installer File Size    : {_c(size_label, '1;93')}")
            print(f"  AI Model (next step)   : {DEFAULT_OLLAMA_MODEL} ({DEFAULT_MODEL_SIZE_LABEL} - will ask separately)\n")
            print("-" * 67)

            if not _ask_yes_no(f"  Should I download Ollama ({size_label}) now? (Y/N): "):
                print("\n  [i] Download cancelled by user. Returning to menu...")
                time.sleep(0.9)
                return False

            installer_path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
            dl_ok, dl_err = download_file_with_reactor(
                final_url,
                installer_path,
                display_name="OllamaSetup.exe",
                expected_size=size_bytes,
            )
            if not dl_ok:
                print(f"\n  [!] Download failed: {dl_err}")
                choice = input("  Press Enter to retry, or type 'B' to go back: ").strip().upper()
                if choice == "B":
                    return False
                continue

            print(_c("\n  [✓] Download verified and complete!", "1;92"))
            if not _ask_yes_no("  Should I install Ollama on your computer now? (Y/N): "):
                print(f"\n  [i] Installation skipped. Installer saved at: {installer_path}")
                input("  Press Enter to return to menu...")
                return False

            inst_ok, inst_err = install_ollama_with_reactor(installer_path)
            try:
                if os.path.exists(installer_path):
                    os.remove(installer_path)
            except OSError:
                pass

            if not inst_ok:
                print(f"\n  [!] Installation issue: {inst_err}")
                choice = input("  Press Enter to retry, or type 'B' to go back: ").strip().upper()
                if choice == "B":
                    return False
                continue

            running, models = get_ollama_models()
            if not running:
                try_wake_ollama()
                running, models = get_ollama_models()

        if running and not models:
            clear_screen()
            print(_c("╔═════════════════════════════════════════════════════════════════╗", "96"))
            print(_c("║  ◈ OFFLINE AI MODEL  —  DOWNLOAD PERMISSION                     ║", "1;97"))
            print(_c("╚═════════════════════════════════════════════════════════════════╝", "96"))
            print(f"  Ollama Engine Status   : {_c('[RUNNING]', '1;92')}")
            print(f"  Recommended AI Model   : {_c(DEFAULT_OLLAMA_MODEL, '1;97')}")
            print(f"  Estimated Download Size: {_c(DEFAULT_MODEL_SIZE_LABEL, '1;93')}")
            print("  Note                   : Needed once; works 100% offline afterward.\n")
            print("-" * 67)

            if not _ask_yes_no(
                f"  Should I download the offline AI model '{DEFAULT_OLLAMA_MODEL}' ({DEFAULT_MODEL_SIZE_LABEL}) now? (Y/N): "
            ):
                print("\n  [i] Model download cancelled by user. Returning to menu...")
                time.sleep(0.9)
                return False

            pull_ok, pull_err = pull_ollama_model_with_reactor(DEFAULT_OLLAMA_MODEL)
            if not pull_ok:
                print(f"\n  [!] Model download failed: {pull_err}")
                choice = input("  Press Enter to retry, or type 'B' to go back: ").strip().upper()
                if choice == "B":
                    return False
                continue

            return True


# --- Offline AI Companion: Intent-Gated Tools + Persistent Chat Memory ---

TOOL_ACQUIRE_SYSTEM = {
    "type": "function",
    "function": {
        "name": "acquire_system_info",
        "description": (
            "Acquires live PC hardware and OS system information (CPU, RAM, GPU, OS, storage, motherboard/BIOS, "
            "network, battery, or running processes) ONLY when the user explicitly asks to acquire or check system info."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": [
                        "overview",
                        "cpu_memory",
                        "gpu_display",
                        "storage_health",
                        "motherboard_bios",
                        "network",
                        "battery_power",
                        "processes",
                    ],
                    "description": "Subsystem to acquire (default 'overview').",
                },
                "requires_admin": {
                    "type": "boolean",
                    "description": "True if Administrator privileges are required or requested.",
                },
            },
            "required": ["topic"],
        },
    },
}

TOOL_SCAN_PROGRAMS = {
    "type": "function",
    "function": {
        "name": "scan_installed_programs",
        "description": (
            "Scans the user's computer (Windows Registry, App Paths & Start Menu) to build a real, detailed list "
            "of installed programs and their executable file locations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filter_keyword": {
                    "type": "string",
                    "description": "Optional keyword to filter programs (empty string to list all).",
                },
            },
            "required": [],
        },
    },
}

TOOL_LAUNCH_PROGRAM = {
    "type": "function",
    "function": {
        "name": "launch_program",
        "description": "Launches/runs a specific installed program on the user's computer.",
        "parameters": {
            "type": "object",
            "properties": {
                "program_name": {
                    "type": "string",
                    "description": "Name of the installed program to run.",
                },
            },
            "required": ["program_name"],
        },
    },
}

TOOL_TERMINATE_PROCESS = {
    "type": "function",
    "function": {
        "name": "terminate_process",
        "description": "Ends/closes a running program or process on the user's computer.",
        "parameters": {
            "type": "object",
            "properties": {
                "process_name": {
                    "type": "string",
                    "description": "Name of the running process or window to close.",
                },
                "pid": {
                    "type": "integer",
                    "description": "Optional numeric Process ID (PID).",
                },
            },
            "required": [],
        },
    },
}

TOOL_SCHEDULE_REMINDER = {
    "type": "function",
    "function": {
        "name": "schedule_reminder",
        "description": "Saves a task or reminder into the user's reminder schedule.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Description of the task or reminder.",
                },
                "when": {
                    "type": "string",
                    "description": "Optional date and/or time phrase from the user.",
                },
            },
            "required": ["task"],
        },
    },
}

TOOL_COMPLETE_REMINDER = {
    "type": "function",
    "function": {
        "name": "complete_reminder",
        "description": "Marks an existing pending reminder or task as completed/done.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_keyword": {
                    "type": "string",
                    "description": "Keyword matching the pending task to mark as done.",
                },
            },
            "required": ["task_keyword"],
        },
    },
}

TOOL_WRITE_JOURNAL = {
    "type": "function",
    "function": {
        "name": "write_journal_entry",
        "description": "Writes and saves a new journal entry under the 'Auto Entry' category in the user's diary.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the journal entry, or 'auto' to use today's date.",
                },
                "content": {
                    "type": "string",
                    "description": "Full text content of the journal entry.",
                },
            },
            "required": ["content"],
        },
    },
}


def _has_negation_guard(low_msg, action_words_pat):
    """Returns True if the user is telling the AI NOT to perform the action."""
    return bool(
        re.search(
            rf"\b(?:don't|do not|never|not|stop|shouldn't|should not|only when)\b.{{0,30}}\b(?:{action_words_pat})\b",
            low_msg,
        )
    )


def has_explicit_journal_intent(user_msg):
    """True ONLY when the user explicitly commands the AI to write/save/log a journal or diary entry."""
    low = user_msg.strip().lower()
    if _has_negation_guard(low, r"save|write|record|log|make|add|create|keep|store"):
        return False
    patterns = [
        r"\b(?:write|save|add|create|make|record|log|put|keep|store)\b.{0,30}\b(?:journal|diary)(?:\s+entry)?\b",
        r"\b(?:journal|diary)\s+(?:this|that|it|entry)\b",
        r"\b(?:save|record|log)\s+(?:this|that|it)\s+(?:in|into|to|as)\s+(?:my\s+)?(?:journal|diary)\b",
        r"\bnew\s+(?:journal|diary)(?:\s+entry)?\b",
    ]
    return any(re.search(p, low) for p in patterns)


def has_explicit_reminder_intent(user_msg):
    """True ONLY when the user explicitly commands the AI to add/schedule or complete a reminder/task."""
    low = user_msg.strip().lower()
    if _has_negation_guard(low, r"remind|save|add|set|schedule|make|create"):
        return False
    patterns = [
        r"\bremind\s+me\b",
        r"\b(?:add|set|create|make|schedule|put|save)\b.{0,25}\b(?:reminder|task|alarm|event|meeting|appointment)\b",
        r"\b(?:mark|complete|finish|done|check\s+off)\b.{0,25}\b(?:reminder|task|done|completed)\b",
    ]
    return any(re.search(p, low) for p in patterns)


def select_tools_for_message(user_msg):
    """
    Exposes action tools ONLY when the user's message explicitly requests an action.
    Normal chat messages receive NO tools so the AI simply talks naturally without side effects.
    """
    low = user_msg.strip().lower()
    selected = []

    if has_explicit_journal_intent(user_msg):
        selected.append(TOOL_WRITE_JOURNAL)

    if has_explicit_reminder_intent(user_msg):
        selected.extend([TOOL_SCHEDULE_REMINDER, TOOL_COMPLETE_REMINDER])

    if re.search(
        r"\b(?:system\s+info|pc\s+specs|hardware|configuration|config|cpu|processor|ram|memory|gpu|graphics|motherboard|bios|storage|disk|battery|network|ip\s+address|admin|administrator|specs)\b",
        low,
    ):
        selected.append(TOOL_ACQUIRE_SYSTEM)

    if re.search(r"\b(?:installed|programs|programmes|software|applications|apps)\b", low):
        selected.append(TOOL_SCAN_PROGRAMS)

    if re.search(r"\b(?:open|run|launch|start)\b", low):
        selected.append(TOOL_LAUNCH_PROGRAM)

    if re.search(r"\b(?:end|kill|close|stop|terminate|quit)\b", low):
        selected.append(TOOL_TERMINATE_PROCESS)

    return selected


def execute_ai_tool_call(tool_name, args, entries, reminders, user_query=""):
    """Executes real Python actions requested by the AI, guarded by explicit user intent."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if tool_name == "write_journal_entry":
        if not has_explicit_journal_intent(user_query):
            return {
                "status": "blocked",
                "message": "User did not ask to save a journal entry. Just reply conversationally.",
            }
        title = str(args.get("title", "auto")).strip()
        content = str(args.get("content", "")).strip()
        if not content:
            return {"status": "error", "message": "Journal content was empty."}

        rec = create_journal_entry_record(entries, title, content, source="auto")
        print(_c(f"\n  [+] AUTO JOURNAL ENTRY SAVED: [{rec['date']}] '{rec['title']}'", "1;92"))
        return {
            "status": "saved",
            "category": "Auto Entry",
            "date": rec["date"],
            "title": rec["title"],
        }

    if tool_name == "schedule_reminder":
        if not has_explicit_reminder_intent(user_query):
            return {
                "status": "blocked",
                "message": "User did not ask to add a reminder. Just reply conversationally.",
            }
        task = str(args.get("task", "")).strip()
        when = str(args.get("when", "")).strip()
        combined = f"{task} {when}".strip()
        if not combined:
            return {"status": "error", "message": "No task text provided."}

        rec = parse_and_create_reminder(combined, reminders, entries)
        day_name = datetime.strptime(rec["due_date"], "%Y-%m-%d").strftime("%a")
        time_12 = format_12h_time(rec["due_time"])
        print(
            _c(
                f"\n  [+] REMINDER SAVED: '{rec['task']}' -> {rec['due_date']} ({day_name}) at {time_12} [{rec['schedule_note']}]",
                "1;92",
            )
        )
        return {
            "status": "saved",
            "task": rec["task"],
            "due_date": rec["due_date"],
            "day": day_name,
            "due_time": time_12,
            "schedule_note": rec["schedule_note"],
        }

    if tool_name == "complete_reminder":
        if not has_explicit_reminder_intent(user_query):
            return {"status": "blocked", "message": "User did not ask to modify reminders."}
        kw = str(args.get("task_keyword", "")).strip().lower()
        pending = get_sorted_pending_reminders(reminders)
        matched = [r for r in pending if kw and kw in r["task"].lower()]
        if not matched:
            return {"status": "not_found", "message": f"No pending reminder matched '{kw}'."}

        done_ts = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        completed_tasks = []
        for r in matched:
            r["done"] = True
            r["completed_at"] = done_ts
            completed_tasks.append(r["task"])
        save_data(entries, reminders=reminders)
        print(_c(f"\n  [+] REMINDER COMPLETED: {', '.join(completed_tasks)}", "1;92"))
        return {"status": "completed", "tasks": completed_tasks}

    if tool_name == "acquire_system_info":
        topic = str(args.get("topic", "overview")).strip()
        req_admin = bool(args.get("requires_admin", False))
        print(_c(f"\n  [i] Acquiring system info ({topic})...", "96"))
        return acquire_system_info_on_demand(topic, req_admin, entries, user_query=user_query)

    if tool_name == "scan_installed_programs":
        kw = str(args.get("filter_keyword", "")).strip()
        return scan_installed_programs_on_demand(filter_keyword=kw)

    if tool_name == "launch_program":
        prog = str(args.get("program_name", "")).strip()
        return launch_installed_program(prog)

    if tool_name == "terminate_process":
        pname = str(args.get("process_name", "")).strip()
        pid = args.get("pid")
        return terminate_running_process(process_name=pname, pid=pid, entries=entries, user_query=user_query)

    return {"status": "error", "message": f"Unknown tool '{tool_name}'."}


def select_relevant_entries(entries, user_query="", max_chars=12000):
    if not entries:
        return []

    stopwords = {
        "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
        "did", "does", "do", "have", "has", "had", "was", "were", "is", "are",
        "the", "and", "for", "with", "about", "from", "into", "that", "this",
        "my", "me", "i", "in", "on", "at", "to", "of", "a", "an", "diary", "entry"
    }
    keywords = {
        w for w in re.findall(r"\w+", user_query.lower())
        if len(w) >= 3 and w not in stopwords
    }

    scored = []
    for idx, entry in enumerate(entries):
        haystack = f"{entry['date']} {entry['title']} {entry['content']}".lower()
        hits = sum(1 for kw in keywords if kw in haystack)
        scored.append((hits, -idx, entry))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    selected = []
    total_chars = 0
    for _, _, entry in scored:
        block_len = len(entry["date"]) + len(entry["title"]) + len(entry["content"]) + 90
        if selected and total_chars + block_len > max_chars:
            continue
        selected.append(entry)
        total_chars += block_len

    return selected


def build_diary_system_prompt(entries, reminders, user_query=""):
    now_str = datetime.now().strftime("%Y-%m-%d (%A) %I:%M %p")
    pending = get_sorted_pending_reminders(reminders)

    if SYSTEM_PROFILE:
        system_block = (
            f"Acquired At: {SYSTEM_PROFILE.get('acquired_at')} | "
            f"PC: {SYSTEM_PROFILE.get('computer_name')} | "
            f"Privilege: {SYSTEM_PROFILE.get('privilege_level')} | "
            f"OS: {SYSTEM_PROFILE.get('os')} | "
            f"CPU: {SYSTEM_PROFILE.get('cpu')} | "
            f"RAM: {SYSTEM_PROFILE.get('ram')} | "
            f"GPU: {SYSTEM_PROFILE.get('gpu')} | "
            f"Storage: {SYSTEM_PROFILE.get('storage')} | "
            f"Ollama: {SYSTEM_PROFILE.get('ollama_accel')}"
        )
    else:
        system_block = "Not acquired yet."

    if INSTALLED_PROGRAMS_CACHE:
        prog_lines = [
            f"- {p['name']} ({p['exe_path'] or p['install_location'] or 'No path'})"
            for p in INSTALLED_PROGRAMS_CACHE[:100]
        ]
        programs_block = f"{len(INSTALLED_PROGRAMS_CACHE)} verified programs:\n" + "\n".join(prog_lines)
    else:
        programs_block = "Not scanned yet."

    rem_lines = []
    for r in pending:
        rem_lines.append(
            f"- [PENDING] {r['due_date']} at {format_12h_time(r['due_time'])}: {r['task']}"
        )
    for r in [x for x in reminders if x.get("done")][-10:]:
        rem_lines.append(f"- [COMPLETED] {r['due_date']}: {r['task']}")
    reminders_block = "\n".join(rem_lines) if rem_lines else "None"

    if not entries:
        diary_dump = "None"
    else:
        chosen = select_relevant_entries(entries, user_query=user_query)
        formatted_entries = []
        for e in chosen:
            cat_tag = "Auto Entry" if get_entry_source(e) == "auto" else "Manual Entry"
            formatted_entries.append(
                f"[{e['date']} | {cat_tag} | {e['title']}]: {e['content']}"
            )
        diary_dump = "\n".join(formatted_entries)

    return (
        f"You are the user's friendly, helpful personal offline assistant. Current date and time: {now_str}.\n"
        "IMPORTANT BEHAVIOR RULES:\n"
        "1. Talk naturally and conversationally with the user. Do NOT dump raw context blocks or headers.\n"
        "2. Casual conversation is automatically remembered in chat history — NEVER create a diary/journal entry or reminder unless the user explicitly asks you to.\n"
        "3. Never guess or invent installed programs, file paths, or PC hardware specs.\n"
        "4. Only reference the user's diary entries, reminders, system info, or installed programs below when relevant to what the user asks.\n\n"
        f"Acquired System Info: {system_block}\n"
        f"Scanned Programs: {programs_block}\n"
        f"Reminders:\n{reminders_block}\n"
        f"Saved Diary Entries ({len(entries)} total):\n{diary_dump}"
    )


def build_recent_chat_context(chat_history, max_messages=30, max_chars=10000):
    """Loads recent saved user/assistant messages into Ollama's conversation history."""
    if not chat_history:
        return []

    collected = []
    total_chars = 0
    for item in reversed(chat_history):
        role = item.get("role")
        content = item.get("content", "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if collected and (len(collected) >= max_messages or total_chars + len(content) > max_chars):
            break
        collected.append({"role": role, "content": content})
        total_chars += len(content)

    collected.reverse()
    return collected


def _post_ollama_chat(payload_dict, timeout=120):
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload_dict).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def stream_ollama_reply(model, messages):
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {
            "num_ctx": 8192,
            "temperature": 0.3,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    full_reply = []
    print("\nAI: ", end="", flush=True)
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            chunk = json.loads(line)
            token = chunk.get("message", {}).get("content", "")
            if token:
                print(token, end="", flush=True)
                full_reply.append(token)
            if chunk.get("done"):
                break
    print("\n")
    return "".join(full_reply)


def run_ai_turn_with_actions(model, api_messages, entries, reminders, user_query=""):
    """
    Passes tools ONLY when user_query explicitly requests an action.
    Normal conversation streams directly with zero risk of accidental diary/reminder writes.
    """
    active_tools = select_tools_for_message(user_query)

    if not active_tools:
        return stream_ollama_reply(model, api_messages)

    base_payload = {
        "model": model,
        "messages": api_messages,
        "tools": active_tools,
        "stream": False,
        "options": {
            "num_ctx": 8192,
            "temperature": 0.2,
        },
    }

    try:
        resp_data = _post_ollama_chat(base_payload)
    except urllib.error.HTTPError as http_err:
        if http_err.code == 400:
            return stream_ollama_reply(model, api_messages)
        raise

    assistant_msg = resp_data.get("message", {})
    tool_calls = assistant_msg.get("tool_calls") or []

    if tool_calls:
        follow_up_messages = list(api_messages)
        follow_up_messages.append(assistant_msg)

        for tc in tool_calls:
            fn_block = tc.get("function", {})
            fn_name = fn_block.get("name", "")
            fn_args = fn_block.get("arguments", {})
            tool_result = execute_ai_tool_call(fn_name, fn_args, entries, reminders, user_query=user_query)
            follow_up_messages.append({
                "role": "tool",
                "content": json.dumps(tool_result, ensure_ascii=False),
            })

        return stream_ollama_reply(model, follow_up_messages)

    reply_text = (assistant_msg.get("content") or "").strip()
    print(f"\nAI: {reply_text}\n")
    return reply_text


def save_chat_to_diary(entries, chat_history):
    if not chat_history:
        print("[!] Nothing in conversation history to save yet.\n")
        return

    now = datetime.now()
    auto_title = f"{now.strftime('%Y-%m-%d')} (AI Chat)"
    title = input("Press Enter to generate auto Title or Give a Title: ").strip() or auto_title

    transcript = []
    for m in chat_history[-40:]:
        speaker = "You" if m["role"] == "user" else "AI"
        ts = m.get("timestamp", "")
        header = f"[{ts}] {speaker}" if ts else speaker
        transcript.append(f"{header}: {m['content']}")

    rec = create_journal_entry_record(entries, title, "\n\n".join(transcript), source="auto")
    print(f"[+] Conversation exported under Auto Entry as '{rec['title']}'.\n")


def talk_to_ai(entries, reminders, chat_history, auto_resume_query=""):
    if not run_permission_ai_setup():
        return

    _, models = get_ollama_models()
    if not models:
        return

    active_model = models[0]
    if len(models) > 1 and not auto_resume_query:
        clear_screen()
        print("=== SELECT OFFLINE AI MODEL ===")
        for idx, name in enumerate(models, start=1):
            print(f"  {idx}. {name}")
        sel = input(f"\nChoose model (1-{len(models)}, Enter for {active_model}): ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(models):
            active_model = models[int(sel) - 1]

    clear_screen()
    pending_count = len(get_sorted_pending_reminders(reminders))
    loaded_history = build_recent_chat_context(chat_history)
    priv_badge = _c("[ADMIN MODE]", "1;92") if is_user_admin() else _c("[STANDARD USER]", "93")

    print("=================================================================")
    print(f"  OFFLINE DIARY AI  ({active_model})   {priv_badge}")
    print("=================================================================")
    print(f"  Memory Loaded : {len(entries)} entries | {pending_count} pending tasks | {len(loaded_history)} past chat msgs")
    print("  Behavior      : All chats saved automatically; journals/reminders saved only when asked")
    print("  Commands      : '/save' export to Auto Entry | '/clear_chat' wipe | '/exit' menu")
    print("-----------------------------------------------------------------")

    if loaded_history:
        print(_c("  Recent Conversation Context:", "90"))
        for past in loaded_history[-4:]:
            who = "You" if past["role"] == "user" else "AI "
            snippet = past["content"].replace("\n", " ")
            if len(snippet) > 78:
                snippet = snippet[:75] + "..."
            print(_c(f"    {who}: {snippet}", "90"))
        print("-----------------------------------------------------------------\n")
    else:
        print()

    queued_query = auto_resume_query.strip()

    while True:
        if queued_query:
            user_msg = queued_query
            queued_query = ""
            print(f"You (resumed with Admin access): {user_msg}")
        else:
            try:
                user_msg = input("You: ").strip()
            except EOFError:
                break

        if not user_msg:
            continue
        if user_msg.lower() in {"/exit", "exit", "quit", "/back"}:
            break
        if user_msg.lower() == "/save":
            save_chat_to_diary(entries, chat_history)
            continue
        if user_msg.lower() == "/clear_chat":
            confirm = input("Wipe all saved AI chat history permanently? (y/N): ").strip().lower()
            if confirm == "y":
                chat_history.clear()
                save_data(entries, chat_history=chat_history)
                print("[+] Saved AI chat history cleared.\n")
            continue

        api_messages = [{"role": "system", "content": build_diary_system_prompt(entries, reminders, user_query=user_msg)}]
        api_messages.extend(build_recent_chat_context(chat_history))
        api_messages.append({"role": "user", "content": user_msg})

        try:
            reply = run_ai_turn_with_actions(active_model, api_messages, entries, reminders, user_query=user_msg)
            now_ts = datetime.now().strftime("%Y-%m-%d %I:%M %p")
            chat_history.append({"timestamp": now_ts, "role": "user", "content": user_msg})
            chat_history.append({"timestamp": now_ts, "role": "assistant", "content": reply})
            save_data(entries, chat_history=chat_history)
        except Exception as err:
            print(f"\n[!] AI connection interrupted: {err}\n")


def main():
    # Starts clean: NO system info or installed program scan is performed at startup
    load_data()

    pending_admin_q = DIARY_CONFIG.pop("pending_admin_query", "")
    if pending_admin_q:
        save_data(DIARY_ENTRIES, config=DIARY_CONFIG)
        talk_to_ai(DIARY_ENTRIES, DIARY_REMINDERS, DIARY_CHAT_HISTORY, auto_resume_query=pending_admin_q)

    show_startup_reminders(DIARY_REMINDERS, DIARY_ENTRIES)

    while True:
        clear_screen()
        editor_name, _ = get_active_editor()
        pending_count = len(get_sorted_pending_reminders(DIARY_REMINDERS))
        manual_count = sum(1 for e in DIARY_ENTRIES if get_entry_source(e) == "manual")
        auto_count = sum(1 for e in DIARY_ENTRIES if get_entry_source(e) == "auto")
        admin_tag = _c(" [ADMIN]", "1;92") if is_user_admin() else ""

        print("==================================================")
        print(f"    PERSONAL DIARY{admin_tag}")
        print(f"    Manual: {manual_count}  |  Auto (AI): {auto_count}  |  Total: {len(DIARY_ENTRIES)}")
        print("==================================================")
        print("  1. Write Manual Entry  (or 'RRR' for Reminder)")
        print("  2. Read / Manage Entries  (Manual / Auto)")
        print("  3. Search Entries")
        print(f"  4. Talk to Offline AI  [{len(DIARY_CHAT_HISTORY)} msgs]")
        print(f"  5. Reminders & Tasks   [{pending_count} pending]")
        print(f"  6. Choose Editor App   [{editor_name}]")
        print("  7. Exit")
        print("--------------------------------------------------")

        choice = input("Select an option (1-7): ").strip()

        if choice == "1":
            add_entry(DIARY_ENTRIES, DIARY_REMINDERS)
        elif choice == "2":
            browse_entries_by_category(DIARY_ENTRIES)
        elif choice == "3":
            search_entries(DIARY_ENTRIES)
        elif choice == "4":
            talk_to_ai(DIARY_ENTRIES, DIARY_REMINDERS, DIARY_CHAT_HISTORY)
        elif choice == "5":
            manage_reminders_menu(DIARY_REMINDERS, DIARY_ENTRIES)
        elif choice == "6":
            configure_external_editor(DIARY_ENTRIES)
        elif choice == "7":
            print("Goodbye.")
            break


# === DIARY DATA START ===
DIARY_CONFIG = {'editor_cmd': None, 'editor_name': 'Notepad'}
DIARY_REMINDERS = []
DIARY_ENTRIES = [   {   'content': 'Nifty Coders pvt details',
        'date': '2026-11-26 05:59 PM',
        'id': '20261126175912670178',
        'source': 'auto',
        'title': 'Nifty Coders pvt details'}]
DIARY_CHAT_HISTORY = []
# === DIARY DATA END ===


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _restore_cursor()
        print("\nExited.")