# Prompt

> In `WORKDIR/media` are the shots of my trip to Portugal: `01-lisbon-1.mp4` … `03-algarve-4.mp4`
> (12 shots, 4 seconds each, three days of four shots), one voice-over per day
> (`voice-1.wav`, `voice-2.wav`, `voice-3.wav`) and `music.wav`.
>
> Make a vlog in `WORKDIR/vlog.kdenlive`:
>
> - the 12 shots in order, 3 seconds each, with short crossfades between them;
> - each day's voice-over starting one second into that day;
> - the subtitles in `WORKDIR/voice-over.srt` (timed for voice-overs placed one second into
>   each day);
> - the music under the whole vlog, quiet, and even quieter while someone speaks; a short fade-in
>   and a 2-second fade-out;
> - an intro title "Portugal / Summer 2026" and a closing "Thanks for watching";
> - a guide at the start of each day.
>
> Render it in 1080p to `WORKDIR/vlog.mp4` and tell me what you checked.

The server must be allowed to read and write `WORKDIR` (`KDENLIVE_MCP_ALLOWED_ROOTS=WORKDIR`).
