#!/usr/bin/env python3
"""Screen Answer Overlay — LLM-powered screenshot analyzer (macOS / OpenRouter)."""

import base64
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from pynput import keyboard

# ─── AppKit (native macOS UI) ─────────────────────────────────────────────────
from AppKit import (
    NSApplication, NSPanel, NSView, NSTextField, NSTextView, NSScrollView,
    NSButton, NSColor, NSFont, NSScreen, NSPasteboard, NSPasteboardTypeString,
    NSViewWidthSizable,
)
from Foundation import NSObject, NSTimer, NSMakeRect, NSMakeSize, NSMakePoint
from PyObjCTools import AppHelper
import objc

load_dotenv()

# AppKit constants (numeric — avoids version-dependent imports)
NSWindowStyleMaskBorderless = 0
NSWindowStyleMaskNonactivatingPanel = 1 << 7
NSBackingStoreBuffered = 2
NSStatusWindowLevel = 25
NSWindowCollectionBehaviorCanJoinAllSpaces = 1 << 0
NSWindowCollectionBehaviorStationary = 1 << 4
NSApplicationActivationPolicyAccessory = 1
NSNoBorder = 0
FLT_MAX = 1.0e7

SCREENSHOT_PATH = "/tmp/screen_answer.png"
OVERLAY_WIDTH = 380
OVERLAY_HEIGHT = 210
AUTO_CLOSE_SEC = 180

STATE_DIR = Path.home() / ".config" / "screen_answer"
STATE_FILE = STATE_DIR / "state.json"

# Two model slots, toggled with Ctrl+Shift+M. Slugs are configurable in .env
# because the exact OpenRouter names of brand-new models can change.
MODELS = [
    ("Opus 4.8", os.getenv("MODEL_OPUS", "anthropic/claude-opus-4.8")),
    ("Sonnet 4.6", os.getenv("MODEL_SONNET", "anthropic/claude-sonnet-4.6")),
]

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


# ─── Model state (persisted across restarts) ──────────────────────────────────

class ModelState:
    def __init__(self):
        self.index = 0
        try:
            data = json.loads(STATE_FILE.read_text())
            self.index = int(data.get("index", 0)) % len(MODELS)
        except Exception:
            self.index = 0

    @property
    def label(self) -> str:
        return MODELS[self.index][0]

    @property
    def slug(self) -> str:
        return MODELS[self.index][1]

    def toggle(self) -> str:
        self.index = (self.index + 1) % len(MODELS)
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({"index": self.index}))
        except Exception:
            pass
        return self.label


STATE = ModelState()


# ─── LLM Client (OpenRouter) ──────────────────────────────────────────────────

def call_llm(image_b64: str, ocr_text: str = "") -> dict:
    """Returns dict with keys: letter, answer, reasoning."""
    text = _call_openrouter(image_b64, STATE.slug, ocr_text)
    return _parse_response(text)


def _build_user_text(ocr_text: str) -> str:
    base = "Przeanalizuj pytanie/zadanie widoczne na tym zrzucie ekranu i podaj odpowiedź."
    if ocr_text.strip():
        return f"OCR text extracted from the screenshot:\n{ocr_text}\n\n{base}"
    return base


def _call_openrouter(image_b64: str, model: str, ocr_text: str = "") -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("Brak OPENROUTER_API_KEY w .env")

    body = {
        "model": model,
        "max_tokens": int(os.getenv("MAX_TOKENS", "8000")),
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
    }

    # Extended reasoning for models that support it (OpenRouter maps this to
    # Anthropic's thinking budget). Set REASONING_TOKENS=0 in .env to disable.
    reasoning_tokens = int(os.getenv("REASONING_TOKENS", "5000"))
    if reasoning_tokens > 0:
        body["reasoning"] = {"max_tokens": reasoning_tokens}

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/KhalSCI/AI_Test_helper",
            "X-Title": "Screen Answer",
        },
        json=body,
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def _parse_response(text: str) -> dict:
    """Parse response with ODPOWIEDŹ: tag at the end."""
    import re
    letter = ""
    answer = ""
    reasoning = text.strip()

    m = re.search(r'ODPOWIED[ŹZ]:\s*(.+)', text, re.IGNORECASE)
    if m:
        answer_raw = m.group(1).strip()
        if len(answer_raw) == 1 and answer_raw in "ABCDEFGH":
            letter = answer_raw
            answer = answer_raw
        elif answer_raw and answer_raw[0] in "ABCDEFGH" and (len(answer_raw) < 2 or answer_raw[1] in " .):–-—"):
            letter = answer_raw[0]
            answer = answer_raw
        else:
            answer = answer_raw
        reasoning = text[:m.start()].strip()

    return {"letter": letter, "answer": answer, "reasoning": reasoning}


