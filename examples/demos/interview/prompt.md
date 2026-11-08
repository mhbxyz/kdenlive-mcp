# Prompt

> In `WORKDIR/media`: `question.mp4` (Sam asking, recorded too quietly), `answer.mp4` (Ana
> answering), two silent cutaways `cutaway_hull.mp4` and `cutaway_tools.mp4`; the transcript is
> `WORKDIR/interview.srt`.
>
> Edit the interview in `WORKDIR/interview.kdenlive`:
>
> - the question, then the answer;
> - the hull cutaway (2 s) one second into the answer and the tools cutaway (2 s) four seconds
>   into it, over the picture, keeping Ana's voice;
> - a name title for each person when they first appear ("Sam Lee / Interviewer",
>   "Ana Ruiz / Boat builder"), top left, since subtitles go at the bottom;
> - the transcript as subtitles;
> - make both voices about as loud as each other;
> - guides at the question, the answer and each cutaway.
>
> Then I moved the media folder to `WORKDIR/archive/2026/media` — fix the project, render it to
> `WORKDIR/interview.mp4` and tell me what you checked.

(`run_demo.py` moves the folder itself between the edit and the render.) The server must be
allowed to read and write `WORKDIR`.
