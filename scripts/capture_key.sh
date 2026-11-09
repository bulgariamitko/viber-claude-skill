#!/usr/bin/env bash
# One-time (or after a Viber update / re-link): capture the PRAGMA hexkey statements that
# Viber Desktop uses to unlock viber.db, and save them to ~/ViberLocal/.viber_keys (0600).
# Adapted from github.com/Anastasia-D-Kravchenko/viber-export-macos (MIT).
# Requires quitting the running Viber for ~1 minute; it is reopened at the end.
set -euo pipefail
umask 077
VIBER_APP="/Applications/Viber.app"
DATA_DIR="${HOME}/ViberLocal"
KEYFILE="${DATA_DIR}/.viber_keys"
mkdir -p "$DATA_DIR"; chmod 700 "$DATA_DIR"

WAS_RUNNING=0
if pgrep -x Viber >/dev/null; then
  WAS_RUNNING=1
  osascript -e 'quit app "Viber"' >/dev/null 2>&1 || true
  for i in $(seq 1 20); do pgrep -x Viber >/dev/null || break; sleep 1; done
  pgrep -x Viber >/dev/null && pkill -x Viber || true
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/vibercap.XXXXXX")"
DBG_APP="$WORK/ViberDbg.app"
stop_dbg() { local i=0; while pgrep -f "$DBG_APP" >/dev/null 2>&1 && [ $i -lt 5 ]; do pkill -KILL -f "$DBG_APP" || true; sleep 1; i=$((i+1)); done; }
cleanup() { stop_dbg; rm -rf "$WORK"; [ $WAS_RUNNING -eq 1 ] && open -a Viber || true; }
trap cleanup EXIT INT TERM

echo "[*] Copying and re-signing a temporary debuggable Viber..."
cp -R "$VIBER_APP" "$DBG_APP"
cat > "$WORK/ent.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.get-task-allow</key><true/></dict></plist>
PLIST
codesign --remove-signature "$DBG_APP" 2>/dev/null || true
codesign --force --deep --sign - --entitlements "$WORK/ent.plist" "$DBG_APP" >/dev/null 2>&1

cat > "$WORK/inject.py" <<'PYEOF'
import lldb, os, re, json
KEYFILE = os.environ["KEYFILE"]
ST = {"keys": [], "since": 0, "done": False, "reg": None}
def _reg(frame):
    if ST["reg"] is None:
        tri = (frame.thread.process.GetTarget().GetTriple() or "").lower()
        ST["reg"] = "x1" if tri.startswith(("arm64", "aarch64")) else "rsi"
    return ST["reg"]
def _qs(frame):
    err = lldb.SBError(); p = frame.thread.process
    a = frame.FindRegister(_reg(frame)).GetValueAsUnsigned()
    if not a: return None
    ptr = p.ReadPointerFromMemory(a + 8, err); n = p.ReadUnsignedFromMemory(a + 16, 8, err)
    if err.Fail() or not (0 < n < 1_000_000): return None
    raw = p.ReadMemory(ptr, int(n) * 2, err)
    return None if err.Fail() else raw.decode("utf-16-le", "replace")
def _finish(frame):
    ST["done"] = True
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(ST["keys"], f)
    try: frame.thread.process.Kill()
    except Exception: pass
    os._exit(0)
def on_sql(frame, bp_loc, extra, d):
    if ST["done"]: return False
    sql = _qs(frame)
    if not sql: return False
    if "hexkey" in sql.lower():
        m = re.search(r"hexkey\s*=\s*'([^']*)'", sql, re.I)
        if m and m.group(1) not in ST["keys"]:
            ST["keys"].append(m.group(1)); ST["since"] = 0
        if len(ST["keys"]) >= 2: _finish(frame)
        return False
    if ST["keys"]:
        ST["since"] += 1
        if re.search(r"\bfrom\b\s+(messages|events|contact)", sql, re.I) or ST["since"] > 40:
            _finish(frame)
    return False
PYEOF

rm -f "$KEYFILE.tmp"
echo "[*] Launching it under lldb to capture the key (a Viber window may flash)..."
KEYFILE="$KEYFILE.tmp" lldb -b \
  -o "command script import $WORK/inject.py" \
  -o "breakpoint set -n _ZN9QSqlQuery4execERK7QString" \
  -o "breakpoint command add -s python -F inject.on_sql 1" \
  -o "breakpoint set -n _ZN10QSqlResult7prepareERK7QString" \
  -o "breakpoint command add -s python -F inject.on_sql 2" \
  -o "run" "$DBG_APP/Contents/MacOS/Viber" > "$WORK/lldb.log" 2>&1 &
LPID=$!
for i in $(seq 1 180); do [ -s "$KEYFILE.tmp" ] && break; kill -0 $LPID 2>/dev/null || break; sleep 2; done
stop_dbg; kill $LPID 2>/dev/null || true
if [ -s "$KEYFILE.tmp" ]; then
  mv "$KEYFILE.tmp" "$KEYFILE"; chmod 600 "$KEYFILE"
  echo "[+] Key saved to $KEYFILE ($(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$KEYFILE") hexkey statements)"
else
  echo "[!] Failed to capture key. Is Viber signed in? lldb log tail:"; tail -5 "$WORK/lldb.log" | sed -E "s/hexkey[^']*'[^']*'/hexkey='<redacted>'/g"; exit 1
fi
