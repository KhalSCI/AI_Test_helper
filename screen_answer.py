#!/home/khal/Desktop/WD_APP/.venv/bin/python3
"""Screen Answer Overlay — LLM-powered screenshot analyzer."""

import base64
import os
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

import requests
from dotenv import load_dotenv
from pynput import keyboard

load_dotenv()

SCREENSHOT_PATH = "/tmp/screen_answer.png"
OVERLAY_WIDTH = 420
OVERLAY_HEIGHT = 220
AUTO_CLOSE_SEC = 180

SYSTEM_PROMPT = (
    "Analizujesz zrzut ekranu z pytaniem lub zadaniem.\n"
    "Postępuj dokładnie według tych kroków:\n"
    "1. Przeczytaj CAŁY tekst na ekranie uważnie i precyzyjnie.\n"
    "2. Jeśli na obrazie jest macierz lub tabela:\n"
    "   - Policz DOKŁADNIE liczbę wierszy i kolumn. Nie zgaduj — licz linie i separatory.\n"
    "   - Odczytaj każdą wartość osobno, zwracając szczególną uwagę na ułamki (np. 1/2, 3/4, -1/3). "
    "Nie myl ułamków z całymi liczbami ani nie łącz sąsiednich komórek.\n"
    "   - Przepisz macierz element po elemencie.\n"
    "3. Rozwiąż zadanie samodzielnie krok po kroku, NIE patrząc jeszcze na odpowiedzi do wyboru. "
    "Jeśli tworzysz ranking — warianty o identycznych wartościach zajmują tę samą pozycję.\n"
    "4. Dopiero teraz porównaj swój wynik z podanymi odpowiedziami i wybierz tę, która pasuje.\n"
    "5. Na samym końcu odpowiedzi napisz linię: ODPOWIEDŹ: X (gdzie X to litera A/B/C/D/E/F/G/H).\n"
    "Jeśli to nie jest pytanie wielokrotnego wyboru — odpowiedz bezpośrednio i zwięźle, "
    "a na końcu napisz: ODPOWIEDŹ: [twoja krótka odpowiedź].\n"
    "Zawsze odpowiadaj po polsku."
)

CSS = """
#overlay {
    background-color: rgba(26, 26, 46, 0.92);
    border-radius: 16px;
    padding: 18px;
}
#answer-letter {
    color: #4fc3f7;
    font-size: 38px;
    font-weight: bold;
}
#answer-body {
    color: #e0e0e0;
    font-size: 14px;
}
#status-text {
    color: #aaaaaa;
    font-size: 13px;
    font-style: italic;
}
#btn-copy, #btn-close, #btn-minimize {
    background: rgba(255,255,255,0.10);
    border: none;
    border-radius: 8px;
    color: #ffffff;
    padding: 4px 14px;
    min-height: 28px;
}
#btn-copy:hover, #btn-close:hover, #btn-minimize:hover {
    background: rgba(255,255,255,0.20);
}
"""


# ─── LLM Client ──────────────────────────────────────────────────────────────


