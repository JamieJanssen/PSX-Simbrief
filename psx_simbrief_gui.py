# psx_simbrief_gui.py
# Clipboard-style GUI for PSX Simbrief

from __future__ import annotations

import configparser
import json
import shutil
import sys
import threading
import tkinter as tk
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import psx_simbrief as backend


VERSION = "1.1b"
APP_NAME = "PSX Simbrief"
APP_DIR = Path(__file__).resolve().parent

if sys.platform == "darwin":
    SETTINGS_DIR = Path.home() / "Library/Application Support/PSX Simbrief"
else:
    SETTINGS_DIR = APP_DIR

INI_PATH = SETTINGS_DIR / "psx_simbrief.ini"
CACHE_PATH = SETTINGS_DIR / "last_flight.json"

DEFAULTS = {
    "username": "",
    "host": "127.0.0.1",
    "port": "10747",
    "route_dir": str(APP_DIR),
}


class PsxSimbriefGui(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("660x650")
        self.minsize(600, 560)
        self.configure(bg="#000000")

        self.config_values = self.load_config()
        self.current_data = None

        self.callsign_var = tk.StringVar(value="-")
        self.coroute_var = tk.StringVar(value="-")
        self.flight_var = tk.StringVar(value="-")
        self.date_var = tk.StringVar(value="-")
        self.reserves_var = tk.StringVar(value="-")
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.load_cached_flight()

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
            values["username"] = config.get("SIMBRIEF", "username", fallback="").strip()
            values["host"] = config.get("PSX", "host", fallback=DEFAULTS["host"]).strip()
            values["port"] = config.get("PSX", "port", fallback=DEFAULTS["port"]).strip()
            values["route_dir"] = config.get("PSX", "route_dir", fallback=DEFAULTS["route_dir"]).strip()

        return values

    def save_config(self, values):
        config = configparser.ConfigParser()
        config["SIMBRIEF"] = {"username": values["username"]}
        config["PSX"] = {
            "host": values["host"],
            "port": values["port"],
            "route_dir": values["route_dir"],
        }

        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with INI_PATH.open("w", encoding="utf-8") as handle:
            config.write(handle)

        self.config_values = values.copy()

    def save_cached_flight(self, data):
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = CACHE_PATH.with_suffix(".tmp")

        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

        temp_path.replace(CACHE_PATH)

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

            self._apply_fetched_data(data, from_cache=True)
        except Exception as exc:
            self.status_var.set("Could not load saved flight")
            print(f"[CACHE] Could not load {CACHE_PATH}: {exc}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Black clipboard fills the complete client area. This deliberately
        # avoids the grey top and side gutters from the earlier layout.
        board = tk.Frame(self, bg="#000000", bd=0)
        board.pack(fill="both", expand=True)

        self.menu_button = tk.Button(
            board,
            text="☰",
            font=("Helvetica Neue", 17),
            width=3,
            bd=0,
            relief="flat",
            bg="#000000",
            fg="#ffffff",
            activebackground="#222222",
            activeforeground="#ffffff",
            highlightthickness=0,
            command=self.show_menu,
        )
        self.menu_button.place(relx=1.0, x=-12, y=10, anchor="ne")

        # Simple metal clipboard clip.
        clip = tk.Frame(board, bg="#777777", width=150, height=24, bd=0)
        clip.place(relx=0.5, y=10, anchor="n")
        clip.pack_propagate(False)
        tk.Frame(clip, bg="#b5b5b5", height=5).pack(fill="x", padx=22, pady=(5, 0))

        paper = tk.Frame(board, bg="#ffffff", bd=0)
        paper.pack(fill="both", expand=True, padx=20, pady=(32, 20))

        content = tk.Frame(paper, bg="#ffffff")
        content.pack(fill="both", expand=True, padx=28, pady=(28, 10))

        self._info_row(content, "FLT NO", self.callsign_var, 0)
        self._info_row(content, "CO ROUTE", self.coroute_var, 1)

        tk.Frame(content, bg="#d0d0d0", height=1).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(10, 14)
        )

        self._info_row(content, "Flight", self.flight_var, 3)
        self._info_row(content, "Date", self.date_var, 4)

        tk.Label(
            content,
            text="Route",
            font=("Menlo", 12, "bold"),
            bg="#ffffff",
            fg="#111111",
            anchor="w",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(18, 6))

        route_frame = tk.Frame(content, bg="#ffffff")
        route_frame.grid(row=6, column=0, columnspan=2, sticky="nsew")

        self.route_text = tk.Text(
            route_frame,
            height=9,
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
        self.route_text.pack(fill="both", expand=True)
        self.route_text.insert("1.0", "-")
        self.route_text.configure(state="disabled")

        reserve_row = tk.Frame(content, bg="#ffffff")
        reserve_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(18, 0))

        tk.Label(
            reserve_row,
            text="RESERVES:",
            font=("Menlo", 12, "bold"),
            bg="#ffffff",
            fg="#111111",
        ).pack(side="left")
        tk.Label(
            reserve_row,
            textvariable=self.reserves_var,
            font=("Menlo", 12),
            bg="#ffffff",
            fg="#111111",
        ).pack(side="left", padx=(10, 0))

        content.columnconfigure(1, weight=1)
        content.rowconfigure(6, weight=1)

        bottom = tk.Frame(paper, bg="#ffffff")
        bottom.pack(fill="x", padx=28, pady=(2, 18))

        self.fetch_button = ttk.Button(bottom, text="Fetch SimBrief", command=self.fetch_simbrief)
        self.fetch_button.pack(side="left")

        self.upload_button = ttk.Button(
            bottom,
            text="Upload to PSX",
            command=self.upload_current_to_psx,
            state="disabled",
        )
        self.upload_button.pack(side="left", padx=(8, 0))

        ttk.Button(bottom, text="Copy Route", command=self.copy_route).pack(side="left", padx=(8, 0))

        tk.Label(
            bottom,
            textvariable=self.status_var,
            font=("Helvetica Neue", 10),
            bg="#ffffff",
            fg="#555555",
            anchor="e",
        ).pack(side="right", padx=(12, 0))

    def _info_row(self, parent, label, variable, row):
        tk.Label(
            parent,
            text=f"{label}:",
            font=("Menlo", 12, "bold"),
            bg="#ffffff",
            fg="#111111",
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=2)

        tk.Label(
            parent,
            textvariable=variable,
            font=("Menlo", 12),
            bg="#ffffff",
            fg="#111111",
            anchor="w",
        ).grid(row=row, column=1, sticky="w", padx=(14, 0), pady=2)

    def show_menu(self):
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Fetch SimBrief", command=self.fetch_simbrief)
        menu.add_command(label="Upload to PSX", command=self.upload_current_to_psx)
        menu.add_separator()
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

        ttk.Label(body, text="SimBrief username").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(body, textvariable=username_var, width=42).grid(
            row=0, column=1, columnspan=2, sticky="ew", pady=6
        )

        ttk.Label(body, text="PSX host").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(body, textvariable=host_var, width=42).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=6
        )

        ttk.Label(body, text="PSX port").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(body, textvariable=port_var, width=42).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=6
        )

        ttk.Label(body, text="PSX route directory").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(body, textvariable=route_var, width=34).grid(row=3, column=1, sticky="ew", pady=6)

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

        ttk.Separator(body).grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 10))
        ttk.Label(body, text=f"Settings file:\n{INI_PATH}", foreground="#666666").grid(
            row=5, column=0, columnspan=3, sticky="w"
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")

        def save():
            username = username_var.get().strip()
            host = host_var.get().strip()
            port = port_var.get().strip()
            route_dir = route_var.get().strip()

            if not username:
                messagebox.showerror("Settings", "SimBrief username is required.", parent=win)
                return

            try:
                port_number = int(port)
                if not 1 <= port_number <= 65535:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Settings", "PSX port must be between 1 and 65535.", parent=win)
                return

            if not route_dir:
                messagebox.showerror("Settings", "PSX route directory is required.", parent=win)
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

        ttk.Button(buttons, text="Save", command=save).pack(side="right", padx=(0, 8))

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # ------------------------------------------------------------------
    # Route maintenance
    # ------------------------------------------------------------------

    def purge_routes(self):
        route_dir = Path(self.config_values.get("route_dir", "")).expanduser()

        if not route_dir.exists() or not route_dir.is_dir():
            messagebox.showerror(
                APP_NAME,
                f"Route directory does not exist:\n{route_dir}",
                parent=self,
            )
            return

        self._show_purge_confirmation(route_dir)

    def _show_purge_confirmation(self, route_dir):
        # Do not use tkinter.messagebox.askyesno here. On current macOS/Tk
        # combinations the native alert can crash inside GameControllerUI.
        win = tk.Toplevel(self)
        win.title("Purge Routes")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        body = ttk.Frame(win, padding=20)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="Are you sure you want to delete everything\nin this route directory?",
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
            for entry in route_dir.iterdir():
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
                deleted += 1
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not purge routes:\n{exc}", parent=self)
            return

        self.status_var.set(f"Purged {deleted} route item{'s' if deleted != 1 else ''}")

    # ------------------------------------------------------------------
    # SimBrief / PSX operations
    # ------------------------------------------------------------------

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

            zfw_kg = int(float(backend.required_text(root, ".//weights/est_zfw")))
            block_kg = int(float(backend.required_text(root, ".//fuel/plan_ramp")))
            zfw_lbs = backend.kg_to_lbs_ceil(zfw_kg)
            block_lbs = backend.kg_to_lbs_ceil(block_kg)

            qi123 = f"Qi123={zfw_lbs}\r\n"
            qs438 = backend.build_qs438(block_lbs)
            wind_body = backend.extract_wind_body(root)
            qs498 = backend.build_qs498(wind_body)

            coroute_name, route_path = backend.download_psx_route_file(root, route_dir)
            callsign, flight_with_runways, readable_date, route, reserve_display = (
                backend.get_flight_summary(root)
            )
            orig, dest = backend.get_orig_dest(root)

            data = {
                "callsign": callsign,
                "coroute": coroute_name,
                "flight": f"{orig} - {dest}",
                "flight_with_runways": flight_with_runways,
                "date": readable_date,
                "route": " ".join(route.split()),
                "reserves": reserve_display,
                "route_path": str(route_path),
                "qi123": qi123,
                "qs438": qs438,
                "qs498": qs498,
                "wind_body": wind_body,
                "zfw_kg": zfw_kg,
                "block_kg": block_kg,
                "wind_corridors": backend.count_wind_corridors(wind_body),
            }

            self.save_cached_flight(data)
            self.after(0, self._apply_fetched_data, data)
        except Exception as exc:
            self.after(0, self._operation_failed, "SimBrief", str(exc))

    def _apply_fetched_data(self, data, from_cache=False):
        self.current_data = data
        self.callsign_var.set(data["callsign"])
        self.coroute_var.set(data["coroute"])
        self.flight_var.set(data["flight"])
        self.date_var.set(data["date"])
        self.reserves_var.set(data["reserves"])

        self.route_text.configure(state="normal")
        self.route_text.delete("1.0", "end")
        self.route_text.insert("1.0", data["route"])
        self.route_text.configure(state="disabled")

        self.fetch_button.configure(state="normal")
        self.upload_button.configure(state="normal")
        self.status_var.set(f"{'Restored' if from_cache else 'Loaded'} {data['callsign']}")

    def upload_current_to_psx(self):
        if not self.current_data:
            return

        self.upload_button.configure(state="disabled")
        self.fetch_button.configure(state="disabled")
        self.status_var.set("Uploading to PSX…")
        threading.Thread(target=self._upload_worker, daemon=True).start()

    def _upload_worker(self):
        try:
            backend.upload_to_psx(
                self.config_values["host"],
                int(self.config_values["port"]),
                self.current_data["qi123"],
                self.current_data["qs438"],
                self.current_data["qs498"],
            )
            self.after(0, self._upload_complete)
        except Exception as exc:
            self.after(0, self._operation_failed, "PSX", str(exc))

    def _upload_complete(self):
        self.fetch_button.configure(state="normal")
        self.upload_button.configure(state="normal")
        self.status_var.set("PSX upload complete")

    def _operation_failed(self, source, message):
        self.fetch_button.configure(state="normal")
        if self.current_data:
            self.upload_button.configure(state="normal")
        self.status_var.set(f"{source} error")
        messagebox.showerror(APP_NAME, message, parent=self)

    def copy_route(self):
        if not self.current_data:
            return

        self.clipboard_clear()
        self.clipboard_append(self.current_data["route"])
        self.update()
        self.status_var.set("Route copied")


if __name__ == "__main__":
    app = PsxSimbriefGui()
    app.mainloop()
