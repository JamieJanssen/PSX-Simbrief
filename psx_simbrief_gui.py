# psx_simbrief_gui.py
# GUI entry point for PSX Simbrief

from __future__ import annotations

import configparser
import socket
import threading
import time
import tkinter as tk
import xml.etree.ElementTree as ET
from pathlib import Path

import psx_simbrief as backend
import psx_simbrief_gui_core as core


VERSION = "1.1j"
APP_NAME = core.APP_NAME
INI_PATH = core.INI_PATH


class PsxSimbriefGui(core.PsxSimbriefGui):
    def __init__(self):
        self._destroying = False
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("500x700")
        self.minsize(460, 620)
        self._restore_window_geometry()

    def _build_ui(self):
        super()._build_ui()
        self.upload_button.configure(text="Flight INIT")
        self.fuel_table_var = tk.StringVar(self, value="")

        # Replace the native macOS Tk button with a compact Canvas so the
        # hamburger fits completely inside the black clipboard area.
        old_menu_button = self.menu_button
        board = old_menu_button.master
        old_menu_button.destroy()

        self.menu_button = tk.Canvas(
            board,
            width=24,
            height=28,
            bg="#000000",
            bd=0,
            relief="flat",
            highlightthickness=0,
            cursor="hand2",
        )
        for y in (8, 14, 20):
            self.menu_button.create_line(
                6,
                y,
                18,
                y,
                fill="#ffffff",
                width=2,
            )
        self.menu_button.bind("<Button-1>", lambda _event: self.show_menu())
        self.menu_button.place(relx=1.0, x=-8, y=2, anchor="ne")

        route_frame = self.route_text.master
        content = route_frame.master
        content.rowconfigure(6, weight=0)
        self.route_text.configure(height=6)

        reserve_row = None
        for child in content.winfo_children():
            info = child.grid_info()
            if info and int(info.get("row", -1)) == 7:
                reserve_row = child
                break

        if reserve_row is not None:
            reserve_row.grid_configure(row=8, pady=(10, 0))
            tk.Label(
                reserve_row,
                text="t",
                font=("Menlo", 8),
                bg="#ffffff",
                fg="#111111",
            ).pack(side="left", padx=(3, 0), pady=(5, 0))

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
            row=7,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(14, 0),
        )

        # Copy Route is no longer needed in the compact layout.
        bottom = self.upload_button.master
        for child in bottom.winfo_children():
            try:
                if child.cget("text") == "Copy Route":
                    child.destroy()
                    break
            except tk.TclError:
                pass

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

    def save_config(self, values):
        super().save_config(values)
        self._save_window_geometry()

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
            height = config.getint("WINDOW", "height", fallback=700)
        except (ValueError, configparser.Error):
            return

        width = max(width, 460)
        height = max(height, 620)
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

            core.SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            with INI_PATH.open("w", encoding="utf-8") as handle:
                config.write(handle)
        except Exception as exc:
            print(f"[WINDOW] Could not save window geometry: {exc}")

    def destroy(self):
        if not self._destroying:
            self._destroying = True
            self._save_window_geometry()
        super().destroy()

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

    def _build_fuel_table(self, root):
        orig_icao, dest_icao = backend.get_orig_dest(root)
        orig_airport = (
            backend.optional_text(root, ".//origin/iata_code")
            or orig_icao
        ).upper()
        dest_airport = (
            backend.optional_text(root, ".//destination/iata_code")
            or dest_icao
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
            alternate_time = int(round(alternate_fuel / avg_fuel_flow * 3600.0))

        minimum_time = trip_time + cont_time + alternate_time + reserve_time
        takeoff_time = max(0, minimum_time + extra_time)
        cont_rule = backend.optional_text(root, ".//general/cont_rule", "").strip()
        cont_label = f"CONT {cont_rule}".strip()

        separator = "-" * 39

        def row(label, airport="", fuel=None, duration=None):
            fuel_text = "" if fuel is None else str(int(fuel))
            time_text = "" if duration is None else self._format_duration(duration)
            return f"{label:<19}{airport:>5}{fuel_text:>8}{time_text:>7}"

        header = f"{'FUEL':<19}{'ARPT':>5}{'FUEL':>8}{'TIME':>7}"
        lines = [
            separator,
            header,
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

        if (not origin_rwy or not dest_rwy) and data.get("flight_with_runways"):
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

    def _apply_fetched_data(self, data, from_cache=False):
        super()._apply_fetched_data(data, from_cache=from_cache)

        display_route = self._route_with_runways(data)
        self.route_text.configure(state="normal")
        self.route_text.delete("1.0", "end")
        self.route_text.insert("1.0", display_route)
        self.route_text.configure(state="disabled")

        self.fuel_table_var.set(
            data.get(
                "fuel_table",
                "Fuel plan unavailable in saved cache.\nFetch SimBrief to refresh.",
            )
        )

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
            with socket.create_connection((psx_host, psx_port), timeout=10) as sock:
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
            self.after(0, self._upload_complete, coroute_name, str(route_path))
        except Exception as exc:
            self.after(0, self._operation_failed, "PSX", str(exc))

    def _upload_complete(self, coroute_name, route_path):
        self.current_data["coroute"] = coroute_name
        self.current_data["route_path"] = route_path
        self.coroute_var.set(coroute_name)
        self.fetch_button.configure(state="normal")
        self.upload_button.configure(state="normal")
        self.status_var.set("Flight INIT complete")


if __name__ == "__main__":
    app = PsxSimbriefGui()
    app.mainloop()
