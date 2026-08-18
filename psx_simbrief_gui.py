# psx_simbrief_gui.py
# Clipboard-style GUI for PSX Simbrief

from __future__ import annotations

import base64
import configparser
import json
import socket
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import psx_simbrief as backend


VERSION = "1.2"
APP_NAME = "PSX Simbrief"
APP_DIR = Path(__file__).resolve().parent

if sys.platform == "darwin":
    SETTINGS_DIR = Path.home() / "Library/Application Support/PSX Simbrief"
else:
    SETTINGS_DIR = APP_DIR

INI_PATH = SETTINGS_DIR / "psx_simbrief.ini"
CACHE_PATH = SETTINGS_DIR / "last_flight.json"
ROUTE_CACHE_PATH = SETTINGS_DIR / "last_route.route"

DEFAULTS = {
    "username": "",
    "host": "127.0.0.1",
    "port": "10747",
    "route_dir": str(APP_DIR),
}


class PsxSimbriefGui(tk.Tk):
    def __init__(self):
        super().__init__()

        self._destroying = False
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("500x620")
        self.minsize(460, 540)
        self.configure(bg="#000000")

        self.config_values = self.load_config()
        self.current_data = None

        self.callsign_var = tk.StringVar(master=self, value="-")
        self.coroute_var = tk.StringVar(master=self, value="-")
        self.flight_var = tk.StringVar(master=self, value="-")
        self.date_var = tk.StringVar(master=self, value="-")
        self.reserves_var = tk.StringVar(master=self, value="-")
        self.status_var = tk.StringVar(master=self, value="Ready")
        self.fuel_table_var = tk.StringVar(master=self, value="")
        self.zfw_var = tk.StringVar(master=self, value="-")
        self.tow_var = tk.StringVar(master=self, value="-")
        self.reserve_display_var = tk.StringVar(master=self, value="-")

        self._build_ui()
        self.load_cached_flight()
        self._restore_window_geometry()

    # ------------------------------------------------------------------
    # Configuration and cache
    # ------------------------------------------------------------------

    def load_config(self):
        values = DEFAULTS.copy()
        config = configparser.ConfigParser()

        source = INI_PATH
        fallback_source = APP_DIR / "psx_simbrief.ini"

        if not source.exists() and fallback_source.exists():
            source = fallback_source

        if source.exists():
            config.read(source, encoding="utf-8")
            values["username"] = config.get(
                "SIMBRIEF", "username", fallback=""
            ).strip()
            values["host"] = config.get(
                "PSX", "host", fallback=DEFAULTS["host"]
            ).strip()
            values["port"] = config.get(
                "PSX", "port", fallback=DEFAULTS["port"]
            ).strip()
            values["route_dir"] = config.get(
                "PSX", "route_dir", fallback=DEFAULTS["route_dir"]
            ).strip()

        return values

    def save_config(self, values):
        config = configparser.ConfigParser()
        config["SIMBRIEF"] = {"username": values["username"]}
        config["PSX"] = {
            "host": values["host"],
            "port": values["port"],
            "route_dir": values["route_dir"],
        }

        if INI_PATH.exists():
            existing = configparser.ConfigParser()
            existing.read(INI_PATH, encoding="utf-8")
            if existing.has_section("WINDOW"):
                config["WINDOW"] = dict(existing["WINDOW"])

        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with INI_PATH.open("w", encoding="utf-8") as handle:
            config.write(handle)

        self.config_values = values.copy()
        self._save_window_geometry()

    def save_cached_flight(self, data):
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = CACHE_PATH.with_suffix(".tmp")

        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

        temp_path.replace(CACHE_PATH)

    def save_persistent_route(self, route_bytes):
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = ROUTE_CACHE_PATH.with_suffix(".tmp")
        temp_path.write_bytes(route_bytes)
        temp_path.replace(ROUTE_CACHE_PATH)

    def _migrate_route_cache(self, data):
        """Create last_route.route from older cache formats when possible."""
        if ROUTE_CACHE_PATH.exists() and ROUTE_CACHE_PATH.is_file():
            return

        encoded = data.get("route_file_b64", "")
        if encoded:
            try:
                self.save_persistent_route(
                    base64.b64decode(encoded.encode("ascii"))
                )
                return
            except Exception:
                pass

        old_path_text = data.get("route_path", "")
        if old_path_text:
            old_path = Path(old_path_text).expanduser()
            if old_path.exists() and old_path.is_file():
                self.save_persistent_route(old_path.read_bytes())

    def load_cached_flight(self):
        if not CACHE_PATH.exists():
            return

        try:
            with CACHE_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)

            required = {
                "callsign",
                "coroute",
                "flight",
                "date",
                "route",
                "reserves",
                "qi123",
                "qs438",
                "qs498",
            }
            if not required.issubset(data):
                raise ValueError("Cached flight data is incomplete")

            self._migrate_route_cache(data)
            self._apply_fetched_data(data, from_cache=True)
        except Exception as exc:
            self.status_var.set("Could not load saved flight")
            print(f"[CACHE] Could not load {CACHE_PATH}: {exc}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        board = tk.Frame(self, bg="#000000", bd=0)
        board.pack(fill="both", expand=True)

        self.menu_button = tk.Canvas(
            board,
            width=20,
            height=24,
            bg="#000000",
            bd=0,
            relief="flat",
            highlightthickness=0,
            cursor="hand2",
        )
        for y in (7, 12, 17):
            self.menu_button.create_line(
                5,
                y,
                15,
                y,
                fill="#ffffff",
                width=2,
            )
        self.menu_button.bind("<Button-1>", lambda _event: self.show_menu())
        self.menu_button.place(relx=1.0, x=-8, y=3, anchor="ne")

        clip = tk.Frame(board, bg="#777777", width=150, height=24, bd=0)
        clip.place(relx=0.5, y=10, anchor="n")
        clip.pack_propagate(False)
        tk.Frame(clip, bg="#b5b5b5", height=5).pack(
            fill="x", padx=22, pady=(5, 0)
        )

        paper = tk.Frame(board, bg="#ffffff", bd=0)
        paper.pack(fill="both", expand=True, padx=20, pady=(32, 20))

        content = tk.Frame(paper, bg="#ffffff")
        content.pack(fill="both", expand=True, padx=28, pady=(18, 0))
        content.columnconfigure(1, weight=1)

        summary = tk.Frame(content, bg="#ffffff")
        summary.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        summary.columnconfigure(1, weight=1)
        summary.columnconfigure(4, weight=1)

        def summary_row(row, left_label, left_var, right_label, right_var):
            tk.Label(
                summary,
                text=f"{left_label}:",
                font=("Menlo", 12, "bold"),
                bg="#ffffff",
                fg="#111111",
                anchor="w",
            ).grid(row=row, column=0, sticky="w", pady=2)
            tk.Label(
                summary,
                textvariable=left_var,
                font=("Menlo", 12),
                bg="#ffffff",
                fg="#111111",
                anchor="w",
            ).grid(row=row, column=1, sticky="w", padx=(12, 18), pady=2)

            tk.Label(
                summary,
                text=f"{right_label}:",
                font=("Menlo", 12, "bold"),
                bg="#ffffff",
                fg="#111111",
                anchor="e",
            ).grid(row=row, column=3, sticky="e", pady=2)
            tk.Label(
                summary,
                textvariable=right_var,
                font=("Menlo", 12),
                bg="#ffffff",
                fg="#111111",
                anchor="e",
            ).grid(row=row, column=4, sticky="e", padx=(12, 0), pady=2)

        summary_row(0, "Flight", self.flight_var, "FLT NO", self.callsign_var)
        summary_row(1, "Date", self.date_var, "CO ROUTE", self.coroute_var)
        summary_row(2, "ZFW", self.zfw_var, "TOW", self.tow_var)

        tk.Frame(content, bg="#d0d0d0", height=1).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(0, 2)
        )

        tk.Label(
            content,
            text="Route",
            font=("Menlo", 12, "bold"),
            bg="#ffffff",
            fg="#111111",
            anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 2))

        route_frame = tk.Frame(content, bg="#ffffff")
        route_frame.grid(row=3, column=0, columnspan=2, sticky="ew")

        self.route_text = tk.Text(
            route_frame,
            height=4,
            wrap="word",
            font=("Menlo", 11),
            bg="#ffffff",
            fg="#111111",
            relief="solid",
            bd=1,
            highlightthickness=0,
            padx=8,
            pady=8,
            undo=False,
            exportselection=True,
        )
        self.route_text.pack(fill="x", expand=True)
        self.route_text.insert("1.0", "-")
        self.route_text.configure(state="disabled")

        self.fuel_table_label = tk.Label(
            content,
            textvariable=self.fuel_table_var,
            font=("Menlo", 9),
            bg="#ffffff",
            fg="#111111",
            justify="left",
            anchor="w",
        )
        self.fuel_table_label.grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(12, 0),
        )

        reserve_row = tk.Frame(content, bg="#ffffff")
        reserve_row.grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        tk.Label(
            reserve_row,
            text="RESERVES:",
            font=("Menlo", 12, "bold"),
            bg="#ffffff",
            fg="#111111",
        ).pack(side="left")
        tk.Label(
            reserve_row,
            textvariable=self.reserve_display_var,
            font=("Menlo", 12),
            bg="#ffffff",
            fg="#111111",
        ).pack(side="left", padx=(10, 0))

        bottom = tk.Frame(paper, bg="#ffffff")
        bottom.pack(fill="x", padx=28, pady=(0, 8))

        self.fetch_button = ttk.Button(
            bottom, text="Fetch SimBrief", command=self.fetch_simbrief
        )
        self.fetch_button.pack(side="left")

        self.upload_button = ttk.Button(
            bottom,
            text="Flight INIT",
            command=self.upload_current_to_psx,
            state="disabled",
        )
        self.upload_button.pack(side="left", padx=(8, 0))

        tk.Label(
            bottom,
            textvariable=self.status_var,
            font=("Helvetica Neue", 10),
            bg="#ffffff",
            fg="#555555",
            anchor="e",
        ).pack(side="right", padx=(12, 0))

    def show_menu(self):
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Purge Routes…", command=self.purge_routes)
        menu.add_command(label="Settings…", command=self.open_settings)
        menu.add_separator()
        menu.add_command(label="Quit", command=self.destroy)

        x = self.menu_button.winfo_rootx()
        y = self.menu_button.winfo_rooty() + self.menu_button.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------
    # Settings window
    # ------------------------------------------------------------------

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("PSX Simbrief Settings")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)

        username_var = tk.StringVar(value=self.config_values["username"])
        host_var = tk.StringVar(value=self.config_values["host"])
        port_var = tk.StringVar(value=self.config_values["port"])
        route_var = tk.StringVar(value=self.config_values["route_dir"])

        ttk.Label(body, text="SimBrief username").grid(
            row=0, column=0, sticky="w", pady=6
        )
        ttk.Entry(body, textvariable=username_var, width=42).grid(
            row=0, column=1, columnspan=2, sticky="ew", pady=6
        )

        ttk.Label(body, text="PSX host").grid(
            row=1, column=0, sticky="w", pady=6
        )
        ttk.Entry(body, textvariable=host_var, width=42).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=6
        )

        ttk.Label(body, text="PSX port").grid(
            row=2, column=0, sticky="w", pady=6
        )
        ttk.Entry(body, textvariable=port_var, width=42).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=6
        )

        ttk.Label(body, text="PSX route directory").grid(
            row=3, column=0, sticky="w", pady=6
        )
        ttk.Entry(body, textvariable=route_var, width=34).grid(
            row=3, column=1, sticky="ew", pady=6
        )

        def browse_route():
            selected = filedialog.askdirectory(
                parent=win,
                initialdir=route_var.get() or str(APP_DIR),
                title="Select PSX route directory",
            )
            if selected:
                route_var.set(selected)

        ttk.Button(body, text="Browse…", command=browse_route).grid(
            row=3, column=2, padx=(8, 0), pady=6
        )

        ttk.Separator(body).grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(12, 10)
        )
        ttk.Label(
            body,
            text=f"Settings file:\n{INI_PATH}",
            foreground="#666666",
        ).grid(row=5, column=0, columnspan=3, sticky="w")

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")

        def save():
            username = username_var.get().strip()
            host = host_var.get().strip()
            port = port_var.get().strip()
            route_dir = route_var.get().strip()

            if not username:
                messagebox.showerror(
                    "Settings", "SimBrief username is required.", parent=win
                )
                return

            try:
                port_number = int(port)
                if not 1 <= port_number <= 65535:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Settings",
                    "PSX port must be between 1 and 65535.",
                    parent=win,
                )
                return

            if not route_dir:
                messagebox.showerror(
                    "Settings", "PSX route directory is required.", parent=win
                )
                return

            values = {
                "username": username,
                "host": host or DEFAULTS["host"],
                "port": str(port_number),
                "route_dir": route_dir,
            }

            try:
                self.save_config(values)
            except Exception as exc:
                messagebox.showerror("Settings", str(exc), parent=win)
                return

            self.status_var.set("Settings saved")
            win.destroy()

        ttk.Button(buttons, text="Save", command=save).pack(
            side="right", padx=(0, 8)
        )

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # ------------------------------------------------------------------
    # Window geometry
    # ------------------------------------------------------------------

    def _restore_window_geometry(self):
        if not INI_PATH.exists():
            return

        config = configparser.ConfigParser()
        config.read(INI_PATH, encoding="utf-8")

        if not config.has_section("WINDOW"):
            return

        try:
            x = config.getint("WINDOW", "x")
            y = config.getint("WINDOW", "y")
            width = config.getint("WINDOW", "width", fallback=500)
            height = config.getint("WINDOW", "height", fallback=620)
        except (ValueError, configparser.Error):
            return

        width = max(width, 460)
        height = max(height, 540)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _save_window_geometry(self):
        try:
            self.update_idletasks()
            x = self.winfo_x()
            y = self.winfo_y()
            width = self.winfo_width()
            height = self.winfo_height()

            config = configparser.ConfigParser()
            if INI_PATH.exists():
                config.read(INI_PATH, encoding="utf-8")

            config["WINDOW"] = {
                "x": str(x),
                "y": str(y),
                "width": str(width),
                "height": str(height),
            }

            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            with INI_PATH.open("w", encoding="utf-8") as handle:
                config.write(handle)
        except Exception as exc:
            print(f"[WINDOW] Could not save window geometry: {exc}")

    def destroy(self):
        if not self._destroying:
            self._destroying = True
            self._save_window_geometry()
        super().destroy()

    # ------------------------------------------------------------------
    # Route maintenance
    # ------------------------------------------------------------------

    def purge_routes(self):
        route_dir = Path(self.config_values.get("route_dir", "")).expanduser()

        if not route_dir.exists() or not route_dir.is_dir():
            self._show_error_dialog(
                "Purge Routes", f"Route directory does not exist:\n{route_dir}"
            )
            return

        self._show_purge_confirmation(route_dir)

    def _show_purge_confirmation(self, route_dir):
        win = tk.Toplevel(self)
        win.title("Purge Routes")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        body = ttk.Frame(win, padding=20)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text=(
                "Are you sure you want to delete all .route files\n"
                "in this route directory?"
            ),
            justify="left",
        ).pack(anchor="w")

        ttk.Label(
            body,
            text=str(route_dir),
            foreground="#666666",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(12, 18))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")

        def close_no():
            win.grab_release()
            win.destroy()

        def confirm_yes():
            win.grab_release()
            win.destroy()
            self.after_idle(lambda: self._purge_routes_confirmed(route_dir))

        no_button = ttk.Button(buttons, text="No", command=close_no)
        no_button.pack(side="right")
        yes_button = ttk.Button(buttons, text="Yes", command=confirm_yes)
        yes_button.pack(side="right", padx=(0, 8))

        win.protocol("WM_DELETE_WINDOW", close_no)
        win.bind("<Escape>", lambda _event: close_no())
        win.bind("<Return>", lambda _event: confirm_yes())

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        no_button.focus_set()

    def _purge_routes_confirmed(self, route_dir):
        try:
            deleted = 0
            for entry in route_dir.glob("*.route"):
                if entry.is_file():
                    entry.unlink()
                    deleted += 1
        except Exception as exc:
            self._show_error_dialog(
                "Purge Routes", f"Could not purge routes:\n{exc}"
            )
            return

        self.status_var.set(
            f"Purged {deleted} route file{'s' if deleted != 1 else ''}"
        )

    # ------------------------------------------------------------------
    # SimBrief / PSX operations
    # ------------------------------------------------------------------

    @staticmethod
    def _xml_int(root, path, default=0):
        value = backend.optional_text(root, path)
        if not value:
            return int(default)
        return int(round(float(value)))

    @staticmethod
    def _format_duration(seconds):
        seconds = int(round(seconds))
        sign = "-" if seconds < 0 else ""
        minutes = int(round(abs(seconds) / 60.0))
        hours, minutes = divmod(minutes, 60)
        return f"{sign}{hours:02d}{minutes:02d}"

    @staticmethod
    def _format_tonnes(value_kg):
        return f"{float(value_kg) / 1000.0:.1f} t"

    def _build_fuel_table(self, root):
        orig_icao, dest_icao = backend.get_orig_dest(root)
        orig_airport = (
            backend.optional_text(root, ".//origin/iata_code") or orig_icao
        ).upper()
        dest_airport = (
            backend.optional_text(root, ".//destination/iata_code") or dest_icao
        ).upper()

        alternate = root.find(".//alternate")
        alternate_airport = ""
        alternate_time = 0
        if alternate is not None:
            alternate_airport = (
                (alternate.findtext("iata_code") or "").strip()
                or (alternate.findtext("icao_code") or "").strip()
            ).upper()
            ete_text = (alternate.findtext("ete") or "").strip()
            if ete_text:
                alternate_time = int(round(float(ete_text)))

        trip_fuel = self._xml_int(root, ".//fuel/enroute_burn")
        cont_fuel = self._xml_int(root, ".//fuel/contingency")
        alternate_fuel = self._xml_int(root, ".//fuel/alternate_burn")
        reserve_fuel = self._xml_int(root, ".//fuel/reserve")
        min_takeoff_fuel = self._xml_int(root, ".//fuel/min_takeoff")
        extra_fuel = self._xml_int(root, ".//fuel/extra")
        takeoff_fuel = self._xml_int(root, ".//fuel/plan_takeoff")
        taxi_fuel = self._xml_int(root, ".//fuel/taxi")
        block_fuel = self._xml_int(root, ".//fuel/plan_ramp")
        avg_fuel_flow = self._xml_int(root, ".//fuel/avg_fuel_flow")

        trip_time = self._xml_int(root, ".//times/est_time_enroute")
        cont_time = self._xml_int(root, ".//times/contfuel_time")
        reserve_time = self._xml_int(root, ".//times/reserve_time")
        extra_time = self._xml_int(root, ".//times/extrafuel_time")
        taxi_time = self._xml_int(root, ".//times/taxi_out")

        if alternate_time <= 0 and alternate_fuel > 0 and avg_fuel_flow > 0:
            alternate_time = int(
                round(alternate_fuel / avg_fuel_flow * 3600.0)
            )

        minimum_time = trip_time + cont_time + alternate_time + reserve_time
        takeoff_time = max(0, minimum_time + extra_time)
        cont_rule = backend.optional_text(
            root, ".//general/cont_rule", ""
        ).strip()
        cont_label = f"CONT {cont_rule}".strip()

        separator = "-" * 39

        def row(label, airport="", fuel=None, duration=None):
            fuel_text = "" if fuel is None else str(int(fuel))
            time_text = (
                "" if duration is None else self._format_duration(duration)
            )
            return f"{label:<19}{airport:>5}{fuel_text:>8}{time_text:>7}"

        lines = [
            separator,
            f"{'FUEL':<19}{'ARPT':>5}{'FUEL':>8}{'TIME':>7}",
            separator,
            row("TRIP", dest_airport, trip_fuel, trip_time),
            row(cont_label, "", cont_fuel, cont_time),
            row("ALTN", alternate_airport, alternate_fuel, alternate_time),
            row("FINRES", "", reserve_fuel, reserve_time),
            separator,
            row("MINIMUM T/OFF FUEL", "", min_takeoff_fuel, minimum_time),
            separator,
            row("EXTRA", "", extra_fuel, max(extra_time, 0)),
            separator,
            row("T/OFF FUEL", "", takeoff_fuel, takeoff_time),
            row("TAXI", orig_airport, taxi_fuel, taxi_time),
            separator,
            row("BLOCK FUEL", orig_airport, block_fuel, None),
        ]
        return "\n".join(lines)

    def fetch_simbrief(self):
        username = self.config_values.get("username", "").strip()
        if not username:
            messagebox.showinfo(
                APP_NAME,
                "Enter your SimBrief username in Settings first.",
                parent=self,
            )
            self.open_settings()
            return

        self.fetch_button.configure(state="disabled")
        self.upload_button.configure(state="disabled")
        self.status_var.set("Fetching SimBrief…")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            username = self.config_values["username"]
            route_dir = self.config_values["route_dir"]

            xml_text = backend.fetch_simbrief_xml(username)
            root = ET.fromstring(xml_text)

            fetch_status = backend.optional_text(root, ".//fetch/status")
            if fetch_status.lower().startswith("error"):
                raise RuntimeError(fetch_status.split(":", 1)[-1].strip())

            zfw_kg = int(
                float(backend.required_text(root, ".//weights/est_zfw"))
            )
            tow_kg = int(
                float(backend.required_text(root, ".//weights/est_tow"))
            )
            block_kg = int(
                float(backend.required_text(root, ".//fuel/plan_ramp"))
            )
            zfw_lbs = backend.kg_to_lbs_ceil(zfw_kg)
            block_lbs = backend.kg_to_lbs_ceil(block_kg)

            qi123 = f"Qi123={zfw_lbs}\r\n"
            qs438 = backend.build_qs438(block_lbs)
            wind_body = backend.extract_wind_body(root)
            qs498 = backend.build_qs498(wind_body)

            coroute_name, route_path = backend.download_psx_route_file(
                root, route_dir
            )
            route_bytes = Path(route_path).read_bytes()
            self.save_persistent_route(route_bytes)

            callsign, flight_with_runways, readable_date, route, reserve_display = (
                backend.get_flight_summary(root)
            )
            orig, dest = backend.get_orig_dest(root)
            origin_rwy = backend.optional_text(root, ".//origin/plan_rwy")
            dest_rwy = backend.optional_text(root, ".//destination/plan_rwy")

            data = {
                "callsign": callsign,
                "coroute": coroute_name,
                "orig": orig,
                "dest": dest,
                "origin_rwy": origin_rwy,
                "dest_rwy": dest_rwy,
                "flight": f"{orig} - {dest}",
                "flight_with_runways": flight_with_runways,
                "date": readable_date,
                "route": " ".join(route.split()),
                "fuel_table": self._build_fuel_table(root),
                "reserves": reserve_display,
                "route_path": str(route_path),
                "qi123": qi123,
                "qs438": qs438,
                "qs498": qs498,
                "wind_body": wind_body,
                "zfw_kg": zfw_kg,
                "tow_kg": tow_kg,
                "block_kg": block_kg,
                "wind_corridors": backend.count_wind_corridors(wind_body),
            }

            self.save_cached_flight(data)
            self.after(0, self._apply_fetched_data, data)
        except Exception as exc:
            self.after(0, self._operation_failed, "SimBrief", str(exc))

    def _route_with_runways(self, data):
        route = data.get("route", "").strip()
        orig = data.get("orig", "").strip().upper()
        dest = data.get("dest", "").strip().upper()
        origin_rwy = data.get("origin_rwy", "").strip().upper()
        dest_rwy = data.get("dest_rwy", "").strip().upper()

        if (
            not origin_rwy or not dest_rwy
        ) and data.get("flight_with_runways"):
            try:
                left, right = data["flight_with_runways"].split(" - ", 1)
                if not origin_rwy and orig and left.startswith(orig):
                    origin_rwy = left[len(orig):].strip()
                if not dest_rwy and dest and right.startswith(dest):
                    dest_rwy = right[len(dest):].strip()
            except ValueError:
                pass

        parts = []
        if orig and origin_rwy:
            parts.append(f"{orig}/{origin_rwy}")
        if route:
            parts.append(route)
        if dest and dest_rwy:
            parts.append(f"{dest}/{dest_rwy}")
        return " ".join(parts) if parts else route

    def _resize_route_box(self):
        self.update_idletasks()
        text = self.route_text.get("1.0", "end-1c")
        font = tkfont.Font(font=self.route_text.cget("font"))

        try:
            padx = int(float(self.route_text.cget("padx")))
            border = int(float(self.route_text.cget("bd")))
        except (TypeError, ValueError, tk.TclError):
            padx = 8
            border = 1

        usable_width = max(
            1,
            self.route_text.winfo_width()
            - 2 * padx
            - 2 * border
            - 2,
        )
        space_width = font.measure(" ")
        display_lines = 0

        for paragraph in text.split("\n"):
            words = paragraph.split()
            if not words:
                display_lines += 1
                continue

            display_lines += 1
            line_width = 0
            for word in words:
                word_width = font.measure(word)
                candidate_width = (
                    word_width
                    if line_width == 0
                    else line_width + space_width + word_width
                )
                if line_width and candidate_width > usable_width:
                    display_lines += 1
                    line_width = word_width
                else:
                    line_width = candidate_width

        self.route_text.configure(height=max(4, min(10, display_lines)))

    def _apply_fetched_data(self, data, from_cache=False):
        self.current_data = data
        self.callsign_var.set(data["callsign"])
        self.coroute_var.set(data["coroute"])
        self.flight_var.set(data["flight"])
        self.date_var.set(data["date"])
        self.reserves_var.set(data["reserves"])

        self.zfw_var.set(
            self._format_tonnes(data["zfw_kg"])
            if data.get("zfw_kg") is not None
            else "-"
        )
        self.tow_var.set(
            self._format_tonnes(data["tow_kg"])
            if data.get("tow_kg") is not None
            else "-"
        )
        reserve_value = data.get("reserves", "-")
        self.reserve_display_var.set(
            f"{reserve_value} t" if reserve_value != "-" else "-"
        )

        display_route = self._route_with_runways(data)
        self.route_text.configure(state="normal")
        self.route_text.delete("1.0", "end")
        self.route_text.insert("1.0", display_route)
        self.route_text.configure(state="disabled")
        self.after_idle(self._resize_route_box)

        self.fuel_table_var.set(
            data.get(
                "fuel_table",
                "Fuel plan unavailable in saved cache.\n"
                "Fetch SimBrief to refresh.",
            )
        )

        self.fetch_button.configure(state="normal")
        self.upload_button.configure(state="normal")
        self.status_var.set(
            f"{'Restored' if from_cache else 'Loaded'} {data['callsign']}"
        )

    def _get_cached_route_bytes(self):
        if ROUTE_CACHE_PATH.exists() and ROUTE_CACHE_PATH.is_file():
            return ROUTE_CACHE_PATH.read_bytes()

        # Migration path for v1.1c, which stored the route in last_flight.json.
        encoded = self.current_data.get("route_file_b64", "")
        if encoded:
            route_bytes = base64.b64decode(encoded.encode("ascii"))
            self.save_persistent_route(route_bytes)
            self.current_data.pop("route_file_b64", None)
            self.save_cached_flight(self.current_data)
            return route_bytes

        # Migration path for older caches if the route still exists in the
        # configured PSX route directory.
        old_path_text = self.current_data.get("route_path", "")
        if old_path_text:
            old_path = Path(old_path_text).expanduser()
            if old_path.exists() and old_path.is_file():
                route_bytes = old_path.read_bytes()
                self.save_persistent_route(route_bytes)
                return route_bytes

        raise RuntimeError(
            "No saved PSX route file is available for this cached flight. "
            "This cache was created before route-file persistence was added. "
            "Fetch SimBrief once to create last_route.route."
        )

    def _get_cached_orig_dest(self):
        orig = self.current_data.get("orig", "").strip().upper()
        dest = self.current_data.get("dest", "").strip().upper()

        if orig and dest:
            return orig, dest

        coroute = self.current_data.get("coroute", "").strip().upper()
        if len(coroute) >= 8:
            orig = coroute[:4]
            dest = coroute[4:8]
            self.current_data["orig"] = orig
            self.current_data["dest"] = dest
            return orig, dest

        raise RuntimeError(
            "Could not determine origin and destination for the saved route file."
        )

    def _restore_route_file_for_upload(self):
        route_bytes = self._get_cached_route_bytes()
        orig, dest = self._get_cached_orig_dest()

        route_dir = Path(self.config_values["route_dir"]).expanduser()
        route_dir.mkdir(parents=True, exist_ok=True)

        current_coroute = self.current_data.get(
            "coroute", ""
        ).strip().upper()
        current_path = (
            route_dir / f"{current_coroute}_.route"
            if current_coroute
            else None
        )

        if current_path and current_path.exists():
            if current_path.read_bytes() == route_bytes:
                target_coroute = current_coroute
                target_path = current_path
            else:
                target_coroute, _, target_path = backend.next_coroute_name(
                    route_dir, orig, dest
                )
        else:
            # If the route directory was purged, this deliberately starts
            # again at the first free number (normally 01).
            target_coroute, _, target_path = backend.next_coroute_name(
                route_dir, orig, dest
            )

        target_path.write_bytes(route_bytes)

        self.current_data["coroute"] = target_coroute
        self.current_data["route_path"] = str(target_path)
        self.save_cached_flight(self.current_data)

        return target_coroute, target_path

    def upload_current_to_psx(self):
        if not self.current_data:
            return

        self.upload_button.configure(state="disabled")
        self.fetch_button.configure(state="disabled")
        self.status_var.set("Flight INIT…")
        threading.Thread(target=self._upload_worker, daemon=True).start()

    def _upload_worker(self):
        try:
            coroute_name, route_path = self._restore_route_file_for_upload()

            psx_host = self.config_values["host"]
            psx_port = int(self.config_values["port"])
            callsign = self.current_data["callsign"].strip()

            print(f"[PSX] Connecting to {psx_host}:{psx_port}...")
            with socket.create_connection(
                (psx_host, psx_port), timeout=10
            ) as sock:
                print("[PSX] Connected successfully.")
                time.sleep(backend.WAIT_AFTER_CONNECT_SECONDS)

                print("[PSX] Initializing flight from SimBrief data...")

                backend.send_command(sock, "Qh401=58\r\n")
                backend.send_command(sock, f"Qs401={callsign}\r\n")
                backend.send_command(
                    sock, f"Qs075={coroute_name}\r\n", pause=0.1
                )
                backend.send_command(sock, "Qh401=53\r\n")

                backend.send_command(sock, self.current_data["qi123"])
                backend.send_command(sock, self.current_data["qs438"])

                backend.send_command(sock, "Qi220=1\r\n")
                backend.send_command(sock, "Qi220=0\r\n")

                time.sleep(backend.AFTER_FUELING_PAUSE_SECONDS)

                backend.send_command(sock, "Qs497=201\r\n")
                backend.send_command(sock, self.current_data["qs498"])
                backend.send_command(sock, "exit\r\n", pause=0)

            print("[PSX] Flight INIT complete. Disconnected.")
            self.after(
                0, self._upload_complete, coroute_name, str(route_path)
            )
        except Exception as exc:
            self.after(0, self._operation_failed, "PSX", str(exc))

    def _upload_complete(self, coroute_name, route_path):
        self.current_data["coroute"] = coroute_name
        self.current_data["route_path"] = route_path
        self.coroute_var.set(coroute_name)
        self.fetch_button.configure(state="normal")
        self.upload_button.configure(state="normal")
        self.status_var.set("Flight INIT complete")

    def _operation_failed(self, source, message):
        self.fetch_button.configure(state="normal")
        if self.current_data:
            self.upload_button.configure(state="normal")
        self.status_var.set(f"{source} error")
        print(f"[{source.upper()}] {message}")
        self._show_error_dialog(f"{source} Error", message)

    def _show_error_dialog(self, title, message):
        # Custom Tk dialog: avoids the native macOS alert path that can crash
        # in current Tk/macOS combinations.
        win = tk.Toplevel(self)
        win.title(title)
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        body = ttk.Frame(win, padding=20)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text=message,
            wraplength=460,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))

        def close():
            try:
                win.grab_release()
            except tk.TclError:
                pass
            win.destroy()

        ttk.Button(body, text="OK", command=close).pack(anchor="e")
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda _event: close())
        win.bind("<Return>", lambda _event: close())

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


if __name__ == "__main__":
    app = PsxSimbriefGui()
    app.mainloop()
