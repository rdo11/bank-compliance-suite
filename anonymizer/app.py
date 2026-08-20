"""Offline GDPR Clipboard Anonymizer - offline desktop app.

A zero-trust, fully offline compliance tool. Reads the system clipboard,
redacts Danish PII (CPR, CVR, bank accounts, phones, emails, credit cards and
custom words) with a regex engine, and shows a before/after preview with the
changed spans highlighted. No LLMs, no network calls, standard library + pyperclip.

Run:
    python app.py            (macOS / Linux)
    double-click run.bat     (Windows, console hidden)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# repo root -> shared core/ (audit logger); in frozen (PyInstaller) builds
# the core package is bundled alongside the executable instead
if getattr(sys, "frozen", False):
    sys.path.insert(0, getattr(sys, "_MEIPASS", "."))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None

from core.audit import AuditLogger

from redactor import (
    CATEGORY_LABELS,
    REDACTED_LABELS,
    RULE_ORDER,
    changed_spans,
    redact,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(APP_DIR, "anonymizer_history.json")
AUDIT_FILE = os.path.join(APP_DIR, "anonymizer_audit.jsonl")
AUDIT_CSV = os.path.join(APP_DIR, "anonymizer_audit.csv")
HISTORY_LIMIT = 100
MONITOR_INTERVAL = 0.7

SAMPLE_TEXT = (
    "Project Vela status update - contact Jens Hansen (jens.hansen@example.dk, +45 21 34 56 78).\n"
    "Customer CPR: 150290-1234 (or 1502901234).\n"
    "Account: Reg. 5320-1234567890 (also written 5320 1234567890).\n"
    "IBAN: DK50 0040 0440 1162 43 (also DK5000400440116243).\n"
    "Card on file: 4111 1111 1111 1111.\n"
    "Company CVR: 12345678.\n"
    "Invoice dated 12/05/2024.\n"
)

ABOUT_TEXT = (
    "Offline GDPR Clipboard Anonymizer (offline)\n"
    "----------------------------------------\n"
    "Zero-trust compliance tool: all processing happens on this machine.\n"
    "No LLMs, no network calls, nothing leaves the device.\n"
    "\n"
    "Rule order (specific identifiers win):\n"
    " 1. CPR numbers (ddmmyy-xxxx, ddmmyyxxxx, date forms dd/mm/yyyy)\n"
    " 2. Bank accounts (reg. 4 digits + konto 10 digits)\n"
    " 3. Phone numbers (+45 / national 8-digit)\n"
    " 4. Email addresses\n"
    " 5. Credit cards (Luhn checksum validated)\n"
    " 6. CVR numbers (8 digits)\n"
    " 7. Custom words you add below\n"
    "\n"
    "Smart checks reduce false positives:\n"
    " - CPR date component must be a plausible day/month.\n"
    " - Credit cards must pass the Luhn checksum.\n"
    "\n"
    "Recommended workflow: paste text -> Anonymize Clipboard -> verify the\n"
    "yellow highlights -> Copy to Clipboard -> paste into chat/email.\n"
)


class AnonymizerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Offline GDPR Clipboard Anonymizer (Offline)")
        self.root.geometry("1150x720")
        self.root.minsize(900, 560)

        self.running = True
        self.current_redacted: str | None = None
        self._clip_seen = ""
        self.history: list[dict] = self._load_history()
        self.session_counts: dict[str, int] = {k: 0 for k in REDACTED_LABELS}
        self.session_strings = 0

        self.enabled_vars = {key: tk.BooleanVar(value=True) for key in RULE_ORDER}
        self.custom_words_var = tk.StringVar()
        self.mask_mode_var = tk.StringVar(value="label")
        self.auto_copy_var = tk.BooleanVar(value=False)
        self.monitor_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready.")

        # append-only audit trail (shared core); stores counts + redacted
        # output only — never the raw PII
        self.audit = AuditLogger(AUDIT_FILE)

        self._build_ui()
        self.refresh_stats()
        self._start_monitor()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------- UI setup
    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_anonymize = ttk.Frame(self.notebook)
        self.tab_history = ttk.Frame(self.notebook)
        self.tab_stats = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_anonymize, text="  Anonymize  ")
        self.notebook.add(self.tab_history, text="  History  ")
        self.notebook.add(self.tab_stats, text="  Stats & About  ")

        self._build_anonymize_tab()
        self._build_history_tab()
        self._build_stats_tab()

        status_bar = ttk.Label(
            self.root, textvariable=self.status_var, relief="sunken", anchor="w"
        )
        status_bar.pack(fill="x", side="bottom")

    def _build_anonymize_tab(self) -> None:
        tab = self.tab_anonymize

        # --- buttons -----------------------------------------------------
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(buttons, text="Anonymize Clipboard", command=self.anonymize_clipboard).pack(side="left")
        ttk.Button(buttons, text="Copy to Clipboard", command=self.copy_output).pack(side="left", padx=6)
        ttk.Button(buttons, text="Load Sample", command=self.load_sample).pack(side="left")
        ttk.Button(buttons, text="Clear", command=self.clear_previews).pack(side="left", padx=6)

        # --- options -----------------------------------------------------
        options = ttk.LabelFrame(tab, text="Redaction options")
        options.pack(fill="x", pady=(0, 6))

        toggles = ttk.Frame(options)
        toggles.pack(side="left", fill="y", padx=8, pady=6)
        for i, key in enumerate(RULE_ORDER):
            label = f"{CATEGORY_LABELS[key]}  ({REDACTED_LABELS[key]})"
            ttk.Checkbutton(toggles, text=label, variable=self.enabled_vars[key]).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 16)
            )

        right = ttk.Frame(options)
        right.pack(side="right", fill="both", expand=True, padx=8, pady=6)
        ttk.Label(right, text="Custom words (comma-separated):").pack(anchor="w")
        ttk.Entry(right, textvariable=self.custom_words_var).pack(fill="x", pady=(2, 6))

        lower = ttk.Frame(right)
        lower.pack(fill="x")
        ttk.Label(lower, text="Mask style:").pack(side="left")
        ttk.Combobox(
            lower,
            textvariable=self.mask_mode_var,
            values=["label", "asterisks"],
            state="readonly",
            width=10,
        ).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(lower, text="Auto-copy result", variable=self.auto_copy_var).pack(side="left")
        ttk.Checkbutton(
            lower,
            text="Monitor clipboard (auto-redact new copies)",
            variable=self.monitor_var,
        ).pack(side="left", padx=(16, 0))

        # --- previews ----------------------------------------------------
        previews = ttk.Frame(tab)
        previews.pack(fill="both", expand=True)

        for col, (header, widget) in enumerate(
            (("Before (original)", "before"), ("After (redacted)", "after"))
        ):
            frame = ttk.LabelFrame(previews, text=header)
            frame.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 1 else 6))
            previews.columnconfigure(col, weight=1)
            previews.rowconfigure(0, weight=1)

            txt = tk.Text(frame, wrap="word", undo=False)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            txt.pack(fill="both", expand=True)
            txt.tag_configure("changed", background="#fff59d")
            setattr(self, f"{widget}_text", txt)

        self.report_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.report_var, foreground="#1a7f37").pack(
            fill="x", pady=(6, 0)
        )

    def _build_history_tab(self) -> None:
        tab = self.tab_history

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="Clear history", command=self.clear_history).pack(side="left")
        ttk.Button(top, text="Refresh stats", command=self.refresh_stats).pack(side="left", padx=6)
        ttk.Button(top, text="Export audit CSV", command=self.export_audit_csv).pack(side="left", padx=6)
        self.audit_status_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.audit_status_var).pack(side="left", padx=12)

        self.history_tree = ttk.Treeview(
            tab, columns=("time", "src", "items", "out"), show="headings", height=10
        )
        self.history_tree.heading("time", text="Time")
        self.history_tree.heading("src", text="Source chars")
        self.history_tree.heading("items", text="Items redacted")
        self.history_tree.heading("out", text="Output chars")
        self.history_tree.column("time", width=180)
        self.history_tree.column("src", width=110, anchor="center")
        self.history_tree.column("items", width=130, anchor="center")
        self.history_tree.column("out", width=110, anchor="center")
        self.history_tree.pack(fill="x")

        ttk.Label(tab, text="Redacted output of selected entry:").pack(anchor="w", pady=(8, 2))
        self.history_detail = tk.Text(tab, wrap="word", height=12)
        history_scroll = ttk.Scrollbar(tab, orient="vertical", command=self.history_detail.yview)
        self.history_detail.configure(yscrollcommand=history_scroll.set)
        history_scroll.pack(side="right", fill="y")
        self.history_detail.pack(fill="both", expand=True)

        self.history_tree.bind("<<TreeviewSelect>>", self._on_history_select)
        self._reload_history_tree()

    def _build_stats_tab(self) -> None:
        self.stats_text = tk.Text(self.tab_stats, wrap="word")
        stats_scroll = ttk.Scrollbar(self.tab_stats, orient="vertical", command=self.stats_text.yview)
        self.stats_text.configure(yscrollcommand=stats_scroll.set)
        stats_scroll.pack(side="right", fill="y")
        self.stats_text.pack(fill="both", expand=True)
        self.stats_text.configure(state="disabled")

    # --------------------------------------------------------------- actions
    def _enabled_keys(self) -> list[str]:
        return [key for key in RULE_ORDER if self.enabled_vars[key].get()]

    def _custom_words(self) -> list[str]:
        raw = self.custom_words_var.get()
        return [w.strip() for w in raw.replace("\n", ",").split(",") if w.strip()]

    def _process_text(self, text: str, source: str) -> None:
        result, counts = redact(
            text,
            enabled=self._enabled_keys(),
            custom_words=self._custom_words(),
            mask_mode=self.mask_mode_var.get(),
        )
        self.current_redacted = result

        spans_before, spans_after = changed_spans(text, result)
        self._fill(self.before_text, text, spans_before)
        self._fill(self.after_text, result, spans_after)

        total = sum(counts.values())
        parts = [f"{v} {CATEGORY_LABELS[k]}" for k, v in counts.items() if v]
        self.report_var.set(
            f"{source}: {len(text)} -> {len(result)} chars, {total} item(s) redacted"
            + (f" ({', '.join(parts)})" if parts else "")
        )
        self.status_var.set(f"{source}: processed {len(text)} characters.")

        self._record_history(text, result, counts)
        self.audit.log(
            app="anonymizer",
            source=source,
            source_len=len(text),
            output_len=len(result),
            items=total,
            counts=counts,
            mask_mode=self.mask_mode_var.get(),
        )

        if self.auto_copy_var.get() and result != text:
            self._copy(result)

    def _copy(self, text: str) -> None:
        if pyperclip is None:
            messagebox.showerror("Missing dependency", "Install pyperclip:  pip install pyperclip")
            return
        pyperclip.copy(text)
        self._clip_seen = text
        self.status_var.set("Copied to clipboard.")

    def anonymize_clipboard(self) -> None:
        if pyperclip is None:
            messagebox.showerror("Missing dependency", "Install pyperclip:  pip install pyperclip")
            return
        try:
            text = pyperclip.paste()
        except Exception as exc:  # pragma: no cover
            self.status_var.set(f"Could not read clipboard: {exc}")
            return
        if not text:
            self.status_var.set("Clipboard is empty or contains no text.")
            return
        self._clip_seen = text
        self._process_text(text, "Clipboard")

    def copy_output(self) -> None:
        if self.current_redacted is None:
            self.status_var.set("Nothing to copy yet - anonymize first.")
            return
        self._copy(self.current_redacted)

    def load_sample(self) -> None:
        if not self.custom_words_var.get():
            self.custom_words_var.set("vela")
        self._process_text(SAMPLE_TEXT, "Sample")

    def clear_previews(self) -> None:
        for widget in (self.before_text, self.after_text):
            widget.config(state="normal")
            widget.delete("1.0", "end")
            widget.config(state="disabled")
        self.report_var.set("")
        self.current_redacted = None
        self.status_var.set("Cleared.")

    # ------------------------------------------------------------- clipboard
    def _monitor_loop(self) -> None:
        while self.running:
            try:
                clip = pyperclip.paste() if pyperclip is not None else ""
            except Exception:  # pragma: no cover
                clip = ""
            if self.monitor_var.get() and clip and clip != self._clip_seen:
                self._clip_seen = clip
                self.root.after(0, self._process_text, clip, "Clipboard (auto)")
            else:
                self._clip_seen = clip
            time.sleep(MONITOR_INTERVAL)

    def _start_monitor(self) -> None:
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    # --------------------------------------------------------------- history
    def _record_history(self, source: str, result: str, counts: dict[str, int]) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_len": len(source),
            "output_len": len(result),
            "items": sum(counts.values()),
            "counts": counts,
            "output": result,
        }
        self.history.append(entry)
        self.history = self.history[-HISTORY_LIMIT:]
        self.session_strings += 1
        for key, val in counts.items():
            self.session_counts[key] += val
        self._save_history()
        self._reload_history_tree()
        self.refresh_stats()

    def _load_history(self) -> list[dict]:
        try:
            with open(HISTORY_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_history(self) -> None:
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
                json.dump(self.history, fh, ensure_ascii=False, indent=2)
        except OSError:  # pragma: no cover
            pass

    def _reload_history_tree(self) -> None:
        self.history_tree.delete(*self.history_tree.get_children())
        for i, entry in enumerate(self.history):
            self.history_tree.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    entry["ts"],
                    entry["source_len"],
                    entry["items"],
                    entry["output_len"],
                ),
            )

    def _on_history_select(self, _event=None) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        if 0 <= idx < len(self.history):
            self.history_detail.delete("1.0", "end")
            self.history_detail.insert("1.0", self.history[idx]["output"])

    def clear_history(self) -> None:
        self.history = []
        self._save_history()
        self._reload_history_tree()
        self.history_detail.delete("1.0", "end")
        self.session_counts = {k: 0 for k in REDACTED_LABELS}
        self.session_strings = 0
        self.refresh_stats()
        self.status_var.set("History cleared.")

    def export_audit_csv(self) -> None:
        try:
            out = self.audit.export_csv(
                AUDIT_CSV,
                columns=["ts", "source", "source_len", "output_len", "items",
                         "counts", "mask_mode"],
            )
            self.audit_status_var.set(f"Exported {self.audit.count()} events to {out.name}")
            self.status_var.set(f"Audit CSV written: {out}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Export failed", str(exc))

    # ----------------------------------------------------------------- stats
    def refresh_stats(self) -> None:
        aggregated = {k: 0 for k in REDACTED_LABELS}
        for entry in self.history:
            for key, val in entry.get("counts", {}).items():
                aggregated[key] += val
        lines = [
            "Statistics",
            "----------",
            f"Strings processed (this session): {self.session_strings}",
            f"Strings processed (all time):     {len(self.history)}",
            f"Total items redacted:             {sum(aggregated.values())}",
            "",
            "Items redacted by category:",
        ]
        for key in RULE_ORDER:
            lines.append(f"  {CATEGORY_LABELS[key]:<18} {aggregated[key]}")
        lines += ["", ABOUT_TEXT]
        self.stats_text.configure(state="normal")
        self.stats_text.delete("1.0", "end")
        self.stats_text.insert("1.0", "\n".join(lines))
        self.stats_text.configure(state="disabled")

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _fill(widget: tk.Text, text: str, spans: list[tuple[int, int]]) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.tag_remove("changed", "1.0", "end")
        for start, end in spans:
            widget.tag_add("changed", f"1.0+{start}c", f"1.0+{end}c")
        widget.config(state="disabled")

    def on_close(self) -> None:
        self.running = False
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AnonymizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()