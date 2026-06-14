#!/usr/bin/env bash
# Helper for granting the macOS permissions Screen Answer needs.
#
# IMPORTANT: macOS (TCC) does NOT allow apps/scripts to grant Accessibility or
# Screen Recording permission for you — that is a security boundary. The actual
# "allow" toggle MUST be flipped by you in System Settings. This script does
# everything around that: it detects your terminal, opens the exact panes,
# triggers the permission prompts so the app shows up in the list immediately,
# and can reset stale entries with `--reset`.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$SCRIPT_DIR/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

# ── Identify the host terminal (permissions are inherited by child Python) ────
case "${TERM_PROGRAM:-}" in
  Apple_Terminal) APP="Terminal";              BID="com.apple.Terminal" ;;
  iTerm.app)      APP="iTerm";                 BID="com.googlecode.iterm2" ;;
  vscode)         APP="Visual Studio Code";    BID="com.microsoft.VSCode" ;;
  WarpTerminal)   APP="Warp";                  BID="dev.warp.Warp-Stable" ;;
  Hyper)          APP="Hyper";                 BID="co.zeit.hyper" ;;
  WezTerm)        APP="WezTerm";               BID="com.github.wez.wezterm" ;;
  ghostty)        APP="Ghostty";               BID="com.mitchellh.ghostty" ;;
  *)              APP="${TERM_PROGRAM:-twój terminal}"; BID="" ;;
esac

echo "═══════════════════════════════════════════════════════════════"
echo " Screen Answer — konfiguracja uprawnień macOS"
echo " Terminal:  $APP  ${BID:+($BID)}"
echo "═══════════════════════════════════════════════════════════════"

# ── Optional: reset stale grants (run when re-granting doesn't 'stick') ───────
if [ "${1:-}" = "--reset" ]; then
  echo "→ Resetuję istniejące zgody dla $APP ..."
  if [ -n "$BID" ]; then
    tccutil reset Accessibility   "$BID" 2>/dev/null && echo "  ✓ Accessibility zresetowane"
    tccutil reset ScreenCapture   "$BID" 2>/dev/null && echo "  ✓ Screen Recording zresetowane"
  else
    echo "  ! Nieznany bundle ID — resetuję globalnie (dotknie wszystkich apek)"
    tccutil reset Accessibility 2>/dev/null
    tccutil reset ScreenCapture 2>/dev/null
  fi
  echo "  Po dodaniu na nowo zrestartuj terminal."
  echo
fi

# ── Trigger the prompts so $APP appears in both lists right away ──────────────
echo "→ Wyzwalam prompt Screen Recording (próbny zrzut)..."
screencapture -x /tmp/_sa_perm_probe.png >/dev/null 2>&1 || true
rm -f /tmp/_sa_perm_probe.png

echo "→ Wyzwalam prompt Accessibility (krótki nasłuch klawiatury)..."
"$PY" - <<'PYEOF' >/dev/null 2>&1 || true
import time
try:
    from pynput import keyboard
    l = keyboard.Listener(on_press=lambda k: None)
    l.start(); time.sleep(1.0); l.stop()
except Exception:
    pass
PYEOF

# ── Open the exact System Settings panes ─────────────────────────────────────
echo "→ Otwieram panele ustawień..."
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
sleep 1
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"

cat <<EOF

───────────────────────────────────────────────────────────────
Zostały Ci 2 kliknięcia (macOS nie pozwala zrobić tego skryptem):

  W obu otwartych panelach (Accessibility ORAZ Screen Recording):
   1. Włącz przełącznik przy „$APP”.
      • Jeśli go nie ma: kliknij „+”, wybierz $APP
        (lub przeciągnij z /Applications albo /System/Applications/Utilities).
   2. macOS może poprosić o restart aplikacji — ZAMKNIJ i otwórz $APP ponownie.

Następnie:
   .venv/bin/python3 screen_answer.py

Jak mimo nadania nadal widzisz „process is not trusted”:
   ./setup_macos.sh --reset      # czyści zacięte wpisy, potem nadaj na nowo
───────────────────────────────────────────────────────────────
EOF
