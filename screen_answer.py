#!/home/khal/Desktop/WD_APP/.venv/bin/python3
"""Screen Answer Overlay — LLM-powered screenshot analyzer."""

import base64
import json
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
AUTO_CLOSE_SEC = 30

SYSTEM_PROMPT = (
    "You are analyzing a screenshot. "
    "Read ALL text on the screen carefully and precisely — every word, number, and symbol matters. "
    "If it's a multiple choice question: identify the correct answer letter (A/B/C/D) with a short justification (1-2 sentences). "
    "If it's another type of question or task: answer it directly and concisely. "
    "Always respond in the same language as the question on the screenshot."
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
#btn-copy, #btn-close {
    background: rgba(255,255,255,0.10);
    border: none;
    border-radius: 8px;
    color: #ffffff;
    padding: 4px 14px;
    min-height: 28px;
}
#btn-copy:hover, #btn-close:hover {
    background: rgba(255,255,255,0.20);
}
"""


# ─── LLM Client ──────────────────────────────────────────────────────────────

# OpenAI structured output schema (used for openai provider)
OPENAI_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "Full step-by-step analysis.",
                },
                "letter": {
                    "type": "string",
                    "description": "Answer letter: A, B, C or D. Empty if not applicable.",
                },
                "answer": {
                    "type": "string",
                    "description": "Short answer (1-2 sentences) with justification.",
                },
            },
            "required": ["reasoning", "letter", "answer"],
            "additionalProperties": False,
        },
    },
}


def call_llm(image_b64: str) -> dict:
    """Returns dict with keys: letter, answer."""
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "openai":
        return _call_openai(image_b64)
    if provider == "anthropic":
        text = _call_anthropic(image_b64)
        return _parse_text_response(text)
    return _call_gemini(image_b64)


def _parse_text_response(text: str) -> dict:
    """Parse a plain-text LLM response into letter + answer."""
    stripped = text.strip()
    letter = ""
    if stripped and stripped[0] in "ABCD" and (len(stripped) < 2 or stripped[1] in " .):–-—\n"):
        letter = stripped[0]
        rest = stripped[1:].lstrip(" .):–-—")
        return {"letter": letter, "answer": rest.strip()}
    return {"letter": "", "answer": stripped}


# ─── Gemini ───────────────────────────────────────────────────────────────────

def _call_gemini(image_b64: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    resp = requests.post(
        url,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "systemInstruction": {
                "parts": [{"text": SYSTEM_PROMPT}],
            },
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"text": "Analyze the question/task visible on this screenshot and provide the answer."},
                    ],
                }
            ],
            "generationConfig": {
                "thinkingConfig": {
                    "thinkingLevel": "high",
                    "includeThoughts": False,
                },
                "maxOutputTokens": 1000,
            },
        },
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"Gemini {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    # Extract text from response parts (skip thought parts)
    parts = data["candidates"][0]["content"]["parts"]
    text = ""
    for part in parts:
        if "text" in part and not part.get("thought"):
            text += part["text"]

    return _parse_text_response(text)


# ─── OpenAI ───────────────────────────────────────────────────────────────────

def _call_openai(image_b64: str) -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "gpt-5.2",
            "max_completion_tokens": 2000,
            "response_format": OPENAI_RESPONSE_SCHEMA,
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
                        {"type": "text", "text": "Analyze the question/task visible on this screenshot and provide the answer."},
                    ],
                },
            ],
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:300]}")
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


# ─── Anthropic ────────────────────────────────────────────────────────────────

def _call_anthropic(image_b64: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 1000,
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
                        {"type": "text", "text": "Analyze the question/task visible on this screenshot and provide the answer."},
                    ],
                }
            ],
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Anthropic {resp.status_code}: {resp.text[:300]}")
    return resp.json()["content"][0]["text"]


# ─── Screenshot ───────────────────────────────────────────────────────────────

def take_screenshot() -> str:
    """Capture screen via maim, return base64-encoded PNG."""
    subprocess.run(["maim", SCREENSHOT_PATH], check=True)
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
        frame.pack_start(top, False, False, 0)

        # Answer letter
        self._letter = Gtk.Label()
        self._letter.set_name("answer-letter")
        self._letter.set_halign(Gtk.Align.START)
        frame.pack_start(self._letter, False, False, 0)

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
        frame.pack_start(scroll, True, True, 0)

        # Copy button
        btn_copy = Gtk.Button(label="Kopiuj")
        btn_copy.set_name("btn-copy")
        btn_copy.set_halign(Gtk.Align.START)
        btn_copy.connect("clicked", self._on_copy)
        frame.pack_start(btn_copy, False, False, 0)

        self.add(frame)

    def _fade_in_step(self, alpha):
        if alpha >= 1.0:
            self.set_opacity(1.0)
            return
        self.set_opacity(alpha)
        GLib.timeout_add(30, self._fade_in_step, alpha + 0.08)

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
        self._full_answer = f"{letter}: {answer}" if letter else answer

        self._status.set_text("")
        self._letter.set_text(letter)
        self._body.set_text(answer)

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

    def trigger(self):
        """Called from hotkey thread — schedule work on GTK main thread."""
        GLib.idle_add(self._run)

    def _run(self):
        # Close previous overlay if open
        if self._overlay:
            try:
                self._overlay.destroy()
            except Exception:
                pass

        self._overlay = OverlayWindow()

        # Run LLM call in background thread
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self):
        try:
            img_b64 = take_screenshot()
            answer = call_llm(img_b64)
            GLib.idle_add(self._overlay.show_answer, answer)
        except Exception as e:
            GLib.idle_add(self._overlay.show_error, f"Błąd: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = App()

    # Global hotkey listener (runs in its own thread)
    hotkeys = keyboard.GlobalHotKeys({"<ctrl>+<shift>+a": app.trigger})
    hotkeys.daemon = True
    hotkeys.start()

    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    print(f"Screen Answer [{provider}] — nasłuchiwanie Ctrl+Shift+A  (Ctrl+C aby zakończyć)")

    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
