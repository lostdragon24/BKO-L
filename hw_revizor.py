#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сбор инвентаризационной информации в Linux в формате, близком к AIDA64.
Требует: lscpu, dmidecode, lsblk, ip, lspci (обычно предустановлены).
Для полного доступа к DMI и SMART запускать с sudo.
"""

import subprocess
import platform
import re
import os

def run_cmd(cmd):
    """Выполняет команду и возвращает stdout (первую строку, если не указано иное)."""
    try:
        # Используем shell=True для простоты парсинга, но в продакшене лучше избегать
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True)
        return out.strip()
    except subprocess.CalledProcessError:
        return ""

def run_cmd_lines(cmd):
    """Возвращает список строк вывода команды."""
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True)
        return [line.strip() for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []

def write_section(f, name):
    f.write(f"\n\n[{name}]\n\n")

def main():
    hostname = platform.node()
    filename = f"{hostname}.ini"

    with open(filename, 'w', encoding='utf-8') as f:

        # ============================================================
        # [Суммарная информация]
        # ============================================================
        write_section(f, "Суммарная информация")
        f.write(f"Компьютер|Имя компьютера= {hostname}\n")

        #Компьютер|Тип компьютера
        f.write(f"Компьютер|Тип компьютера= {run_cmd('dmidecode -s chassis-type 2>/dev/null')}\n")

        # --- Операционная система ---
        os_name = run_cmd("grep '^PRETTY_NAME' /etc/os-release | cut -d'\"' -f2")
        kernel = platform.release()
        f.write(f"Компьютер|Операционная система= {os_name}\n")
        f.write(f"Компьютер|Версия ядра= {kernel}\n")

        # --- Процессор (кратко) ---
        # Используем LC_ALL=C для стабильного парсинга на любом языке
        cpu_model = run_cmd("LC_ALL=C lscpu | grep '^Model name:' | cut -d':' -f2-").strip()
        f.write(f"Системная плата|Тип ЦП= {cpu_model}\n")

        # --- Системная память (суммарно) ---
        # Пытаемся через dmidecode (точнее, как в AIDA64), иначе через /proc/meminfo
        mem_total = 0
        mem_out = run_cmd("dmidecode -t memory 2>/dev/null | grep 'Size:' | cut -d':' -f2")
        for line in mem_out.splitlines():
            line = line.strip()
            if not line or 'No Module' in line or 'None' in line:
                continue
            m = re.search(r'(\d+)\s*(GB|MB)', line, re.IGNORECASE)
            if m:
                val, unit = int(m.group(1)), m.group(2).upper()
                mem_total += val * 1024 if unit == 'GB' else val
        if mem_total == 0:
            # Fallback: /proc/meminfo
            mem_info = run_cmd("grep MemTotal /proc/meminfo")
            mem_kb = int(re.search(r'\d+', mem_info).group()) if mem_info else 0
            mem_total = mem_kb // 1024
        f.write(f"Системная плата|Системная память= {mem_total // 1024} ГБ\n")

        # --- Видеоадаптер (кратко) ---
        gpu = run_cmd("lspci | grep -i vga | cut -d':' -f3-").strip()
        f.write(f"Отображение|Видеоадаптер1= {gpu}\n")

        # --- Сеть (первый IP) ---
        ip_addr = run_cmd("ip -4 addr show | grep inet | grep -v 127.0.0.1 | head -1 | awk '{print $2}'")
        f.write(f"Сеть|Первичный адрес IP= {ip_addr}\n")
        mac = run_cmd("ip link show | grep -v lo | grep 'link/ether' | head -1 | awk '{print $2}'")
        f.write(f"Сеть|Первичный адрес MAC= {mac}\n")

        # --- DMI (Сводка) ---
        f.write(f"DMI|DMI поставщик BIOS= {run_cmd('dmidecode -s bios-vendor 2>/dev/null')}\n")
        f.write(f"DMI|DMI версия BIOS= {run_cmd('dmidecode -s bios-version 2>/dev/null')}\n")
        f.write(f"DMI|DMI производитель системы= {run_cmd('dmidecode -s system-manufacturer 2>/dev/null')}\n")
        f.write(f"DMI|DMI система= {run_cmd('dmidecode -s system-product-name 2>/dev/null')}\n")
        f.write(f"DMI|DMI серийный номер системной платы= {run_cmd('dmidecode -s baseboard-serial-number 2>/dev/null')}\n")

        #Системная плата|Системная плата
        f.write(f"Системная плата|Системная плата= {run_cmd('cat /sys/devices/virtual/dmi/id/board_name')}\n")

        lsblk_json = run_cmd("lsblk -J -b -d -o NAME,MODEL,SIZE,SERIAL,TRAN")
        import json
        try:
            data = json.loads(lsblk_json)
            idx = 1
            for dev in data.get('blockdevices', []):
                name = dev.get('name', '')
                model = dev.get('model', 'N/A')
                size_bytes = dev.get('size', 0)
                serial = dev.get('serial', 'N/A')
                tran = dev.get('tran', 'N/A')
                # Пропускаем loop-устройства и ram
                if name.startswith('loop') or name.startswith('ram'):
                    continue
                size_gb = round(int(size_bytes) / (1024**3)) if size_bytes else 0
                f.write(f"Хранение данных|Дисковый накопитель{idx}= {model}\n")
                idx += 1
        except Exception:
            f.write("Хранение данных Windows|Ошибка= Не удалось прочитать lsblk\n")


        #[Системная плата]
        write_section(f, "Системная плата")

        f.write(f"Свойства системной платы|Системная плата= {run_cmd('cat /sys/devices/virtual/dmi/id/board_name')}\n")
        f.write(f"Производитель системной платы|Фирма= {run_cmd('cat /sys/devices/virtual/dmi/id/board_vendor')}\n")

        #[Видео Windows]
        write_section(f, "Видео Windows")
        gpu = run_cmd("lspci | grep -i vga | cut -d':' -f3-").strip()

        f.write(f"Видео Windows1|Свойства видеоадаптера|Описание устройства= {gpu}\n")

        # ============================================================
        # [SPD (Модули памяти)]
        # ============================================================
        write_section(f, "SPD")
        # Парсим блоки Memory Device (Type 17) из dmidecode
        dmi_out = subprocess.run(['dmidecode', '-t', '17'], capture_output=True, text=True).stdout
        devices = dmi_out.split('Memory Device')
        spd_idx = 1

        for dev in devices[1:]: # Первый элемент — заголовок dmidecode
            lines = [l.strip() for l in dev.splitlines() if l.strip()]
            dev_data = {}
            for line in lines:
                if ':' in line:
                    k, v = line.split(':', 1)
                    dev_data[k.strip()] = v.strip()

            size = dev_data.get('Size', '')
            if not size or 'No Module' in size or 'None' in size:
                continue

            f.write(f"SPD{spd_idx}|Свойства модуля памяти|Имя разъема= {dev_data.get('Locator', 'N/A')}\n")
            f.write(f"SPD{spd_idx}|Свойства модуля памяти|Размер модуля= {size}\n")
            f.write(f"SPD{spd_idx}|Свойства модуля памяти|Тип памяти= {dev_data.get('Type', 'N/A')}\n")
            f.write(f"SPD{spd_idx}|Свойства модуля памяти|Скорость= {dev_data.get('Speed', 'N/A')}\n")
            f.write(f"SPD{spd_idx}|Свойства модуля памяти|Производитель= {dev_data.get('Manufacturer', 'N/A')}\n")
            f.write(f"SPD{spd_idx}|Свойства модуля памяти|Серийный номер= {dev_data.get('Serial Number', 'N/A')}\n")
            f.write(f"SPD{spd_idx}|Свойства модуля памяти|Ширина данных= {dev_data.get('Data Width', 'N/A')}\n")
            spd_idx += 1


        # ============================================================
        # [DMI] (Подробно)
        # ============================================================
        write_section(f, "DMI")
        # Процессор (используем lscpu для деталей, доступных без root)
        cpu_vendor = run_cmd("LC_ALL=C lscpu | grep '^Vendor ID:' | cut -d':' -f2-").strip()
        cpu_socket = run_cmd("LC_ALL=C lscpu | grep '^Socket(s):' | cut -d':' -f2-").strip()
        cpu_mhz_max = run_cmd("LC_ALL=C lscpu | grep '^CPU max MHz:' | cut -d':' -f2-").strip()
        cpu_mhz_cur = run_cmd("LC_ALL=C lscpu | grep '^CPU MHz:' | cut -d':' -f2-").strip()
        
        f.write(f"Процессоры1|Свойства процессора|Производитель= {cpu_vendor}\n")
        f.write(f"Процессоры1|Свойства процессора|Версия= {cpu_model}\n")
        f.write(f"Процессоры1|Свойства процессора|Тип разъёма= {cpu_socket}\n")
        f.write(f"Процессоры1|Свойства процессора|Максимальная частота= {cpu_mhz_max} МГц\n")
        f.write(f"Процессоры1|Свойства процессора|Текущая частота= {cpu_mhz_cur} МГц\n")

        # Кэш (L1d, L1i, L2, L3)
        for cache_name, grep_key in [("L1 кэш данных", "L1d cache"), ("L1 кэш инструкций", "L1i cache"), ("L2 кэш", "L2 cache"), ("L3 кэш", "L3 cache")]:
            val = run_cmd(f"LC_ALL=C lscpu | grep '{grep_key}' | cut -d':' -f2-").strip()
            if val:
                f.write(f"Кэш-память|Свойства кэша|{cache_name}= {val}\n")


        # ============================================================
        # [Сеть Windows] (эмуляция секции)
        # ============================================================
        write_section(f, "Сеть Windows")
        # Получаем список интерфейсов, исключая loopback
        ifaces = run_cmd_lines("ip -o link show | awk -F': ' '{print $2}'")
        idx = 1
        for iface in ifaces:
            if iface == 'lo':
                continue
            mac = run_cmd(f"cat /sys/class/net/{iface}/address 2>/dev/null")
            ip = run_cmd(f"ip -4 addr show {iface} | grep inet | awk '{{print $2}}'")
            # Описание адаптера через lspci (если это физический интерфейс)
            # Для простоты просто пишем имя интерфейса
            f.write(f"Сеть Windows{idx}|Свойства сетевого адаптера|Аппаратный адрес= {mac}\n")
            f.write(f"Сеть Windows{idx}|Свойства сетевого адаптера|Сетевой адаптер= {iface}\n")
            if ip:
                f.write(f"Сеть Windows{idx}|Адреса сетевого адаптера|IP / маска подсети= {ip}\n")
            idx += 1


        # ============================================================
        # [Хранение данных Windows] (Диски)
        # ============================================================
        write_section(f, "Хранение данных Windows")
        # lsblk с JSON-выводом для надежного парсинга
        lsblk_json = run_cmd("lsblk -J -b -d -o NAME,MODEL,SIZE,SERIAL,TRAN")
        import json
        try:
            data = json.loads(lsblk_json)
            idx = 1
            for dev in data.get('blockdevices', []):
                name = dev.get('name', '')
                model = dev.get('model', 'N/A')
                size_bytes = dev.get('size', 0)
                serial = dev.get('serial', 'N/A')
                tran = dev.get('tran', 'N/A')
                # Пропускаем loop-устройства и ram
                if name.startswith('loop') or name.startswith('ram'):
                    continue
                size_gb = round(int(size_bytes) / (1024**3)) if size_bytes else 0
                f.write(f"Хранение данных Windows{idx}|Свойства устройства|Описание драйвера= {model}\n")
                f.write(f"Хранение данных Windows{idx}|Свойства устройства|Интерфейс= {tran}\n")
                f.write(f"Хранение данных Windows{idx}|Свойства устройства|Форматированная ёмкость= {size_gb} ГБ\n")
                f.write(f"Хранение данных Windows{idx}|Свойства устройства|Серийный номер= {serial}\n")
                idx += 1
        except Exception:
            f.write("Хранение данных Windows|Ошибка= Не удалось прочитать lsblk\n")


        # ============================================================
        # [Видео Windows] (GPU)
        # ============================================================
        write_section(f, "Видео Windows")
        gpu_info = run_cmd("lspci | grep -i vga")
        if gpu_info:
            # Формат: 07:00.0 VGA compatible controller: NVIDIA Corporation TU117 [GeForce GTX 1650] (rev a1)
            parts = gpu_info.split(': ')
            if len(parts) >= 3:
                desc = parts[-1]
                f.write(f"Видео Windows1|Свойства видеоадаптера|Описание устройства= {desc}\n")
                # Пытаемся получить VRAM (только для NVIDIA)
                if 'nvidia' in desc.lower():
                    vram = run_cmd("nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null")
                    if vram:
                        f.write(f"Видео Windows1|Свойства видеоадаптера|Объем видеоОЗУ= {vram} МБ\n")
                else:
                    f.write(f"Видео Windows1|Свойства видеоадаптера|Объем видеоОЗУ= N/A (требуется драйвер)\n")

        # ============================================================
        # [Операционная система]
        # ============================================================
        write_section(f, "Операционная система")
        f.write(f"Свойства операционной системы|Название ОС= {os_name}\n")
        f.write(f"Свойства операционной системы|Версия ОС= {kernel}\n")
        f.write(f"Лицензионная информация|Ключ продукта= N/A (Linux)\n")

        # ============================================================
        # [Установленные программы] (пример для Debian/Ubuntu)
        # ============================================================
        write_section(f, "Установленные программы")
        # Определяем пакетный менеджер
        if os.path.exists("/usr/bin/dpkg-query"):
            pkgs = run_cmd_lines("dpkg-query -W -f='${Package}|${Version}\\n' 2>/dev/null")
        elif os.path.exists("/usr/bin/rpm"):
            pkgs = run_cmd_lines("rpm -qa --qf '%{NAME}|%{VERSION}-%{RELEASE}\\n' 2>/dev/null")
        elif os.path.exists("/usr/bin/pacman"):
            pkgs = run_cmd_lines("pacman -Q 2>/dev/null")
            pkgs = [p.replace(' ', '|', 1) for p in pkgs]
        else:
            pkgs = []

        idx = 1
        for pkg in pkgs[:200]:  # Ограничим вывод, чтобы файл не стал гигантским
            parts = pkg.split('|', 1)
            name = parts[0]
            ver = parts[1] if len(parts) > 1 else ""
            f.write(f"Установленные программы{idx}= {name}\n")
            f.write(f"{name}|Версия={ver}\n")
            idx += 1

    print(f"Отчет сохранен в файл: {filename}")

if __name__ == "__main__":
    main()
