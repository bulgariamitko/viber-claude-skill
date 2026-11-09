#!/usr/bin/env python3
"""Local Viber Desktop helper for Claude.

  viber.py refresh                      decrypt the live viber.db -> ~/ViberLocal/viber_plain.db (~10-30s)
  viber.py find <name|number>           find contacts and chats (1:1 and groups)
  viber.py chats [-n 20]                most recently active chats
  viber.py read <chat> [-n 30] [--since YYYY-MM-DD] [--today] [--voice]
                                        messages in a chat (chat = ChatID number, or a name/number search);
                                        --voice also transcribes voice messages inline (local Whisper, slow)
  viber.py transcribe <chat|file> [-n 10] [--model base|medium]
                                        transcribe voice messages locally (no cloud, no API key); cached
  viber.py send <chat> <text>           open the 1:1 chat in Viber and paste the text into the input box
                                        (draft - the user presses Enter). --enter tries to send (experimental)
  viber.py sql "<query>"                raw read-only query on the plaintext copy

Data: ~/ViberLocal (outside Dropbox, 0700). Key: ~/ViberLocal/.viber_keys (from capture_key.sh).
"""
import argparse, datetime as dt, json, os, re, sqlite3, subprocess, sys, time, glob

HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, "ViberLocal")
PLAIN = os.path.join(DATA, "viber_plain.db")
KEYS = os.path.join(DATA, ".viber_keys")
HERE = os.path.dirname(os.path.abspath(__file__))
VSQL = os.path.join(HERE, "vsql")
PLUGINS = "/Applications/Viber.app/Contents/PlugIns"
TYPES = {0: "system", 1: "text", 2: "image", 3: "video", 4: "sticker", 9: "link", 11: "file", 72: "other"}
TRANSCRIPTS = os.path.join(DATA, "transcripts")
WHISPER_MODEL = os.environ.get("VIBER_WHISPER_MODEL", "medium")  # base=fast/rough, medium=slower/accurate


def is_voice(path):
    return bool(path) and ("/PTT/" in path or os.path.basename(path or "").startswith("Voice_"))


def transcribe_file(path, model=None, lang="bg"):
    """Transcribe one audio file locally with openai-whisper. Cached by filename. Returns text or raises."""
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path or "(no path)")
    os.makedirs(TRANSCRIPTS, mode=0o700, exist_ok=True)
    model = model or WHISPER_MODEL
    cache = os.path.join(TRANSCRIPTS, os.path.splitext(os.path.basename(path))[0] + f".{model}.txt")
    if os.path.exists(cache):
        return open(cache).read().strip()
    whisper = subprocess.run(["which", "whisper"], capture_output=True, text=True).stdout.strip()
    if not whisper:
        raise RuntimeError("openai-whisper not installed (pip3 install openai-whisper); ffmpeg also required.")
    out = TRANSCRIPTS + "/.tmp"
    os.makedirs(out, exist_ok=True)
    cmd = [whisper, path, "--model", model, "--output_format", "txt", "--output_dir", out,
           "--fp16", "False", "--verbose", "False"]
    if lang and lang != "auto":
        cmd += ["--language", lang]
    r = subprocess.run(cmd, capture_output=True, text=True)
    txt = os.path.join(out, os.path.splitext(os.path.basename(path))[0] + ".txt")
    if r.returncode or not os.path.exists(txt):
        msg = (r.stderr or r.stdout or "")[-500:]
        if "CERTIFICATE_VERIFY_FAILED" in msg or "URLError" in msg:
            raise RuntimeError(f"whisper model '{model}' not cached and download was blocked (SSL). "
                               f"Download it once with curl - see SKILL.md troubleshooting.")
        raise RuntimeError("whisper failed: " + msg)
    text = open(txt).read().strip()
    with open(cache, "w") as f:
        f.write(text)
    os.chmod(cache, 0o600)
    os.remove(txt)
    return text


