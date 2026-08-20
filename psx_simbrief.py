# psx_simbrief_v1_1b.py
# Standalone SimBrief XML -> Aerowinx PSX uploader
#
# v1.0:
# - Reads settings from psx_simbrief.ini
# - Clears screen and prints header
# - Downloads SimBrief PSX .route file to [PSX] route_dir
# - Saves route file as ORIGDEST01_.route, ORIGDEST02_.route, etc.
# - Shows CO ROUTE as ORIGDEST01, ORIGDEST02, etc. in final summary
# - Shows FLT NO from <atc><callsign>
# - Uploads ZFW, fuel, and wind alofts to PSX
# - Sends "exit" before disconnecting from PSX
# - Compact debug output
# - Adds RESERVES line from <fuel_min_onboard>
# - RESERVES are shown in thousands, rounded UP to 0.1:
#     9058 -> 9.1
#
# No external modules required.

from __future__ import annotations

import configparser
import html
import math
import os
import re
import socket
import textwrap
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


VERSION = "1.1b"

APP_DIR = Path(__file__).resolve().parent
INI_PATH = APP_DIR / "psx_simbrief.ini"

DEFAULT_PSX_HOST = "127.0.0.1"
DEFAULT_PSX_PORT = 10747
DEFAULT_ROUTE_DIR = str(APP_DIR)

WAIT_AFTER_CONNECT_SECONDS = 1.0
SEND_PAUSE_SECONDS = 0.3
AFTER_FUELING_PAUSE_SECONDS = 1.0

KG_TO_LBS = 2.20462262185
SEPARATOR = "--------------------------------------------------------------------"
BOX_LINE = "==============================================================="

TANK_CAPACITY_LBS = {
    "main1": 29293,
    "main2": 84058,
    "main3": 84058,
    "main4": 29293,
    "res2": 8856,
    "res3": 8856,
    "center": 115000,
    "aux": 21495,
    "stab": 22110,
}

# AUX and STAB are deliberately last; not every aircraft has them installed.
FILL_ORDER = [
    "main1",
    "main2",
    "main4",
    "main3",
    "center",
    "res2",
    "res3",
    "aux",
    "stab",
]


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_header():
    print(f"""
===============================================================
                  PSX ↔ Simbrief v{VERSION}
---------------------------------------------------------------
                 Jamie Janssen  © 2026
===============================================================
""")


def load_config():
    config = configparser.ConfigParser()

    if INI_PATH.exists():
        config.read(INI_PATH, encoding="utf-8")

    username = config.get("SIMBRIEF", "username", fallback="").strip()
    if not username:
        raise RuntimeError("Missing [SIMBRIEF] username in psx_simbrief.ini")

    host = config.get("PSX", "host", fallback=DEFAULT_PSX_HOST)
    port = config.getint("PSX", "port", fallback=DEFAULT_PSX_PORT)
    route_dir = config.get("PSX", "route_dir", fallback=DEFAULT_ROUTE_DIR)

    return username, host.strip(), int(port), route_dir.strip()


def kg_to_lbs_ceil(value_kg):
    return math.ceil(float(value_kg) * KG_TO_LBS)


def format_reserves(reserve_kg):
    return f"{math.ceil(float(reserve_kg) / 100) / 10:.1f}"


