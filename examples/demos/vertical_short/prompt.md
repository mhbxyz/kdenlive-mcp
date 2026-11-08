# Prompt

> In the folder `WORKDIR/media` there are three landscape shots (`beach.mp4`, `city.mp4`,
> `night.mp4`, 4 seconds each at 30 fps) and a music file (`music.wav`).
>
> Make a vertical short for social media in `WORKDIR/short.kdenlive`:
>
> - vertical 1080×1920 at 30 fps;
> - the three shots in that order, 3 seconds each, cropped so they fill the vertical frame (no
>   black bars);
> - a short crossfade between consecutive shots;
> - make the night shot darker;
> - a title "Summer / in 3 shots" (two lines) at the top during the first two seconds;
> - the music under the whole edit, quieter than the shots, fading in briefly;
> - all the sound fading out over the last second and a half;
>
> then render it to `WORKDIR/short.mp4` and tell me what you checked.

`WORKDIR` is the folder given to `run_demo.py` / `make_media.py`. The server must be allowed to
read and write there (`KDENLIVE_MCP_ALLOWED_ROOTS=WORKDIR`).
