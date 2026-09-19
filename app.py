"""
Business Card Extractor — NiceGUI frontend + orchestration.

Features
--------
* drag & drop / file-picker upload of one or MANY business-card images
* sequential extraction against a local llama.cpp (llama-server) vision model
  using its OpenAI-compatible /v1/chat/completions endpoint
* live progress + per-card status in a results table with thumbnails
* export the extracted fields to CSV, Excel (XLSX) or JSONL
* in-app backend settings (server URL / model / image size / timeout),
  persisted to settings.json

Run:
    python app.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from nicegui import run, ui
from PIL import Image

import exporter
import extractor
from config import (
    APP_NAME,
    DEFAULT_MAX_SIZE,
    DEFAULT_MODEL,
    DEFAULT_SERVER_URL,
    DEFAULT_TIMEOUT,
    FIELDS,
    HOST,
    OUTPUT_DIR,
    PORT,
    SETTINGS_FILE,
    UPLOAD_DIR,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

STATUS_META = {
    "pending": ("Queued", "#94a3b8"),
    "processing": ("Processing…", "#38bdf8"),
    "done": ("Done", "#34d399"),
    "error": ("Error", "#f87171"),
}

THUMB_SLOT = """
<div class="flex flex-col items-center gap-1" style="min-width:110px">
    <img :src="props.row.thumb" style="height:52px;border-radius:6px;object-fit:cover;border:1px solid rgba(148,163,184,.4)">
    <div class="text-caption" style="max-width:112px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ props.row.filename }}</div>
</div>
"""

STATUS_SLOT = """
<div class="flex flex-col items-center" style="min-width:110px">
    <div class="text-sm font-medium" :style="{color: props.row.status_color}">{{ props.row.status }}</div>
    <div v-if="props.row.error" class="text-xs" style="color:#f87171;max-width:190px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" :title="props.row.error">{{ props.row.error }}</div>