def fetch_simbrief_xml(username):
    url = f"https://www.simbrief.com/api/xml.fetcher.php?username={username}"

    try:
        with urlopen(url, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        # SimBrief may return a useful XML error response with HTTP 400,
        # for example when no flight plan is currently on file.
        body = exc.read().decode("utf-8", errors="replace")

        if body.lstrip().startswith("<?xml") or body.lstrip().startswith("<OFP"):
            return body

        raise


def strip_html(value):
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return value


def required_text(root, path):
    value = root.findtext(path)
    if not value or not value.strip():
        raise RuntimeError(f"Missing XML value: {path}")
    return value.strip()


def optional_text(root, path, fallback=""):
    value = root.findtext(path)
    if value is None:
        return fallback
    value = value.strip()
    return value if value else fallback


def distribute_fuel(total_lbs):
    remaining = int(total_lbs)
    tanks = {name: 0 for name in TANK_CAPACITY_LBS}

    for tank in FILL_ORDER:
        if remaining <= 0:
            break

        amount = min(TANK_CAPACITY_LBS[tank], remaining)
        tanks[tank] = amount
        remaining -= amount

    if remaining > 0:
        raise RuntimeError("Block fuel exceeds PSX tank capacity")

    return tanks


def build_qs438(block_fuel_lbs):
    tanks = distribute_fuel(block_fuel_lbs)

    values = [
        tanks["main1"] * 10,
        tanks["main2"] * 10,
        tanks["main3"] * 10,
        tanks["main4"] * 10,
        tanks["res2"] * 10,
        tanks["res3"] * 10,
        tanks["center"] * 10,
        tanks["stab"] * 10,
        tanks["aux"] * 10,
        block_fuel_lbs * 10,
        1500,
    ]

    return "Qs438=d" + ";".join(str(value) for value in values) + ";\r\n"


def psx_fix_name(name):
    match = re.match(r"^(\d{2})(N|S)(\d{3})(E|W)$", name.strip(), re.IGNORECASE)
    if not match:
        return name

    lat = match.group(1)
    hemi = match.group(2).upper()
    lon = match.group(3)

    return f"{lat}{lon[-2:]}{hemi}"


def convert_oceanic_waypoints(line):
    def repl(match):
        original = match.group(0)
        converted = psx_fix_name(original)
        return converted.ljust(len(original))

    return re.sub(r"\b\d{2}[NS]\d{3}[EW]\b", repl, line, flags=re.IGNORECASE)


def extract_wind_body(root):
    plan_html = root.findtext(".//plan_html")
    if not plan_html:
        raise RuntimeError("No <plan_html> found")

    plain = strip_html(plan_html)

    start = plain.find("WIND INFORMATION")
    if start < 0:
        raise RuntimeError("No WIND INFORMATION block found")

    end = plain.find(SEPARATOR, start + len("WIND INFORMATION"))
    if end < 0:
        end = len(plain)

    raw_block = plain[start:end]

    lines = []
    for raw_line in raw_block.splitlines():
        line = raw_line.rstrip()

        if line.strip() == "WIND INFORMATION":
            continue

        if line.strip() and set(line.strip()) == {"-"}:
            continue

        if line.strip() and not is_wind_value_line(line):
            line = line.lstrip()

        lines.append(convert_oceanic_waypoints(line))

    while lines and not lines[0].strip():
        lines.pop(0)

    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


def is_wind_value_line(line):
    return re.match(r"^\s*\d{3}\s+\d{3}/\d{3}\s+[+-]\d{2}", line) is not None


def count_wind_corridors(wind_body):
    count = 0

    for line in wind_body.splitlines():
        if not line.strip():
            continue

        if is_wind_value_line(line):
            continue

        names = [name.strip() for name in re.split(r"\s{2,}", line.strip()) if name.strip()]
        count += len(names)

    return count


def build_qs498(wind_body):
    caret_text = (
        wind_body
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "^")
    )

    return f"Qs498=#{caret_text}\r\n"


def send_command(sock, command, pause=SEND_PAUSE_SECONDS):
    sock.sendall(command.encode("utf-8"))
    time.sleep(pause)


def get_orig_dest(root):
    orig = optional_text(root, ".//api_params/orig")
    dest = optional_text(root, ".//api_params/dest")

    if not orig:
        orig = required_text(root, ".//origin/icao_code")

    if not dest:
        dest = required_text(root, ".//destination/icao_code")

    return orig.upper(), dest.upper()


def next_coroute_name(route_dir, orig, dest):
    """
    Returns:
        coroute_name: EHAMKEWR01
        filename:     EHAMKEWR01_.route

    Picks the first free sequence from 01 to 99.
    """
    route_dir = Path(route_dir).expanduser()
    route_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"{orig}{dest}"

    for seq in range(1, 100):
        coroute_name = f"{prefix}{seq:02d}"
        filename = f"{coroute_name}_.route"
        candidate = route_dir / filename

        if not candidate.exists():
            return coroute_name, filename, candidate

    raise RuntimeError(f"No free co-route filename found for {prefix}01_.route to {prefix}99_.route")


def download_psx_route_file(root, route_dir):
    time_generated = required_text(root, ".//params/time_generated")
    orig, dest = get_orig_dest(root)

    source_filename = f"{orig}{dest}_PSX_{time_generated}.route"
    route_url = f"https://www.simbrief.com/ofp/flightplans/{source_filename}"

    coroute_name, target_filename, target_path = next_coroute_name(route_dir, orig, dest)

    with urlopen(route_url, timeout=20) as response:
        target_path.write_bytes(response.read())

    return coroute_name, target_path