# ─── Screenshot (native macOS `screencapture`) ────────────────────────────────

def take_screenshot(select_region: bool = False):
    """Capture screen via screencapture; return base64 PNG, or None if cancelled."""
    try:
        os.remove(SCREENSHOT_PATH)
    except OSError:
        pass

    if select_region:
        cmd = ["screencapture", "-i", "-x", SCREENSHOT_PATH]  # -i: interactive region
    else:
        cmd = ["screencapture", "-x", SCREENSHOT_PATH]        # -x: silent (no sound)
    subprocess.run(cmd, check=True)

    # On cancel (Esc during region select) no file is written.
    if not os.path.exists(SCREENSHOT_PATH) or os.path.getsize(SCREENSHOT_PATH) == 0:
        return None
    with open(SCREENSHOT_PATH, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ─── Overlay (native AppKit NSPanel) ──────────────────────────────────────────

def _rgb(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r / 255.0, g / 255.0, b / 255.0, a)


def _label(frame, size, color, bold=False, right=False):
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setTextColor_(color)
    f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    if right:
        f.setAlignment_(1)  # NSRightTextAlignment
    return f


class Overlay(NSObject):
    """Persistent borderless, non-activating panel shown in the top-right corner."""

    def init(self):
        self = objc.super(Overlay, self).init()
        if self is None:
            return None
        self._timer = None
        self.full_answer = ""
        self._build()
        return self

    def _build(self):
        W, H, pad = OVERLAY_WIDTH, OVERLAY_HEIGHT, 12
        rect = NSMakeRect(0, 0, W, H)

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
        )

        # Rounded, semi-transparent dark background
        content = NSView.alloc().initWithFrame_(rect)
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setCornerRadius_(14.0)
        layer.setMasksToBounds_(True)
        try:
            layer.setBackgroundColor_(_rgb(26, 26, 46, 0.94).CGColor())
        except Exception:
            panel.setOpaque_(True)
            panel.setBackgroundColor_(_rgb(26, 26, 46, 1.0))
        panel.setContentView_(content)

        # Top row: model label (left) + Kopiuj / hide buttons (right)
        self.model_label = _label(NSMakeRect(pad, H - pad - 20, 180, 18),
                                  11, _rgb(170, 170, 170))
        self.model_label.setStringValue_(STATE.label)
        content.addSubview_(self.model_label)

        btn_hide = NSButton.alloc().initWithFrame_(NSMakeRect(W - pad - 28, H - pad - 26, 28, 26))
        btn_hide.setTitle_("✕")
        btn_hide.setBezelStyle_(1)
        btn_hide.setTarget_(self)
        btn_hide.setAction_("hideClicked:")
        content.addSubview_(btn_hide)

        btn_copy = NSButton.alloc().initWithFrame_(NSMakeRect(W - pad - 28 - 6 - 64, H - pad - 26, 64, 26))
        btn_copy.setTitle_("Kopiuj")
        btn_copy.setBezelStyle_(1)
        btn_copy.setTarget_(self)
        btn_copy.setAction_("copyClicked:")
        content.addSubview_(btn_copy)

        # Big answer letter
        self.letter = _label(NSMakeRect(pad, H - pad - 26 - 44, 140, 44),
                            34, _rgb(79, 195, 247), bold=True)
        content.addSubview_(self.letter)

        # Scrollable answer/reasoning body
        body_top = H - pad - 26 - 44 - 2
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(pad, pad, W - 2 * pad, body_top - pad))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(NSNoBorder)
        scroll.setAutohidesScrollers_(True)

        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, W - 2 * pad, body_top - pad))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        tv.setTextColor_(_rgb(224, 224, 224))
        tv.setFont_(NSFont.systemFontOfSize_(13))
        tv.setTextContainerInset_(NSMakeSize(2, 2))
        tv.setMinSize_(NSMakeSize(0, 0))
        tv.setMaxSize_(NSMakeSize(FLT_MAX, FLT_MAX))
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.setAutoresizingMask_(NSViewWidthSizable)
        # Fixed-width container so text wraps to the panel width (the window is
        # not resizable, so width-tracking is unnecessary).
        tv.textContainer().setContainerSize_(NSMakeSize(W - 2 * pad, FLT_MAX))
        scroll.setDocumentView_(tv)
        self.textview = tv
        content.addSubview_(scroll)

        # Position: top-right corner of the main screen (below the menu bar)
        sframe = NSScreen.mainScreen().frame()
        x = sframe.origin.x + sframe.size.width - W - 20
        y = sframe.origin.y + sframe.size.height - H - 40
        panel.setFrameOrigin_(NSMakePoint(x, y))

        self.panel = panel

    # ── Timer ──
    def _arm_timer(self):
        if self._timer is not None:
            self._timer.invalidate()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            AUTO_CLOSE_SEC, self, "autoClose:", None, False
        )

    def autoClose_(self, timer):
        self.panel.orderOut_(None)

    # ── Button actions (ObjC selectors) ──
    def hideClicked_(self, sender):
        self.panel.orderOut_(None)

    def copyClicked_(self, sender):
        if self.full_answer:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(self.full_answer, NSPasteboardTypeString)

    # ── Called from the app controller (main thread via AppHelper.callAfter) ──
    def hide_for_capture(self):
        self.panel.orderOut_(None)

    def show_loading(self):
        self.letter.setStringValue_("…")
        self.textview.setString_("Analizuję...")
        self.model_label.setStringValue_(STATE.label)
        self._arm_timer()
        self.panel.orderFrontRegardless()

    def show_answer(self, result):
        letter = result.get("letter", "").strip()
        answer = result.get("answer", "").strip()
        reasoning = result.get("reasoning", "").strip()
        self.full_answer = f"{letter}: {answer}\n\n{reasoning}" if letter else f"{answer}\n\n{reasoning}"

        self.letter.setStringValue_(letter or "✓")
        body = answer
        if reasoning:
            body += f"\n\n─── Reasoning ───\n{reasoning}"
        self.textview.setString_(body)
        self.model_label.setStringValue_(STATE.label)
        self._arm_timer()
        self.panel.orderFrontRegardless()

    def show_error(self, msg):
        self.full_answer = msg
        self.letter.setStringValue_("!")
        self.textview.setString_(msg)
        self._arm_timer()
        self.panel.orderFrontRegardless()

    def update_model_label(self):
        self.model_label.setStringValue_(STATE.label)

    def toggle_visibility(self):
        if self.panel.isVisible():
            self.panel.orderOut_(None)
        else:
            self.panel.orderFrontRegardless()