</div>
"""

COLUMNS = (
    [
        {
            "name": "thumbnail",
            "label": "Card",
            "field": "thumbnail",
            "align": "left",
            "sortable": False,
            "width": "140",
        }
    ]
    + [
        {
            "name": field,
            "label": field,
            "field": field,
            "align": "left",
            "sortable": True,
            "width": "185"
            if field in ("Position / Job Title", "Company", "Location")
            else "150",
        }
        for field in FIELDS
    ]
    + [
        {
            "name": "status",
            "label": "Status",
            "field": "status",
            "align": "center",
            "sortable": True,
            "width": "135",
        },
        {
            "name": "duration",
            "label": "Time (s)",
            "field": "duration",
            "align": "right",
            "sortable": True,
            "width": "90",
        },
    ]
)


class CardApp:
    """Application state + UI logic (single-user local tool)."""

    def __init__(self) -> None:
        self.cards: list[dict] = []
        self.settings = self._load_settings()
        self._processing = False
        self._cancel_requested = False

        # UI references (populated in build_page)
        self.results_box = None
        self.summary_box = None
        self.progress_bar = None
        self.progress_label = None
        self.server_dot = None
        self.server_text = None
        self.process_btn = None
        self.cancel_btn = None

    # ------------------------------------------------------------ settings

    def _default_settings(self) -> dict:
        return {
            "server_url": DEFAULT_SERVER_URL,
            "model": DEFAULT_MODEL,
            "max_size": DEFAULT_MAX_SIZE,
            "timeout": DEFAULT_TIMEOUT,
            "auto_process": True,
        }

    def _load_settings(self) -> dict:
        settings = self._default_settings()
        if SETTINGS_FILE.exists():
            try:
                settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return settings

    def save_settings(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    # ----------------------------------------------------------------- UI

    def build_page(self) -> None:
        with ui.header().classes(
            "items-center bg-transparent px-6 py-3 border-b border-gray-200 dark:border-gray-700"
        ):
            ui.icon("badge").classes("text-3xl text-indigo-500")
            ui.label(APP_NAME).classes("text-xl font-semibold")
            ui.label("llama.cpp · Qwen3-VL · local").classes(
                "text-xs text-gray-400 mb-1"
            )
            ui.space()
            self.server_dot = ui.element("div").style(
                "width:10px;height:10px;border-radius:9999px;background:#94a3b8"
            )
            self.server_text = ui.label("checking server…").classes(
                "text-xs text-gray-400 max-w-[380px] truncate"
            )
            ui.button(icon="dark_mode", on_click=self._toggle_theme).props(
                "flat round dense"
            )

        with ui.column().classes("w-full max-w-[1400px] mx-auto p-6 gap-5"):
            # ---- upload
            with ui.card().classes("w-full"):
                ui.upload(
                    on_upload=self.on_upload,
                    multiple=True,
                    auto_upload=True,
                    max_file_size=60 * 1024 * 1024,
                    max_files=200,
                    label="Drag & drop business-card images here — multiple files at once are fine",
                ).classes("w-full").props('accept="image/*"')

            # ---- controls
            with ui.row().classes("w-full items-center gap-2"):
                self.process_btn = ui.button(
                    "Process cards", icon="play_arrow", on_click=self.start_processing
                ).props("color=primary")
                self.cancel_btn = (
                    ui.button("Cancel", icon="stop", on_click=self.request_cancel)
                    .props("flat color=red")
                    .disable()
                )
                ui.button(
                    "Clear all", icon="delete_sweep", on_click=self.clear_all
                ).props("flat")
                ui.space()
                ui.button(
                    "Download CSV",
                    icon="download",
                    on_click=lambda: self.download("csv"),
                ).props("outline")
                ui.button(
                    "Download Excel (XLSX)",
                    icon="table_chart",
                    on_click=lambda: self.download("xlsx"),
                ).props("outline color=indigo")
                ui.button(
                    "Download JSONL",
                    icon="data_object",
                    on_click=lambda: self.download("json"),
                ).props("outline")
                ui.space()
                ui.button(
                    "Backend settings", icon="settings", on_click=self.open_settings
                ).props("flat dense")

            # ---- progress
            with ui.row().classes("w-full items-center gap-3"):
                self.progress_bar = (
                    ui.linear_progress(value=0, show_value=False)
                    .classes("flex-grow")
                    .props("instant-feedback round")
                )
                self.progress_label = ui.label("").classes(
                    "text-xs text-gray-500 whitespace-nowrap"
                )

            # ---- summary chips
            self.summary_box = ui.row().classes("w-full items-center gap-2")

            # ---- results table
            self.results_box = ui.column().classes("w-full")
            self.render_results()
            self.render_summary()

            # ---- footer
            with ui.expansion(
                "llama-server reference command (your current setup)", icon="terminal"
            ).classes("w-full text-sm"):
                ui.code(
                    ".\\llama-server.exe -hf Qwen/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M -ngl 0 -t 8 -c 8192 -b 2048 -ub 512 -np 1 --port 8080",
                    language="bash",
                ).classes("w-full")
                ui.label(
                    "The app calls {url}/v1/chat/completions — change it in Backend settings if your port differs.".format(
                        url=DEFAULT_SERVER_URL
                    )
                ).classes("text-xs text-gray-400")

        ui.timer(0.4, self._probe_server, once=True)

    # ------------------------------------------------------- small helpers

    def _toggle_theme(self) -> None:
        try:
            ui.dark_mode().toggle()
        except Exception:
            pass

    async def _probe_server(self) -> None:
        try:
            ok, info = await run.io_bound(
                extractor.check_server, self.settings["server_url"]
            )
            self.set_server_status(ok, info)
        except Exception:
            pass

    def set_server_status(self, ok: bool, info: str) -> None:
        try:
            if self.server_dot is None:
                return
            color = "#22c55e" if ok else "#ef4444"
            self.server_dot.style(
                "width:10px;height:10px;border-radius:9999px;background:" + color
            )
            self.server_text.text = (
                info if ok else f"{self.settings['server_url']} · {info}"
            )
        except Exception:
            pass

    def _set_buttons_busy(self, busy: bool) -> None:
        try:
            if self.process_btn is not None:
                self.process_btn.set_enabled(not busy)
            if self.cancel_btn is not None:
                self.cancel_btn.set_enabled(busy)
        except Exception:
            pass

    # ------------------------------------------------------------- upload

    async def on_upload(self, e) -> None:
        name = e.file.name
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTS:
            ui.notify(
                f"{name} skipped — unsupported file type ({ext}).", type="warning"
            )
            return

        card_id = uuid.uuid4().hex[:10]
        dest = UPLOAD_DIR / f"{card_id}.jpg"
        try:
            blob = await e.file.read()
            with Image.open(io.BytesIO(blob)) as img:
                img.load()
                rgb = img.convert("RGB")
                rgb.save(dest, format="JPEG", quality=90)  # normalised copy on disk
                rgb.thumbnail((240, 240))  # small in-browser thumb
                buf = io.BytesIO()
                rgb.save(buf, format="JPEG", quality=80)
            thumb_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as exc:
            ui.notify(
                f"{name} skipped — could not read image ({exc}).", type="negative"
            )
            return

        self.cards.append(
            {
                "id": card_id,
                "filename": name,
                "path": str(dest).replace("\\", "/"),
                "thumb": f"data:image/jpeg;base64,{thumb_b64}",
                "status": "pending",
                "data": None,
                "meta": None,
                "error": "",
                "duration": 0.0,
            }
        )
        self.render_results()
        self.render_summary()
        if self.settings.get("auto_process", True):
            self.start_processing()

    # ---------------------------------------------------------- processing

    def start_processing(self) -> None:
        if self._processing:
            ui.notify("Processing is already running.", type="info")
            return
        if not any(c["status"] in ("pending", "error") for c in self.cards):
            ui.notify(
                "Nothing to process — upload some card images first.", type="info"
            )
            return
        self._cancel_requested = False
        self._processing = True
        self._set_buttons_busy(True)
        asyncio.create_task(self._process_loop())

    def request_cancel(self) -> None:
        if self._processing:
            self._cancel_requested = True
            ui.notify("Cancelling after the current card finishes…", type="info")

    def clear_all(self) -> None:
        if not self.cards:
            return
        self._cancel_requested = True
        self.cards.clear()
        self.render_results()
        self.render_summary()
        self._update_progress("")

    async def _process_loop(self) -> None:
        """Process queued cards one at a time (llama-server runs -np 1)."""
        try:
            while not self._cancel_requested:
                card = next((c for c in self.cards if c["status"] == "pending"), None)
                if card is None:
                    break
                card["status"] = "processing"
                self._update_progress(f"extracting {card['filename']}")
                self.render_results()
                started = time.perf_counter()
                try:
                    fields, meta = await run.io_bound(
                        extractor.extract_from_image,
                        card["path"],
                        self.settings["server_url"],
                        self.settings["model"],
                        int(self.settings["max_size"]),
                        int(self.settings["timeout"]),
                    )
                    card["data"] = fields
                    card["meta"] = meta
                    card["status"] = "done"
                except Exception as exc:
                    card["status"] = "error"
                    card["error"] = f"{type(exc).__name__}: {exc}"[:300]
                finally:
                    card["duration"] = round(time.perf_counter() - started, 1)
                self.render_results()
        finally:
            self._processing = False
            self._set_buttons_busy(False)
            self._update_progress("")
            self.render_summary()
            if self.cards:
                done = sum(1 for c in self.cards if c["status"] == "done")
                failed = sum(1 for c in self.cards if c["status"] == "error")
                # background task has no slot context — enter one explicitly
                try:
                    with self.results_box:
                        ui.notify(
                            f"Finished — {done} extracted, {failed} failed.",
                            type="positive" if failed == 0 else "warning",
                        )
                except Exception:
                    pass

    # ------------------------------------------------------------- render

    def _row_for(self, card: dict) -> dict:
        text, color = STATUS_META.get(card["status"], (card["status"], "#94a3b8"))
        data = card.get("data") or {}
        row = {
            "id": card["id"],
            "thumb": card["thumb"],
            "filename": card["filename"],
            "status": text,
            "status_color": color,
            "error": (card.get("error") or "")[:200],
            "duration": f"{card['duration']:.1f}" if card.get("duration") else "",
        }
        for field in FIELDS:
            row[field] = data.get(field, "Null") if card["status"] == "done" else "—"
        return row

    def render_results(self) -> None:
        try:
            if self.results_box is None:
                return
            self.results_box.clear()
            with self.results_box:
                if not self.cards:
                    with ui.card().classes("w-full items-center"):
                        ui.icon("photo_library").classes(
                            "text-5xl text-gray-300 dark:text-gray-600"
                        )
                        ui.label(
                            "No cards yet. Upload one or more business-card images above."
                        ).classes("text-gray-400")
                    return
                rows = [self._row_for(c) for c in self.cards]
                with ui.card().classes("w-full"):
                    table = ui.table(
                        rows=rows,
                        columns=COLUMNS,
                        row_key="id",
                        pagination={"rowsPerPage": 10, "options": [5, 10, 25, 50]},
                    ).classes("w-full")
                    table.add_slot("body-cell-thumbnail", THUMB_SLOT)
                    table.add_slot("body-cell-status", STATUS_SLOT)
        except Exception:
            pass

    def render_summary(self) -> None:
        try:
            if self.summary_box is None:
                return
            self.summary_box.clear()
            total = len(self.cards)
            done = sum(1 for c in self.cards if c["status"] == "done")
            failed = sum(1 for c in self.cards if c["status"] == "error")
            queued = sum(
                1 for c in self.cards if c["status"] in ("pending", "processing")
            )
            times = [
                c["duration"]
                for c in self.cards
                if c["status"] == "done" and c.get("duration")
            ]
            avg = sum(times) / len(times) if times else 0

            def chip(text: str, classes: str) -> None:
                ui.label(text).classes(
                    "px-3 py-1 rounded-full text-xs font-medium border " + classes
                )

            # explicit slot context — this runs from background tasks too
            with self.summary_box:
                chip(
                    f"Total {total}",
                    "border-gray-300 text-gray-600 dark:text-gray-300 dark:border-gray-600",
                )
                chip(
                    f"Extracted {done}",
                    "border-emerald-300 text-emerald-600 dark:text-emerald-400",
                )
                if failed:
                    chip(
                        f"Failed {failed}",
                        "border-red-300 text-red-600 dark:text-red-400",
                    )
                if queued:
                    chip(
                        f"In queue {queued}",
                        "border-sky-300 text-sky-600 dark:text-sky-400",
                    )
                if avg:
                    chip(
                        f"Avg {avg:.1f}s / card",
                        "border-indigo-300 text-indigo-600 dark:text-indigo-400",
                    )
        except Exception:
            pass

    def _update_progress(self, detail: str = "") -> None:
        try:
            if self.progress_bar is None:
                return
            total = len(self.cards)
            finished = sum(1 for c in self.cards if c["status"] in ("done", "error"))
            self.progress_bar.value = finished / total if total else 0
            self.progress_label.text = f"{finished}/{total} done" + (
                f" · {detail}" if detail else ""
            )
        except Exception:
            pass

    # ----------------------------------------------------------- settings

    def open_settings(self) -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[440px]"):
            ui.label("Backend settings").classes("text-lg font-semibold")
            server_in = (
                ui.input("llama-server URL", value=self.settings["server_url"])
                .props("dense outlined")
                .classes("w-full")
            )
            model_in = (
                ui.input("Model (payload 'model' field)", value=self.settings["model"])
                .props("dense outlined")
                .classes("w-full")
            )
            max_in = (
                ui.number(
                    "Max image size — longest side (px)",
                    value=self.settings["max_size"],
                    min=256,
                    max=2048,
                    step=128,
                )
                .props("dense outlined")
                .classes("w-full")
            )
            timeout_in = (
                ui.number(
                    "Per-card request timeout (s)",
                    value=self.settings["timeout"],
                    min=10,
                    max=3600,
                    step=10,
                )
                .props("dense outlined")
                .classes("w-full")
            )
            auto_sw = ui.switch(
                "Start extraction automatically on upload",
                value=self.settings["auto_process"],
            )

            with ui.row().classes("w-full items-center justify-end gap-2"):

                async def test_connection() -> None:
                    target = (server_in.value or "").strip() or "http://localhost:8080"
                    ok, info = await run.io_bound(extractor.check_server, target)
                    self.set_server_status(ok, info)
                    ui.notify(
                        f"Server check: {info}", type="positive" if ok else "negative"
                    )

                ui.button(
                    "Test connection", icon="network_check", on_click=test_connection
                ).props("flat")

                def save() -> None:
                    self.settings.update(
                        {
                            "server_url": (server_in.value or "").strip()
                            or DEFAULT_SERVER_URL,
                            "model": (model_in.value or "").strip() or DEFAULT_MODEL,
                            "max_size": int(max_in.value or DEFAULT_MAX_SIZE),
                            "timeout": int(timeout_in.value or DEFAULT_TIMEOUT),
                            "auto_process": bool(auto_sw.value),
                        }
                    )
                    self.save_settings()
                    dlg.close()
                    ui.notify("Settings saved to settings.json", type="positive")

                ui.button("Save", on_click=save).props("color=primary")
        dlg.open()

    # ------------------------------------------------------------ export

    def download(self, fmt: str) -> None:
        ready = [c for c in self.cards if c["status"] == "done" and c.get("data")]
        if not ready:
            ui.notify(
                "Nothing to export yet — extract at least one card first.",
                type="warning",
            )
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = OUTPUT_DIR / f"business_cards_{stamp}.{fmt}"
        try:
            if fmt == "csv":
                exporter.save_csv(target, ready)
            elif fmt == "xlsx":
                exporter.save_xlsx(target, ready)
            else:
                exporter.save_jsonl(target, ready)
        except Exception as exc:
            ui.notify(f"Export failed: {exc}", type="negative")
            return
        ui.download(str(target))
        ui.notify(f"Exported {len(ready)} card(s) → {target.name}", type="positive")


APP = CardApp()


@ui.page("/")
def index() -> None:
    APP.build_page()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title=APP_NAME, host=HOST, port=PORT, reload=False, show=False, favicon="🪪")
