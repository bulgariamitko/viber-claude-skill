---
name: viber
description: Read and reply to your personal Viber messages (Viber Desktop on this Mac) - chats, contacts, groups, images/files people sent, and voice messages (transcribed locally). Use whenever the user mentions Viber, or asks to read/check/summarize messages from a person or group, look at images someone sent, transcribe/listen to a voice message, or reply/answer/write to someone on Viber (e.g. "read <contact>'s Viber messages and the images they sent", "what did <contact> write", "transcribe their voice message", "reply to them that...").
---

# Viber (local, unofficial)

Reads the user's own Viber Desktop data locally and drives the Viber app for replies. It decrypts
a working copy of the message database (`~/ViberLocal/viber_plain.db`, kept outside any cloud-synced
folder) and never uploads anything. Tool: `python3 ~/.claude/skills/viber/scripts/viber.py`.

**This is unofficial and for the user's own account only.** It depends on how the installed Viber
version stores and unlocks its data, so a Viber update can break it (see Troubleshooting). One-time
setup (`capture_key.sh`) and build steps are in the README.

## Reading
1. `viber.py find <name>` - lists matching chats (ChatID, name, number, last activity). If the
   contact's name is in a non-Latin script, a Latin spelling may find nothing: retry the native
   spelling (e.g. transliterated back), or a short partial stem. If several match, pick the 1:1 chat
   with the most recent activity, or ask the user if genuinely unclear.
2. `viber.py read <ChatID> [-n 30] [--today] [--since YYYY-MM-DD]` - refreshes the copy (~5 s, Viber
   stays open) and prints messages oldest->newest as `time  sender: text`.
   Images/videos/files print as `[image] /path/to/file.jpg` - **open images with the Read tool** to
   see them. Voice messages print as `[voice] /path/.../PTT/....m4a`.
   "(not downloaded)" means the user never opened it in Viber Desktop; a thumbnail path may be given.
3. Other: `viber.py chats -n 20` (recent chats), `viber.py sql "<query>"` (read-only SQL on the copy;
   tables Events, Messages, Contact, ChatInfo, ChatRelation; Events.TimeStamp is ms; Direction 1 = the user).

## Voice messages (transcription)
Transcription is **fully local** (openai-whisper + ffmpeg on this Mac) - no API key, no cloud, audio
never leaves the machine. Transcripts are cached in `~/ViberLocal/transcripts/` (one per clip per model).
- `viber.py transcribe <ChatID|name> [-n 10] [--model base|medium]` - transcribe the recent voice
  messages in a chat, printed as `time  sender: "text"`.
- `viber.py transcribe <path-to.m4a>` - transcribe a single audio file.
- `viber.py read <ChatID> --voice` - inline transcripts while reading a chat.
- **Model:** `medium` (default) is accurate for many non-English languages; `base` is ~8x faster but
  rougher - use it only for a quick gist or when there are many clips. On CPU, `medium` runs roughly
  real-time to 2x (a 75 s clip ~2 min), so a whole chat can be slow; prefer a small `-n`, or name the
  one clip the user means. Override the default with env var `VIBER_WHISPER_MODEL=base|small|medium|large`.
- **Quality caveat:** short or noisy clips can come out rough even on `medium`. Flag low-confidence
  results to the user rather than presenting a shaky transcript as exact; offer to retry a specific clip.
- **Optional cloud transcription:** if the user prefers a hosted speech-to-text API for speed/accuracy,
  it can be added reading the key from an env var or a user-pointed file. Never read API keys out of
  another app's storage; only use a key the user explicitly provides.

## Replying
- Draft the reply in the user's language/tone and **show the exact text to the user and get explicit
  approval before calling send**. Never send on your own initiative.
- `viber.py send <ChatID> "<text>"` opens that 1:1 chat in Viber and pastes the text into the input box
  as a **draft**. Then tell the user: "Pasted into Viber - press Enter to send." It does NOT send.
  (`--enter` attempts an automatic Enter but does not reliably send on current Viber builds - avoid it.)
- Group chats can't be deep-linked: tell the user to open the group; you can still prepare the text.
- Multi-line text is fine via paste.
- The paste goes to the Viber window, so mention it if the user may be typing in Viber at that moment.

## Troubleshooting
- "Decryption failed - the key changed" (after a Viber update or re-linking): ask the user first, then run
  `~/.claude/skills/viber/scripts/capture_key.sh` - it quits Viber for ~1 minute, captures the key
  into `~/ViberLocal/.viber_keys`, and reopens Viber.
- `vsql` binary missing (first install, or broken after a Viber/Qt update): rebuild it -
  `clang++ -std=c++17 -O1 -o vsql vsql.cpp` in the scripts dir (needs Xcode Command Line Tools).
- Whisper model missing / "SSL CERTIFICATE_VERIFY_FAILED" on first transcribe: Python couldn't download
  the model (often a TLS-intercepting security tool). Models live in `~/.cache/whisper/`. Get the URL from
  `python3 -c "import whisper; print(whisper._MODELS['small'])"` then
  `curl -Lo ~/.cache/whisper/small.pt "<url>"`.
- Accessibility errors on send: the terminal app needs Accessibility permission (System Settings ->
  Privacy & Security -> Accessibility).

## Privacy
Message contents read here are sent to the model provider as part of the conversation, like any file.
Only read the chats the user asked about; don't dump unrelated conversations. The decrypted copy and
transcripts stay on the user's machine under `~/ViberLocal/`.
