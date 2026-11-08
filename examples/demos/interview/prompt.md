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
> - a lower third for each person when they first appear ("Sam Lee / Interviewer",
>   "Ana Ruiz / Boat builder"), bottom left but above the subtitles;
> - the transcript as subtitles;
> - make both voices about as loud as each other;
> - guides at the question, the answer and each cutaway.

Then the media folder is moved to an archive disk (`run_demo.py` does it itself between the edit
and the render), and the assistant is told:

> I moved the media folder to `WORKDIR/archive/2026/media`. Fix the project, render it to
> `WORKDIR/interview.mp4` and tell me what you checked.

The server must be allowed to read and write `WORKDIR`.
