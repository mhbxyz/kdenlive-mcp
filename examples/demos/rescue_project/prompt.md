# Prompt

> `WORKDIR/rescue.kdenlive` is a Kdenlive project a colleague sent me. Its media folder was
> renamed: the files are now in `WORKDIR/footage/`.
>
> 1. Tell me which Kdenlive version made it and what is wrong with it.
> 2. Fix the missing media.
> 3. The first crossfade is too long: make it about 12 frames. Keep the second one.
> 4. The middle shot is too loud: lower it by 6 dB.
> 5. End the video with a short fade to black and fade the sound out at the same time.
> 6. Put a guide at the start ("Opening") and one where the last shot starts ("Last shot").
> 7. Render it to `WORKDIR/rescued.mp4` with melt, and tell me what you checked.
>
> Edit the project in place, and do not touch anything else in it.

The server must be allowed to read and write `WORKDIR` (`KDENLIVE_MCP_ALLOWED_ROOTS=WORKDIR`).