def live_db():
    dbs = sorted(glob.glob(os.path.join(HOME, "Library/Application Support/ViberPC/*/viber.db")),
                 key=os.path.getmtime, reverse=True)
    if not dbs:
        sys.exit("viber.db not found - is Viber Desktop installed and signed in?")
    return dbs[0]


def vsql(stmts):
    env = dict(os.environ, QT_PLUGIN_PATH=PLUGINS, QT_LOGGING_RULES="*.debug=false;qt.sql.*=false")
    r = subprocess.run([VSQL, live_db(), KEYS], input="\n".join(stmts) + "\n",
                       capture_output=True, text=True, env=env)
    if r.returncode == 5:
        sys.exit("Decryption failed - the key changed (Viber re-linked/updated?). Run capture_key.sh "
                 "(it quits Viber for ~1 min) and retry.")
    return r


def refresh(quiet=False):
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    if not os.path.exists(KEYS):
        sys.exit(f"No key yet. Run {HERE}/capture_key.sh first (quits Viber for ~1 min).")
    if not os.path.exists(VSQL):
        subprocess.run(["clang++", "-std=c++17", "-O1", "-o", VSQL, VSQL + ".cpp"], check=True)
    t0 = time.time()
    tmp = PLAIN + ".tmp"
    for f in (tmp, tmp + "-journal"):
        if os.path.exists(f): os.remove(f)
    q = lambda s: s.replace("'", "''")
    # pass 1: schema
    r = vsql([f"ATTACH DATABASE '{q(tmp)}' AS plain KEY ''",
              "CREATE TABLE plain.__master AS SELECT type,name,tbl_name,sql FROM main.sqlite_master",
              "DETACH DATABASE plain"])
    if "FAIL" in r.stdout or r.returncode:
        sys.exit("schema copy failed:\n" + r.stdout + r.stderr)
    c = sqlite3.connect(tmp)
    master = c.execute("SELECT type,name,sql FROM __master").fetchall()
    tables = [(n, s) for t, n, s in master if t == "table" and s and not n.startswith("sqlite_")]
    for n, s in tables:
        c.execute(s)
    c.execute("DROP TABLE __master"); c.commit(); c.close()
    # pass 2: data
    stmts = [f"ATTACH DATABASE '{q(tmp)}' AS plain KEY ''", "BEGIN"]
    stmts += [f'INSERT INTO plain."{n}" SELECT * FROM main."{n}"' for n, _ in tables]
    stmts += ["COMMIT", "DETACH DATABASE plain"]
    r = vsql(stmts)
    if r.returncode:
        print("warning: some tables failed to copy:\n" + "\n".join(l for l in r.stdout.splitlines() if l.startswith("FAIL")), file=sys.stderr)
    # indexes + views (best effort)
    c = sqlite3.connect(tmp)
    for t, n, s in master:
        if t in ("index", "view") and s:
            try: c.execute(s)
            except sqlite3.Error: pass
    c.commit(); c.close()
    os.chmod(tmp, 0o600)
    os.replace(tmp, PLAIN)
    if not quiet:
        n = sqlite3.connect(PLAIN).execute("SELECT count(*), max(TimeStamp) FROM Events").fetchone()
        print(f"refreshed in {time.time()-t0:.0f}s: {n[0]} events, latest {fmt_ts(n[1])}")