def call_llm(image_b64: str, ocr_text: str = "") -> dict:
    """Returns dict with keys: letter, answer, reasoning."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    if provider == "openai":
        text = _call_openai(image_b64, ocr_text)
    else:
        text = _call_anthropic(image_b64, ocr_text)
    return _parse_response(text)


def _parse_response(text: str) -> dict:
    """Parse response with ODPOWIEDŹ: tag at the end."""
    import re
    letter = ""
    answer = ""
    reasoning = text.strip()

    # Look for ODPOWIEDŹ: X line
    m = re.search(r'ODPOWIED[ŹZ]:\s*(.+)', text, re.IGNORECASE)
    if m:
        answer_raw = m.group(1).strip()
        # Check if it's just a letter
        if len(answer_raw) == 1 and answer_raw in "ABCDEFGH":
            letter = answer_raw
            answer = answer_raw
        elif answer_raw and answer_raw[0] in "ABCDEFGH" and (len(answer_raw) < 2 or answer_raw[1] in " .):–-—"):
            letter = answer_raw[0]
            answer = answer_raw
        else:
            answer = answer_raw
        # Everything before the tag is reasoning
        reasoning = text[:m.start()].strip()

    return {"letter": letter, "answer": answer, "reasoning": reasoning}


def _build_user_text(ocr_text: str) -> str:
    """Build user message text, including OCR extract if available."""
    base = "Przeanalizuj pytanie/zadanie widoczne na tym zrzucie ekranu i podaj odpowiedź."
    if ocr_text.strip():
        return f"OCR text extracted from the screenshot:\n{ocr_text}\n\n{base}"
    return base


# ─── OpenAI ───────────────────────────────────────────────────────────────────

def _call_openai(image_b64: str, ocr_text: str = "") -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "gpt-5.2",
            "max_completion_tokens": 4096,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": _build_user_text(ocr_text)},
                    ],
                },
            ],
        },
        timeout=90,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


# ─── Anthropic ────────────────────────────────────────────────────────────────

def _call_anthropic(image_b64: str, ocr_text: str = "") -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": _build_user_text(ocr_text)},
                    ],
                }
            ],
        },
        timeout=90,
    )
    if not resp.ok:
        raise RuntimeError(f"Anthropic {resp.status_code}: {resp.text[:300]}")
    return resp.json()["content"][0]["text"]


# ─── Screenshot ───────────────────────────────────────────────────────────────

def take_screenshot(select_region: bool = False) -> str:
    """Capture screen via maim, return base64-encoded PNG."""
    cmd = ["maim", "-s", SCREENSHOT_PATH] if select_region else ["maim", SCREENSHOT_PATH]
    subprocess.run(cmd, check=True)
    with open(SCREENSHOT_PATH, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ─── Overlay Window ──────────────────────────────────────────────────────────

class OverlayWindow(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)

        # Transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_default_size(OVERLAY_WIDTH, OVERLAY_HEIGHT)
        self.set_resizable(False)

        # Position: top-right
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()
        self.move(geom.x + geom.width - OVERLAY_WIDTH - 20, geom.y + 20)

        # Apply CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Layout
        self._build_ui()

        # Escape to close
        self.connect("key-press-event", self._on_key)

        # Auto-close timer
        self._close_timer = None
        self._minimized = False
        self._full_answer = ""

        # Fade-in
        self.set_opacity(0)
        self.show_all()
        self._fade_in_step(0)

    def _build_ui(self):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        frame.set_name("overlay")

        # Top bar: status / close button
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._status = Gtk.Label(label="Analizuję...")
        self._status.set_name("status-text")
        self._status.set_halign(Gtk.Align.START)
        top.pack_start(self._status, True, True, 0)

        btn_close = Gtk.Button(label="✕")
        btn_close.set_name("btn-close")
        btn_close.connect("clicked", lambda _: self.destroy())
        top.pack_end(btn_close, False, False, 0)

        btn_min = Gtk.Button(label="—")
        btn_min.set_name("btn-minimize")
        btn_min.connect("clicked", self._on_minimize)
        top.pack_end(btn_min, False, False, 4)

        frame.pack_start(top, False, False, 0)

        # Content area (hideable for minimize)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # Answer letter
        self._letter = Gtk.Label()
        self._letter.set_name("answer-letter")
        self._letter.set_halign(Gtk.Align.START)
        self._content.pack_start(self._letter, False, False, 0)

        # Answer body (scrollable)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._body = Gtk.Label()
        self._body.set_name("answer-body")
        self._body.set_line_wrap(True)
        self._body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._body.set_halign(Gtk.Align.START)
        self._body.set_valign(Gtk.Align.START)
        self._body.set_max_width_chars(50)
        self._body.set_selectable(True)
        scroll.add(self._body)
        self._content.pack_start(scroll, True, True, 0)

        # Copy button
        btn_copy = Gtk.Button(label="Kopiuj")
        btn_copy.set_name("btn-copy")
        btn_copy.set_halign(Gtk.Align.START)
        btn_copy.connect("clicked", self._on_copy)
        self._content.pack_start(btn_copy, False, False, 0)

        frame.pack_start(self._content, True, True, 0)

        self.add(frame)

    def _fade_in_step(self, alpha):
        if alpha >= 1.0:
            self.set_opacity(1.0)
            return
        self.set_opacity(alpha)
        GLib.timeout_add(30, self._fade_in_step, alpha + 0.08)

    def _on_minimize(self, _btn):
        if self._minimized:
            self._content.show_all()
            self._status.show()
            self.set_opacity(0.92)
            self.resize(OVERLAY_WIDTH, OVERLAY_HEIGHT)
            self._minimized = False
        else:
            self._content.hide()
            self._status.hide()
            self.set_opacity(0.01)
            self.resize(1, 1)
            self._minimized = True

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()

    def _on_copy(self, _btn):
        text = self._full_answer
        if text:
            subprocess.Popen(
                ["xclip", "-selection", "clipboard"],
                stdin=subprocess.PIPE,
            ).communicate(text.encode())

    def show_answer(self, result: dict):
        letter = result.get("letter", "").strip()
        answer = result.get("answer", "").strip()
        reasoning = result.get("reasoning", "").strip()
        self._full_answer = f"{letter}: {answer}\n\n{reasoning}" if letter else f"{answer}\n\n{reasoning}"

        self._status.set_text("")
        self._letter.set_text(letter)
        body = answer
        if reasoning:
            body += f"\n\n─── Reasoning ───\n{reasoning}"
        self._body.set_text(body)

        # Start auto-close timer
        self._close_timer = GLib.timeout_add_seconds(AUTO_CLOSE_SEC, self._auto_close)

    def show_error(self, msg: str):
        self._full_answer = msg
        self._status.set_text("")
        self._letter.set_text("!")
        self._body.set_text(msg)

    def _auto_close(self):
        self.destroy()
        return False


# ─── App Controller ──────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self._overlay = None

    def trigger_region(self):
        """Ctrl+Shift+Q — select region."""
        GLib.idle_add(self._run, True)

    def trigger_full(self):
        """Ctrl+Shift+W — full screen."""
        GLib.idle_add(self._run, False)

    def _run(self, select_region: bool):
        # Close previous overlay if open
        if self._overlay:
            try:
                self._overlay.destroy()
            except Exception:
                pass

        # Take screenshot before showing overlay (region select needs clean screen)
        threading.Thread(target=self._process, args=(select_region,), daemon=True).start()

    def _process(self, select_region: bool):
        try:
            img_b64 = take_screenshot(select_region)
            GLib.idle_add(self._show_overlay)
            answer = call_llm(img_b64)
            GLib.idle_add(self._overlay.show_answer, answer)
        except Exception as e:
            GLib.idle_add(self._show_overlay)
            GLib.idle_add(self._overlay.show_error, f"Błąd: {e}")

    def _show_overlay(self):
        self._overlay = OverlayWindow()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = App()

    # Global hotkey listener (runs in its own thread)
    hotkeys = keyboard.GlobalHotKeys({
        "<ctrl>+<shift>+q": app.trigger_region,
        "<ctrl>+<shift>+w": app.trigger_full,
    })
    hotkeys.daemon = True
    hotkeys.start()

    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    print(f"Screen Answer [{provider}]")
    print("  Ctrl+Shift+Q — zaznacz region")
    print("  Ctrl+Shift+W — cały ekran")
    print("  Ctrl+C aby zakończyć")

    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