def get_flight_summary(root):
    orig, dest = get_orig_dest(root)

    callsign = required_text(root, ".//atc/callsign")
    origin_rwy = required_text(root, ".//origin/plan_rwy")
    dest_rwy = required_text(root, ".//destination/plan_rwy")

    route = optional_text(root, ".//api_params/route")
    if not route:
        route = optional_text(root, ".//general/route_navigraph")
    if not route:
        route = required_text(root, ".//general/route")

    date_raw = required_text(root, ".//api_params/date")
    readable_date = datetime.fromtimestamp(int(date_raw), tz=timezone.utc).strftime("%d %b %Y")

    def get_destination_reserve(root):
        _, dest = get_orig_dest(root)

        for fix in root.findall(".//fix"):
            ident = fix.findtext("ident", "").strip().upper()

            if ident == dest:
                reserve = fix.findtext("fuel_min_onboard")
                if reserve:
                    return reserve.strip()

        raise RuntimeError(f"No fuel_min_onboard found for destination {dest}")

    reserve_kg = get_destination_reserve(root)
    reserve_display = format_reserves(reserve_kg)

    return callsign, f"{orig}{origin_rwy} - {dest}{dest_rwy}", readable_date, route, reserve_display


def print_final_summary(callsign, coroute_name, flight, readable_date, route, reserve_display):
    print()
    print(BOX_LINE)
    print(f"FLT NO:   {callsign}")
    print(f"CO ROUTE: {coroute_name}")
    print()
    print(f"Flight:   {flight}")
    print(f"Date:     {readable_date}")
    print()

    wrapped_route = textwrap.wrap(route, width=55)

    if wrapped_route:
        print(f"Route:    {wrapped_route[0]}")
        for line in wrapped_route[1:]:
            print(f"          {line}")
    else:
        print("Route:")

    print()
    print(f"RESERVES: {reserve_display}")
    print(BOX_LINE)


def upload_to_psx(psx_host, psx_port, qi123, qs438, qs498):
    print(f"[PSX] Connecting to {psx_host}:{psx_port}...")

    with socket.create_connection((psx_host, psx_port), timeout=10) as sock:
        print("[PSX] Connected successfully.")
        time.sleep(WAIT_AFTER_CONNECT_SECONDS)

        print("[PSX] Uploading SimBrief data...")

        send_command(sock, qi123)
        send_command(sock, qs438)

        send_command(sock, "Qi220=1\r\n")
        send_command(sock, "Qi220=0\r\n")

        time.sleep(AFTER_FUELING_PAUSE_SECONDS)

        send_command(sock, "Qs497=201\r\n")
        send_command(sock, qs498)

        send_command(sock, "exit\r\n", pause=0)

    print("[PSX] Upload complete. Disconnected.")


def main():
    clear_screen()
    print_header()

    username, psx_host, psx_port, route_dir = load_config()

    print("[SIMBRIEF] Fetching latest OFP XML...")

    xml_text = fetch_simbrief_xml(username)
    root = ET.fromstring(xml_text)

    fetch_status = optional_text(root, ".//fetch/status")
    if fetch_status.lower().startswith("error"):
        message = fetch_status.split(":", 1)[-1].strip()
        print(f"[SIMBRIEF] {message}.")
        return

    zfw_kg = int(float(required_text(root, ".//weights/est_zfw")))
    block_kg = int(float(required_text(root, ".//fuel/plan_ramp")))

    zfw_lbs = kg_to_lbs_ceil(zfw_kg)
    block_lbs = kg_to_lbs_ceil(block_kg)

    qi123 = f"Qi123={zfw_lbs}\r\n"
    qs438 = build_qs438(block_lbs)

    wind_body = extract_wind_body(root)
    wind_corridors = count_wind_corridors(wind_body)
    qs498 = build_qs498(wind_body)

    coroute_name, route_path = download_psx_route_file(root, route_dir)
    callsign, flight, readable_date, route, reserve_display = get_flight_summary(root)

    print(f"[SIMBRIEF] ZFW: {zfw_lbs} lbs / {zfw_kg} kg -> Qi123")
    print(f"[SIMBRIEF] Block fuel: {block_lbs} lbs / {block_kg} kg -> Qs438")
    print(f"[SIMBRIEF] Wind corridors: {wind_corridors}")
    print(f"[SIMBRIEF] PSX route saved: {route_path}")

    upload_to_psx(psx_host, psx_port, qi123, qs438, qs498)
    print_final_summary(callsign, coroute_name, flight, readable_date, route, reserve_display)


if __name__ == "__main__":
    main()