def db():
    if not os.path.exists(PLAIN):
        refresh(quiet=True)
    c = sqlite3.connect(f"file:{PLAIN}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def fmt_ts(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if ms else "-"


def clean(s):
    return re.sub(r"[⁨⁩‎‏‪-‮]", "", s or "")


def contact_name(r):
    return clean(r["Name"]) or clean(r["ClientName"]) or r["Number"] or f"Contact#{r['ContactID']}"


CHATS_SQL = """
SELECT ci.ChatID, ci.Name AS ChatName, ci.PGType,
  (SELECT count(*) FROM ChatRelation cr WHERE cr.ChatID=ci.ChatID) AS Members,
  (SELECT max(TimeStamp) FROM Events e WHERE e.ChatID=ci.ChatID) AS Last
FROM ChatInfo ci"""


def chat_label(c, chat):
    if chat["ChatName"]:
        return f"{clean(chat['ChatName'])} (group, {chat['Members']} members)"
    rows = c.execute("SELECT co.* FROM ChatRelation cr JOIN Contact co USING(ContactID) WHERE cr.ChatID=?",
                     (chat["ChatID"],)).fetchall()
    rows = [r for r in rows if re.sub(r"\D", "", r["Number"] or "") != my_number()] or rows
    return ", ".join(f"{contact_name(r)} {r['Number'] or ''}".strip() for r in rows) or f"Chat#{chat['ChatID']}"


def my_number():
    return os.path.basename(os.path.dirname(live_db()))


def find(term):
    c = db()
    like = f"%{term}%"
    digits = re.sub(r"\D", "", term)
    contacts = c.execute("""SELECT * FROM Contact WHERE Name LIKE ? OR ClientName LIKE ? OR SortName LIKE ?
                            OR (? <> '' AND replace(Number,'+','') LIKE ?)""",
                         (like, like, like, digits, f"%{digits}%")).fetchall()
    me = my_number()
    ids = [r["ContactID"] for r in contacts if re.sub(r"\D", "", r["Number"] or "") != me or digits == me]
    chats = {}
    if ids:
        for ch in c.execute(CHATS_SQL + f" WHERE ci.ChatID IN (SELECT ChatID FROM ChatRelation WHERE ContactID IN ({','.join('?'*len(ids))}))", ids):
            chats[ch["ChatID"]] = ch
    for ch in c.execute(CHATS_SQL + " WHERE ci.Name LIKE ?", (like,)):
        chats[ch["ChatID"]] = ch
    res = sorted(chats.values(), key=lambda ch: ch["Last"] or 0, reverse=True)
    # 1:1 chats first, then groups
    res.sort(key=lambda ch: 1 if ch["ChatName"] else 0)
    return c, res


def resolve_chat(term):
    if str(term).isdigit() and len(str(term)) < 7:
        c = db()
        ch = c.execute(CHATS_SQL + " WHERE ci.ChatID=?", (int(term),)).fetchone()
        if not ch: sys.exit(f"No chat with id {term}")
        return c, ch
    c, res = find(term)
    if not res: sys.exit(f"No chat matches '{term}'. Try `viber.py find`.")
    one_to_one = [r for r in res if not r["ChatName"]]
    if len(one_to_one) == 1 or (len(res) == 1):
        return c, (one_to_one or res)[0]
    print(f"'{term}' is ambiguous - pass a ChatID:", file=sys.stderr)
    for r in res[:15]:
        print(f"  {r['ChatID']:>6}  {chat_label(c, r)}  last {fmt_ts(r['Last'])}", file=sys.stderr)
    sys.exit(2)


def cmd_find(a):
    c, res = find(a.term)
    if not res: print("no matches"); return
    for r in res[:30]:
        print(f"{r['ChatID']:>6}  {chat_label(c, r)}  last {fmt_ts(r['Last'])}")


def cmd_chats(a):
    c = db()
    for r in c.execute(CHATS_SQL + " ORDER BY Last DESC LIMIT ?", (a.n,)):
        unread = ""
        print(f"{r['ChatID']:>6}  {fmt_ts(r['Last'])}  {chat_label(c, r)}")


def cmd_read(a):
    if not a.no_refresh: refresh(quiet=True)
    c, ch = resolve_chat(a.chat)
    where, params = ["e.ChatID=?", "e.Type<>3", "m.Type<>0"], [ch["ChatID"]]
    since = None
    if a.today: since = dt.datetime.combine(dt.date.today(), dt.time())
    elif a.since: since = dt.datetime.fromisoformat(a.since)
    if since:
        where.append("e.TimeStamp>=?"); params.append(int(since.timestamp() * 1000))
    rows = c.execute(f"""SELECT * FROM (SELECT e.TimeStamp, e.Direction, e.ContactID, m.Type, m.Body, m.PayloadPath,
                         m.ThumbnailPath, m.Info, co.Name, co.ClientName, co.Number
                         FROM Events e JOIN Messages m USING(EventID) LEFT JOIN Contact co ON co.ContactID=e.ContactID
                         WHERE {' AND '.join(where)} ORDER BY e.TimeStamp DESC LIMIT ?) ORDER BY TimeStamp""",
                     params + [a.n]).fetchall()
    print(f"# Chat {ch['ChatID']}: {chat_label(c, ch)}")
    for r in rows:
        who = "Me" if r["Direction"] == 1 else (clean(r["Name"]) or clean(r["ClientName"]) or r["Number"] or "?")
        kind = TYPES.get(r["Type"], f"type{r['Type']}")
        body = (r["Body"] or "").strip()
        extra = ""
        if r["Type"] in (2, 3, 11):
            raw = r["PayloadPath"] or ""
            voice = is_voice(raw)
            kind = "voice" if voice else kind
            have = raw and os.path.exists(raw)
            path = raw
            if not have:
                thumb = r["ThumbnailPath"] or ""
                path = (raw + " (not downloaded" + (f"; thumbnail: {thumb}" if thumb and os.path.exists(thumb) else "") + ")") if raw else (f"(not downloaded; thumbnail: {thumb})" if thumb else "(not downloaded)")
            try:
                fi = json.loads(r["Info"] or "{}").get("fileInfo", {})
                if fi.get("FileName") and r["Type"] == 11 and not voice: extra = f" name={fi['FileName']}"
            except ValueError: pass
            if voice and have and getattr(a, "voice", False):
                try:
                    body = f"[voice] “{transcribe_file(raw)}”"
                except Exception as e:
                    body = f"[voice] {path} (transcription failed: {e})"
            elif voice:
                hint = "" if not have else "  (use --voice to transcribe, or: viber.py transcribe <ChatID>)"
                body = f"[voice] {path}{hint}"
            else:
                body = (body + " " if body and not body.isdigit() else "") + f"[{kind}] {path}{extra}"
        elif r["Type"] == 4:
            body = "[sticker]"
        elif r["Type"] != 1:
            body = f"[{kind}] {body}"
        print(f"{fmt_ts(r['TimeStamp'])}  {who}: {body}")


def osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode: sys.exit("AppleScript failed (Accessibility permission?): " + r.stderr.strip())
    return r.stdout.strip()


def cmd_send(a):
    refresh(quiet=True)
    c, ch = resolve_chat(a.chat)
    if ch["ChatName"]:
        sys.exit("Sending to group chats isn't supported yet (no deep link). Open it manually.")
    co = c.execute("SELECT co.* FROM ChatRelation cr JOIN Contact co USING(ContactID) WHERE cr.ChatID=?",
                   (ch["ChatID"],)).fetchone()
    number = (co["Number"] or "").strip()
    if not number: sys.exit("This contact has no phone number stored; can't deep-link.")
    label = chat_label(c, ch)
    before = c.execute("SELECT max(TimeStamp) FROM Events WHERE ChatID=?", (ch["ChatID"],)).fetchone()[0] or 0
    c.close()
    # open the chat
    subprocess.run(["open", f"viber://chat?number={number.replace('+', '%2B')}"], check=True)
    time.sleep(2.5)
    osa('tell application "Viber" to activate')
    time.sleep(0.8)
    # paste via clipboard (preserves Cyrillic/emoji), restore clipboard afterwards
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=a.text.encode("utf-8"), check=True)
    osa('tell application "System Events" to tell process "Viber" to keystroke "v" using command down')
    time.sleep(0.6)
    if not a.enter:
        print(f"Draft pasted into the Viber chat with {label}. NOT sent - the user must press Enter in Viber.")
        subprocess.run(["pbcopy"], input=old)
        return
    osa('tell application "System Events" to tell process "Viber" to key code 36')  # Return
    time.sleep(0.5)
    subprocess.run(["pbcopy"], input=old)
    # verify
    for _ in range(6):
        time.sleep(2)
        refresh(quiet=True)
        c = db()
        row = c.execute("""SELECT e.TimeStamp, m.Body FROM Events e JOIN Messages m USING(EventID)
                           WHERE e.ChatID=? AND e.Direction=1 AND e.TimeStamp>? ORDER BY e.TimeStamp DESC LIMIT 1""",
                        (ch["ChatID"], before)).fetchone()
        if row:
            same = (row["Body"] or "").strip() == a.text.strip()
            print(f"SENT to {label} at {fmt_ts(row['TimeStamp'])}" + ("" if same else f" (text differs! got: {row['Body']!r})"))
            return
    print(f"WARNING: could not confirm the message in chat {label}. Check Viber - it may have gone to another chat or still be in the input box.")
    sys.exit(1)


