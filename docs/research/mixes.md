# Same-track mixes (crossfades) in Kdenlive 26.08 projects

Evidence for #61. Source: `tests/fixtures/kd26/kd26-mixes.kdenlive`, made by placing three AV
clips of `ntsc.mp4` (29.97 fps) at `[0,40)`, `[40,80)` and `[80,130)` with handles, then
letting **Kdenlive 26.08.2** add the mixes (*Select All*, *Mix Clips*, headless GUI lab,
`scripts/make_fixtures.py mixes`). A second lab project (cuts at 90 and 180 frames, 15-frame
handles) gave the same structure.

## Structure

Each track is a `<tractor>` with two `<playlist>` layers. For a mix between a left clip L and a
right clip R:

- **Layers:** L and R are on different layers. Kdenlive alternates: in a chain of mixes the
  middle clip is on layer 1, its neighbours on layer 0.
- **Overlap:** both clips are extended into their handles, so they overlap. Here L became
  `[0,60)` (source 0..59) and R `[30,90)` (source 0..59): the 30-frame default mix duration,
  limited by R's 10-frame head handle.
- **Transition:** one `<transition>` inside the track tractor (not the sequence):

  | Property | Video track | Audio track |
  |---|---|---|
  | `mlt_service` / `kdenlive_id` | `luma` | `mix` |
  | `a_track` / `b_track` | `0`/`1` when L is on layer 0; `1`/`0` when L is on layer 1 | always `0`/`1` |
  | `reverse` | `0` | `1` when L is on layer 1 |
  | other | `softness=0`, `alpha_over=0`, `fix_background_alpha=1`, `invert=0`, `factory=loader` | `start=-1`, `accepts_blanks=1`, `duck_*` |

- **Bounds:** `in` is R's start. `out` is **L's end, exclusive** (`in`=30, `out`=60 for an
  overlap `[30,60)`), unlike other MLT ranges whose `out` is inclusive.
- **`kdenlive:mixcut`:** frames from the original cut to the end of the mix. The cut was at 40:
  `out - mixcut` = 60 - 20 = 40. (A symmetric mix cannot tell the two readings apart; this
  asymmetric one can.)
- **Groups:** the AV pairs stay `AVSplit` groups keyed by their new start positions.

## Consequences for editing (implemented in `timeline_edit.py`)

- A mix is identified by its bounds: the clip ending at `out` and the clip on the other layer
  starting at `in`. A same-track transition that does not match two clips is refused.
- Moving a mixed clip would leave the transition pointing at nothing: refused.
- Trimming the mixed side would change the mix: refused. The free side may change as long as the
  clip still covers the mix.
- Removing a mixed clip removes the mix and trims the partner back to the cut, which restores
  the edit as it was before the mix (verified: removing the middle clip gives back `[0,40)` and
  `[80,130)`).
