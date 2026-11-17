#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сбор инвентаризационной информации в Linux в формате, близком к AIDA64.
Требует: lscpu, dmidecode, lsblk, ip, lspci (обычно предустановлены).
Для полного доступа к DMI, SPD и серийным номерам запускать с sudo.
"""

import subprocess
import platform
import re
import os
import json
import glob
import struct
import math

# Словарь популярных PNP ID производителей мониторов
PNP_VENDORS = {
    "ACR": "Acer", "AOC": "AOC", "AUS": "ASUS", "BNQ": "BenQ",
    "DEL": "Dell", "ENC": "EIZO", "GSM": "LG", "HPN": "HP",
    "IVM": "Iiyama", "LEN": "Lenovo", "LGD": "LG Display",
    "MSI": "MSI", "NEC": "NEC", "PHL": "Philips", "SAM": "Samsung",
    "SEC": "Samsung", "SNY": "Sony", "VSC": "ViewSonic", "XMI": "Xiaomi"
}


def run_cmd(cmd):
    """Выполняет команду и возвращает stdout в виде строки."""
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True)
        return out.strip()
    except subprocess.CalledProcessError:
        return ""


def run_cmd_lines(cmd):
    """Возвращает список непустых строк вывода команды."""
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True)
        return [line.strip() for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


def write_section(f, name):
    """Записывает заголовок секции в INI-файл."""
    f.write(f"\n\n[{name}]\n\n")


def get_monitors_info():
    """Читает EDID из /sys/class/drm и возвращает подробный список словарей по каждому монитору."""
    monitors = []
    for edid_path in sorted(glob.glob("/sys/class/drm/*/edid")):
        try:
            with open(edid_path, "rb") as f:
                data = f.read()
        except Exception:
            continue

        if len(data) < 128 or data[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00':
            continue

        # 1. Производитель (PNP ID, байты 8-9)
        mfg_raw = struct.unpack(">H", data[8:10])[0]
        c1 = chr(((mfg_raw >> 10) & 0x1F) + 64)
        c2 = chr(((mfg_raw >> 5) & 0x1F) + 64)
        c3 = chr((mfg_raw & 0x1F) + 64)
        mfg_id = f"{c1}{c2}{c3}"
        vendor_name = PNP_VENDORS.get(mfg_id, mfg_id)

        # Код модели (байты 10-11, little-endian)
        prod_code = struct.unpack("<H", data[10:12])[0]

        # 2. Дата выпуска (байт 16 — неделя, байт 17 — год от 1990)
        week = data[16]
        year = data[17] + 1990
        mfg_date = f"Неделя {week} / {year}" if 1 <= week <= 54 else f"{year}"

        # 3. Физические размеры экрана в см (байты 21 и 22)
        width_cm = data[21]
        height_cm = data[22]
        diag_inch = round(math.hypot(width_cm, height_cm) / 2.54, 1) if (width_cm and height_cm) else 0

        # 4. Дескрипторы по 18 байт (смещения: 54, 72, 90, 108)
        model_name = ""
        serial_str = ""
        max_res = ""
        refresh_rate = ""

        for offset in (54, 72, 90, 108):
            block = data[offset:offset + 18]
            if block[:3] == b'\x00\x00\x00':
                tag = block[3]
                text = block[5:18].decode("ascii", errors="ignore").split("\n")[0].strip()
                if tag == 0xFC:    # Display Product Name
                    model_name = text
                elif tag == 0xFF:  # Display Product Serial Number
                    serial_str = text
            elif not max_res and (block[0] != 0 or block[1] != 0):
                pixel_clock = struct.unpack("<H", block[0:2])[0] * 10000  # в Гц
                h_active = block[2] + ((block[4] & 0xF0) << 4)
                h_blank = block[3] + ((block[4] & 0x0F) << 8)
                v_active = block[5] + ((block[7] & 0xF0) << 4)
                v_blank = block[6] + ((block[7] & 0x0F) << 8)
                if h_active and v_active:
                    max_res = f"{h_active} x {v_active}"
                    total_pixels = (h_active + h_blank) * (v_active + v_blank)
                    if total_pixels > 0:
                        hz = round(pixel_clock / total_pixels)
                        refresh_rate = f"{hz} Гц"

        # Числовой серийный номер (если нет строкового)
        if not serial_str:
            num_serial = struct.unpack("<I", data[12:16])[0]
            if num_serial != 0:
                serial_str = str(num_serial)

        # Имя порта (например, HDMI-A-1 или DP-1)
        port = edid_path.split("/")[-2]
        if "-" in port:
            port = port.split("-", 1)[1]

        display_name = model_name if model_name else f"Монитор {mfg_id}"
        summary_str = f"{display_name} [NoDB] ({serial_str})" if serial_str else f"{display_name} [NoDB]"

        monitors.append({
            "summary": summary_str,
            "name": display_name,
            "vendor": vendor_name,
            "monitor_id": f"{mfg_id}{prod_code:04X}",
            "serial": serial_str if serial_str else "N/A",
            "mfg_date": mfg_date,
            "size_str": f'{diag_inch}" ({width_cm} см x {height_cm} см)' if diag_inch else "N/A",
            "max_res": max_res if max_res else "N/A",
            "refresh_rate": refresh_rate if refresh_rate else "N/A",
            "port": port
        })

    return monitors


def get_disks_info():
    """Собирает список физических накопителей через lsblk -J один раз."""
    disks = []
    lsblk_json = run_cmd("lsblk -J -b -d -o NAME,MODEL,SIZE,SERIAL,TRAN")
    if not lsblk_json:
        return disks
    try:
        data = json.loads(lsblk_json)
        for dev in data.get('blockdevices', []):
            name = dev.get('name', '')
            if name.startswith('loop') or name.startswith('ram') or name.startswith('zram'):
                continue
            model = (dev.get('model') or name or 'N/A').strip()
            size_bytes = dev.get('size') or 0
            serial = (dev.get('serial') or 'N/A').strip()
            tran = (dev.get('tran') or 'N/A').strip().upper()
            size_gb = round(int(size_bytes) / (1024**3)) if size_bytes else 0
            disks.append({
                "name": name,
                "model": model,
                "size_gb": size_gb,
                "serial": serial,
                "tran": tran
            })
    except Exception:
        pass
    return disks


def get_gpus_info():
    """Собирает подробную информацию о видеоадаптерах."""
    gpus = []
    gpu_lines = run_cmd_lines("lspci | grep -iE 'vga|3d|display'")
    nv_vram_list = run_cmd_lines("nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null")
    nv_idx = 0

    for line in gpu_lines:
        parts = line.split(': ', 1)
        if len(parts) < 2:
            continue
        pci_slot = parts[0].split()[0]
        desc = parts[1].strip()

        driver = run_cmd(f"lspci -k -s {pci_slot} | grep 'Kernel driver in use:' | cut -d':' -f2-").strip()
        vram_str = "N/A"

        if 'nvidia' in desc.lower():
            if nv_idx < len(nv_vram_list) and nv_vram_list[nv_idx].isdigit():
                vram_str = f"{nv_vram_list[nv_idx]} МБ"
                nv_idx += 1
        else:
            vram_bytes = run_cmd(f"cat /sys/bus/pci/devices/0000:{pci_slot}/mem_info_vram_total 2>/dev/null")
            if vram_bytes.isdigit():
                vram_str = f"{int(vram_bytes) // (1024 * 1024)} МБ"

        gpus.append({
            "slot": pci_slot,
            "desc": desc,
            "driver": driver if driver else "N/A",
            "vram": vram_str
        })
    return gpus


def main():
    hostname = platform.node()
    filename = f"{hostname}.ini"

    # Предварительный сбор общих данных
    os_name = run_cmd("grep '^PRETTY_NAME' /etc/os-release | cut -d'\"' -f2")
    kernel = platform.release()
    cpu_model = run_cmd("LC_ALL=C lscpu | grep '^Model name:' | cut -d':' -f2-").strip()
    monitors = get_monitors_info()
    disks = get_disks_info()
    gpus = get_gpus_info()
    audio_devs = [
        line.split(': ', 1)[1].strip()
        for line in run_cmd_lines("lspci | grep -i audio")
        if ': ' in line
    ]

    with open(filename, 'w', encoding='utf-8') as f:

        # ============================================================
        # [Суммарная информация]
        # ============================================================
        write_section(f, "Суммарная информация")
        f.write(f"Компьютер|Имя компьютера= {hostname}\n")
        f.write(f"Компьютер|Тип компьютера= {run_cmd('dmidecode -s chassis-type 2>/dev/null')}\n")
        f.write(f"Компьютер|Операционная система= {os_name}\n")
        f.write(f"Компьютер|Версия ядра= {kernel}\n")
        f.write(f"Системная плата|Тип ЦП= {cpu_model}\n")
        f.write(f"Системная плата|Системная плата= {run_cmd('cat /sys/devices/virtual/dmi/id/board_name 2>/dev/null')}\n")

        # --- Системная память (суммарно) ---
        mem_total = 0
        mem_out = run_cmd("LC_ALL=C dmidecode -t memory 2>/dev/null | grep 'Size:' | cut -d':' -f2")
        for line in mem_out.splitlines():
            line = line.strip()
            if not line or 'No Module' in line or 'None' in line:
                continue
            m = re.search(r'(\d+)\s*(GB|MB)', line, re.IGNORECASE)
            if m:
                val, unit = int(m.group(1)), m.group(2).upper()
                mem_total += val * 1024 if unit == 'GB' else val
        if mem_total == 0:
            mem_info = run_cmd("grep MemTotal /proc/meminfo")
            mem_kb = int(re.search(r'\d+', mem_info).group()) if mem_info else 0
            mem_total = mem_kb // 1024
        f.write(f"Системная плата|Системная память= {mem_total // 1024} ГБ\n")

        # --- Аудио (кратко) ---
        for idx, aud in enumerate(audio_devs, start=1):
            f.write(f"Мультимедиа|Звуковой адаптер{idx}= {aud}\n")

        # --- Видеоадаптеры (кратко) ---
        for idx, gpu in enumerate(gpus, start=1):
            f.write(f"Отображение|Видеоадаптер{idx}= {gpu['desc']}\n")
        # --- Мониторы (кратко) ---
        for idx, mon in enumerate(monitors, start=1):
            f.write(f"Дисплей|Монитор{idx}= {mon['summary']}\n")
        # --- Хранение данных (кратко) ---
        for idx, disk in enumerate(disks, start=1):
            f.write(f"Хранение данных|Дисковый накопитель{idx}= {disk['model']} ({disk['size_gb']} ГБ)\n")
        # --- Сеть (первый IP и MAC) ---
        ip_addr = run_cmd("ip -4 addr show | grep inet | grep -v 127.0.0.1 | head -1 | awk '{print $2}'")
        mac = run_cmd("ip link show | grep -v lo | grep 'link/ether' | head -1 | awk '{print $2}'")
        f.write(f"Сеть|Первичный адрес IP= {ip_addr}\n")
        f.write(f"Сеть|Первичный адрес MAC= {mac}\n")
        # --- DMI (Сводка) ---
        f.write(f"DMI|DMI поставщик BIOS= {run_cmd('dmidecode -s bios-vendor 2>/dev/null')}\n")
        f.write(f"DMI|DMI версия BIOS= {run_cmd('dmidecode -s bios-version 2>/dev/null')}\n")
        f.write(f"DMI|DMI производитель системы= {run_cmd('dmidecode -s system-manufacturer 2>/dev/null')}\n")
        f.write(f"DMI|DMI система= {run_cmd('dmidecode -s system-product-name 2>/dev/null')}\n")
        f.write(f"DMI|DMI серийный номер системной платы= {run_cmd('dmidecode -s baseboard-serial-number 2>/dev/null')}\n")

        # ============================================================
        # [Системная плата]
        # ============================================================
        write_section(f, "Системная плата")
        f.write(f"Свойства системной платы|Системная плата= {run_cmd('cat /sys/devices/virtual/dmi/id/board_name 2>/dev/null')}\n")
        f.write(f"Производитель системной платы|Фирма= {run_cmd('cat /sys/devices/virtual/dmi/id/board_vendor 2>/dev/null')}\n")

        # ============================================================
        # [SPD] (Модули памяти)
        # ============================================================
        write_section(f, "SPD")
        dmi_out = run_cmd("LC_ALL=C dmidecode -t 17 2>/dev/null")
        devices = dmi_out.split('Memory Device')
        spd_idx = 1
        for dev in devices[1:]:
            lines = [l.strip() for l in dev.splitlines() if l.strip()]
            dev_data = {}
            for line in lines:
                if ':' in line:
                    k, v = line.split(':', 1)
                    dev_data[k.strip()] = v.strip()
            size = dev_data.get('Size', '')
            if not size or 'No Module' in size or 'None' in size:
                continue
            prefix = f"SPD{spd_idx}|Свойства модуля памяти"
            mfg = dev_data.get('Manufacturer', '').strip()
            part_num = dev_data.get('Part Number', 'N/A').strip()
            if mfg and part_num.lower().startswith(mfg.lower()):
                full_title = part_num
            else:
                full_title = f"{mfg} {part_num}".strip()
            max_speed = dev_data.get('Speed', 'N/A')
            cfg_speed = dev_data.get('Configured Memory Speed', max_speed)
            f.write(f"SPD{spd_idx}= {dev_data.get('Locator', 'DIMM')}: {full_title}\n")
            f.write(f"{prefix}|Имя модуля= {part_num}\n")
            f.write(f"{prefix}|Серийный номер= {dev_data.get('Serial Number', 'N/A')}\n")
            f.write(f"{prefix}|Размер модуля= {size}\n")
            f.write(f"{prefix}|Тип памяти= {dev_data.get('Type', 'N/A')} ({dev_data.get('Type Detail', 'N/A')})\n")
            f.write(f"{prefix}|Форм-фактор= {dev_data.get('Form Factor', 'N/A')}\n")
            f.write(f"{prefix}|Скорость памяти (номинал)= {max_speed}\n")
            f.write(f"{prefix}|Текущая скорость (XMP/EXPO)= {cfg_speed}\n")
            f.write(f"{prefix}|Производитель= {mfg if mfg else 'N/A'}\n")
            f.write(f"{prefix}|Ширина данных / Общая ширина= {dev_data.get('Data Width', 'N/A')} / {dev_data.get('Total Width', 'N/A')}\n")
            f.write(f"{prefix}|Ранговость (Rank)= {dev_data.get('Rank', 'N/A')}\n")
            f.write(f"{prefix}|Напряжение= {dev_data.get('Configured Voltage', 'N/A')}\n")
            spd_idx += 1

        # ============================================================
        # [DMI] (Процессор и кэш)
        # ============================================================
        write_section(f, "DMI")
        cpu_vendor = run_cmd("LC_ALL=C lscpu | grep '^Vendor ID:' | cut -d':' -f2-").strip()
        cpu_socket = run_cmd("LC_ALL=C dmidecode -t processor 2>/dev/null | grep 'Socket Designation:' | head -1 | cut -d':' -f2-").strip()
        if not cpu_socket:
            cpu_socket = run_cmd("LC_ALL=C lscpu | grep '^Socket(s):' | cut -d':' -f2-").strip()
        cpu_mhz_max_raw = run_cmd("LC_ALL=C lscpu | grep '^CPU max MHz:' | cut -d':' -f2-").strip()
        cpu_mhz_max = f"{round(float(cpu_mhz_max_raw))} МГц" if cpu_mhz_max_raw else "N/A"
        cpu_mhz_cur_raw = run_cmd("grep -m1 '^cpu MHz' /proc/cpuinfo | cut -d':' -f2-").strip()
        cpu_mhz_cur = f"{round(float(cpu_mhz_cur_raw))} МГц" if cpu_mhz_cur_raw else "N/A"
        cores = run_cmd("LC_ALL=C lscpu | grep '^Core(s) per socket:' | cut -d':' -f2-").strip()
        threads = run_cmd("LC_ALL=C lscpu | grep '^CPU(s):' | cut -d':' -f2-").strip()
        prefix_cpu = "Процессоры1|Свойства процессора"
        f.write(f"{prefix_cpu}|Производитель= {cpu_vendor}\n")
        f.write(f"{prefix_cpu}|Версия= {cpu_model}\n")
        f.write(f"{prefix_cpu}|Тип разъёма= {cpu_socket}\n")
        f.write(f"{prefix_cpu}|Ядра / Потоки= {cores} / {threads}\n")
        f.write(f"{prefix_cpu}|Максимальная частота= {cpu_mhz_max}\n")
        f.write(f"{prefix_cpu}|Текущая частота= {cpu_mhz_cur}\n")
        cache_map = [
            ("L1 кэш данных", r"^L1d( cache)?:"),
            ("L1 кэш инструкций", r"^L1i( cache)?:"),
            ("L2 кэш", r"^L2( cache)?:"),
            ("L3 кэш", r"^L3( cache)?:"),
        ]
        for cache_name, pattern in cache_map:
            val = run_cmd(f"LC_ALL=C lscpu | grep -E '{pattern}' | cut -d':' -f2-").strip()
            if val:
                f.write(f"Кэш-память|Свойства кэша|{cache_name}= {val}\n")

        # ============================================================
        # [Видео Windows] (GPU)
        # ============================================================
        write_section(f, "Видео Windows")
        for idx, gpu in enumerate(gpus, start=1):
            prefix = f"Видео Windows{idx}|Свойства видеоадаптера"
            f.write(f"{prefix}|Описание устройства= {gpu['desc']}\n")
            f.write(f"{prefix}|Аппаратный ID (PCI)= {gpu['slot']}\n")
            f.write(f"{prefix}|Драйвер ядра= {gpu['driver']}\n")
            f.write(f"{prefix}|Объем видеоОЗУ= {gpu['vram']}\n")

        # ============================================================
        # [Монитор]
        # ============================================================
        write_section(f, "Монитор")
        for idx, mon in enumerate(monitors, start=1):
            prefix = f"Монитор{idx}|Свойства монитора"
            f.write(f"{prefix}|Имя монитора= {mon['name']}\n")
            f.write(f"{prefix}|ID монитора= {mon['monitor_id']}\n")
            f.write(f"{prefix}|Производитель= {mon['vendor']}\n")
            f.write(f"{prefix}|Серийный номер= {mon['serial']}\n")
            f.write(f"{prefix}|Дата выпуска= {mon['mfg_date']}\n")
            f.write(f"{prefix}|Видимая область экрана= {mon['size_str']}\n")
            f.write(f"{prefix}|Максимальное разрешение= {mon['max_res']}\n")
            f.write(f"{prefix}|Частота обновления= {mon['refresh_rate']}\n")
            f.write(f"{prefix}|Интерфейс= {mon['port']}\n")

        # ============================================================
        # [Сеть Windows]
        # ============================================================
        write_section(f, "Сеть Windows")
        ifaces = run_cmd_lines("ip -o link show | awk -F': ' '{print $2}'")
        idx = 1
        for iface in ifaces:
            if iface == 'lo':
                continue
            mac = run_cmd(f"cat /sys/class/net/{iface}/address 2>/dev/null")
            ip = run_cmd(f"ip -4 addr show {iface} | grep inet | awk '{{print $2}}'")
            f.write(f"Сеть Windows{idx}|Свойства сетевого адаптера|Аппаратный адрес= {mac}\n")
            f.write(f"Сеть Windows{idx}|Свойства сетевого адаптера|Сетевой адаптер= {iface}\n")
            if ip:
                f.write(f"Сеть Windows{idx}|Адреса сетевого адаптера|IP / маска подсети= {ip}\n")
            idx += 1

        # ============================================================
        # [Хранение данных Windows] (Диски)
        # ============================================================
        write_section(f, "Хранение данных Windows")
        if disks:
            for idx, disk in enumerate(disks, start=1):
                prefix = f"Хранение данных Windows{idx}|Свойства устройства"
                f.write(f"{prefix}|Описание драйвера= {disk['model']}\n")
                f.write(f"{prefix}|Устройство= /dev/{disk['name']}\n")
                f.write(f"{prefix}|Интерфейс= {disk['tran']}\n")
                f.write(f"{prefix}|Форматированная ёмкость= {disk['size_gb']} ГБ\n")
                f.write(f"{prefix}|Серийный номер= {disk['serial']}\n")
        else:
            f.write("Хранение данных Windows|Ошибка= Не удалось прочитать lsblk\n")

        # ============================================================
        # [Операционная система]
        # ============================================================
        write_section(f, "Операционная система")
        f.write(f"Свойства операционной системы|Название ОС= {os_name}\n")
        f.write(f"Свойства операционной системы|Версия ОС= {kernel}\n")
        f.write(f"Лицензионная информация|Ключ продукта= N/A (Linux)\n")

        # ============================================================
        # [Установленные программы]
        # ============================================================
        write_section(f, "Установленные программы")
        if os.path.exists("/usr/bin/dpkg-query"):
            pkgs = run_cmd_lines("dpkg-query -W -f='${Package}|${Version}\n' 2>/dev/null")
        elif os.path.exists("/usr/bin/rpm"):
            pkgs = run_cmd_lines("rpm -qa --qf '%{NAME}|%{VERSION}-%{RELEASE}\n' 2>/dev/null")
        elif os.path.exists("/usr/bin/pacman"):
            pkgs = run_cmd_lines("pacman -Q 2>/dev/null")
            pkgs = [p.replace(' ', '|', 1) for p in pkgs]
        else:
            pkgs = []
        for idx, pkg in enumerate(pkgs[:200], start=1):
            parts = pkg.split('|', 1)
            name = parts[0]
            ver = parts[1] if len(parts) > 1 else ""
            f.write(f"Установленные программы{idx}= {name}\n")
            f.write(f"{name}|Версия={ver}\n")

    print(f"Отчет сохранен в файл: {filename}")


if __name__ == "__main__":
    main()