def cmd_transcribe(a):
    # A direct file path, or a ChatID / name whose recent voice messages we transcribe.
    if os.path.exists(a.target):
        print(transcribe_file(a.target, a.model))
        return
    if not a.no_refresh: refresh(quiet=True)
    c, ch = resolve_chat(a.target)
    rows = c.execute("""SELECT e.TimeStamp, e.Direction, m.PayloadPath, co.Name, co.ClientName, co.Number
                        FROM Events e JOIN Messages m USING(EventID) LEFT JOIN Contact co ON co.ContactID=e.ContactID
                        WHERE e.ChatID=? AND m.Type=11 ORDER BY e.TimeStamp DESC LIMIT ?""",
                     (ch["ChatID"], a.n)).fetchall()
    rows = [r for r in rows if is_voice(r["PayloadPath"])][::-1]
    if not rows:
        print(f"No voice messages found in chat {ch['ChatID']} ({chat_label(c, ch)})."); return
    print(f"# Voice messages in chat {ch['ChatID']}: {chat_label(c, ch)}  (model={a.model or WHISPER_MODEL})")
    for r in rows:
        who = "Me" if r["Direction"] == 1 else (clean(r["Name"]) or clean(r["ClientName"]) or r["Number"] or "?")
        p = r["PayloadPath"]
        if not (p and os.path.exists(p)):
            print(f"{fmt_ts(r['TimeStamp'])}  {who}: [voice] (not downloaded - open it in Viber first)"); continue
        try:
            print(f"{fmt_ts(r['TimeStamp'])}  {who}: “{transcribe_file(p, a.model)}”")
        except Exception as e:
            print(f"{fmt_ts(r['TimeStamp'])}  {who}: [voice] transcription failed: {e}")


