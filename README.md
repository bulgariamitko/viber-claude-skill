# Viber skill for Claude (local, macOS)

A [Claude](https://claude.com/claude-code) **skill** that lets Claude read and reply to **your own**
Viber messages on macOS — chats, contacts, groups, the images/files people send, and voice messages
(transcribed locally). Everything runs on your Mac; no message content is uploaded except what Claude
reads back to you in the conversation.

You can ask things like:

> "Read the last messages from `<contact>` and the images they sent, transcribe their voice message,
> and draft a reply."

Claude finds the chat, refreshes a decrypted local copy of your history, opens images, transcribes
voice notes with a local Whisper model, drafts a reply, and (with your approval) pastes it into Viber
for you to send with Enter.

---

## ⚠️ Use on your own data only

This is **unofficial** and not affiliated with Viber/Rakuten. Viber has no official API for personal
accounts, so this works by reading your own local Viber Desktop database and driving your own Viber app.

- Use it only on **your own Mac, your own Viber account, and your own messages.**
- It reads your local data; it does not access anyone else's account.
- It's fragile by nature: a Viber update can change how data is stored/unlocked and break it until the
  key is re-captured or the helper is rebuilt (see Troubleshooting).
- You are responsible for complying with the law and Viber's terms in your jurisdiction.

The decryption technique (capturing the DB key from the running app) is adapted from
[`viber-export-macos`](https://github.com/Anastasia-D-Kravchenko/viber-export-macos) (MIT).

---

## Requirements

- **macOS** with **Viber Desktop** installed and **signed in**.
- **Xcode Command Line Tools** (`xcode-select --install`) — provides `clang++`, `lldb`, `codesign`.
- **Python 3**.
- For voice transcription: **ffmpeg** and **openai-whisper**
  (`brew install ffmpeg` and `pip3 install openai-whisper`). Optional — reading/replying work without it.
- Claude Code (or another Claude client that loads skills from `~/.claude/skills/`).

## Install

```bash
# 1. Put the skill where Claude looks for personal skills
git clone https://github.com/bulgariamitko/viber-claude-skill.git
mkdir -p ~/.claude/skills/viber
cp -R viber-claude-skill/SKILL.md viber-claude-skill/scripts ~/.claude/skills/viber/

# 2. Build the small decryption helper
cd ~/.claude/skills/viber/scripts
clang++ -std=c++17 -O1 -o vsql vsql.cpp

# 3. Capture your Viber database key once (quits Viber for ~1 minute, then reopens it)
./capture_key.sh
```

That's it. In Claude Code, just mention Viber (e.g. *"read my latest Viber messages from `<contact>`"*)
and the skill activates.

The first transcription downloads a Whisper model (a few hundred MB) into `~/.cache/whisper/`.

## Usage (the CLI the skill drives)

```bash
python3 ~/.claude/skills/viber/scripts/viber.py find <name|number>
python3 ~/.claude/skills/viber/scripts/viber.py read <ChatID> [-n 30] [--today] [--since YYYY-MM-DD] [--voice]
python3 ~/.claude/skills/viber/scripts/viber.py transcribe <ChatID|name|file.m4a> [--model base|medium]
python3 ~/.claude/skills/viber/scripts/viber.py chats [-n 20]
python3 ~/.claude/skills/viber/scripts/viber.py send <ChatID> "<text>"   # pastes a draft; you press Enter
python3 ~/.claude/skills/viber/scripts/viber.py sql "<read-only query>"
```

Data lives under `~/ViberLocal/` (`viber_plain.db`, `transcripts/`, and the key file). Keep that
folder out of any cloud-synced directory.

## How it works

1. **Key capture** (`capture_key.sh`, once): launches a temporary, ad-hoc-re-signed debuggable copy of
   Viber under `lldb`, records the `PRAGMA hexkey` your Viber issues to unlock its database, and saves
   it to `~/ViberLocal/.viber_keys`. Your installed Viber is never modified.
2. **Refresh** (`vsql`, a tiny C++ helper): opens the live encrypted `viber.db` through Viber's own
   bundled SQLite codec using the saved key, and copies the tables into a plaintext
   `~/ViberLocal/viber_plain.db`. Fast, and works while Viber stays open.
3. **Read/search/transcribe**: plain SQLite queries over that copy; images/files are read from Viber's
   `Downloads/` folder; voice notes are transcribed with a local Whisper model (cached).
4. **Reply**: opens the 1:1 chat via a `viber://` deep link and pastes your approved text as a draft.

## Security & privacy notes

- `vsql` reads the encrypted DB; all of its writes go to `~/ViberLocal/`. The decrypted copy and
  transcripts are **unencrypted** — they're written owner-only (`0600`), but store them somewhere safe.
- `capture_key.sh` re-signs a throwaway **copy** of Viber (ad-hoc, `get-task-allow`) so a debugger can
  read the key from it. Antivirus/EDR may flag this; nothing leaves your machine.
- No key material is persisted in logs; diagnostic output redacts it.
- What Claude reads is sent to your model provider as part of the conversation, like any file you share.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Decryption failed - the key changed` | Re-run `./capture_key.sh` (after a Viber update or re-link). |
| `vsql` missing or errors after a Viber update | Rebuild: `clang++ -std=c++17 -O1 -o vsql vsql.cpp`. |
| First transcribe fails with `SSL CERTIFICATE_VERIFY_FAILED` | Python couldn't download the model. Get the URL from `python3 -c "import whisper; print(whisper._MODELS['small'])"` and `curl -Lo ~/.cache/whisper/small.pt "<url>"`. |
| `send` does nothing | Give your terminal app Accessibility permission (System Settings → Privacy & Security → Accessibility). |

## License

MIT — see [LICENSE](LICENSE). Not affiliated with, endorsed by, or supported by Viber / Rakuten.
