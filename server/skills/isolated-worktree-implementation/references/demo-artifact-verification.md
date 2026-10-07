# Demo artifact verification notes

Use this when the task produces a video, image, audio file, or other human-facing artifact.

## Minimum verification

- Confirm the reported artifact path exists.
- Decode or open the artifact with a real tool, not just by trusting the generator summary.
- For video, sample at least one frame from the middle and one near the end.
- For image overlays, inspect the actual pixels or open the image in a viewer.
- If the task claims a UI overlay or caption is readable, verify it on the rendered artifact at the target resolution.

## Useful command pattern

```bash
ffprobe -v error -show_streams -show_format output.mp4
ffmpeg -v error -ss 00:00:05 -i output.mp4 -frames:v 1 frame-5s.png
ffmpeg -v error -sseof -2 -i output.mp4 -frames:v 1 frame-final.png
```

## Pitfalls

- A successful encoder exit does not prove the overlay text fits or is legible.
- A subagent summary is not evidence for a media artifact.
- If the artifact is ignored by git, still verify it locally before reporting.
- If the first frame looks fine, check a later frame too; overlays often clip only when the scene changes.