def cmd_sql(a):
    c = db()
    cur = c.execute(a.query)
    print("\t".join(d[0] for d in cur.description))
    for r in cur: print("\t".join("" if v is None else str(v) for v in r))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("refresh")
    f = s.add_parser("find"); f.add_argument("term")
    ch = s.add_parser("chats"); ch.add_argument("-n", type=int, default=20)
    r = s.add_parser("read"); r.add_argument("chat"); r.add_argument("-n", type=int, default=30)
    r.add_argument("--since"); r.add_argument("--today", action="store_true")
    r.add_argument("--no-refresh", action="store_true")
    r.add_argument("--voice", action="store_true", help="transcribe voice messages inline (local Whisper, slow)")
    se = s.add_parser("send"); se.add_argument("chat"); se.add_argument("text"); se.add_argument("--enter", action="store_true",
                    help="experimental: also press Enter (currently does not send in Viber 29.4)")
    tr = s.add_parser("transcribe"); tr.add_argument("target", help="audio file path, or a ChatID/name")
    tr.add_argument("-n", type=int, default=10, help="how many recent voice messages (when target is a chat)")
    tr.add_argument("--model", default=None, help="whisper model: base (fast) or medium (accurate, default)")
    tr.add_argument("--no-refresh", action="store_true")
    q = s.add_parser("sql"); q.add_argument("query")
    a = p.parse_args()
    {"refresh": lambda a: refresh(), "find": cmd_find, "chats": cmd_chats, "read": cmd_read,
     "send": cmd_send, "transcribe": cmd_transcribe, "sql": cmd_sql}[a.cmd](a)


if __name__ == "__main__":
    main()