# ─── App Controller ───────────────────────────────────────────────────────────

class App:
    def __init__(self, overlay):
        self.overlay = overlay

    def trigger_region(self):
        self._start(True)

    def trigger_full(self):
        self._start(False)

    def _start(self, select_region: bool):
        AppHelper.callAfter(self.overlay.hide_for_capture)
        threading.Thread(target=self._process, args=(select_region,), daemon=True).start()

    def _process(self, select_region: bool):
        try:
            time.sleep(0.2)  # let the overlay disappear before capture
            img_b64 = take_screenshot(select_region)
            if img_b64 is None:
                return  # selection cancelled
            AppHelper.callAfter(self.overlay.show_loading)
            answer = call_llm(img_b64)
            AppHelper.callAfter(self.overlay.show_answer, answer)
        except Exception as e:
            AppHelper.callAfter(self.overlay.show_error, f"Błąd: {e}")

    def toggle_model(self):
        label = STATE.toggle()
        print(f"Model → {label}")
        AppHelper.callAfter(self.overlay.update_model_label)

    def toggle_hide(self):
        AppHelper.callAfter(self.overlay.toggle_visibility)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    nsapp = NSApplication.sharedApplication()
    nsapp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon

    overlay = Overlay.alloc().init()
    app = App(overlay)

    hotkeys = keyboard.GlobalHotKeys({
        "<ctrl>+<shift>+q": app.trigger_region,
        "<ctrl>+<shift>+w": app.trigger_full,
        "<ctrl>+<shift>+m": app.toggle_model,
        "<ctrl>+<shift>+h": app.toggle_hide,
    })
    hotkeys.daemon = True
    hotkeys.start()

    print(f"Screen Answer — model: {STATE.label}")
    print("  Ctrl+Shift+Q — zaznacz region")
    print("  Ctrl+Shift+W — cały ekran")
    print("  Ctrl+Shift+M — przełącz model (Opus 4.8 / Sonnet 4.6)")
    print("  Ctrl+Shift+H — schowaj/pokaż nakładkę")
    print("  Ctrl+C aby zakończyć")

    try:
        AppHelper.runEventLoop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
