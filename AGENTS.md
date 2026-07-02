# Project Instructions

For any request involving creative work, marketing strategy, social media, Instagram Reels, viral content, copywriting, visual concepts, storytelling, brand positioning, campaign ideas, hooks, scripts, naming, messaging, or creative direction, automatically use the native Codex skill at `.codex/skills/creative-ideation/SKILL.md`.

Do not return generic brainstorms. Apply the skill's obviousness filter before presenting ideas, and include the skill's required output fields for each concept unless the user explicitly asks for a different format.

For any request involving editing raw video footage (cutting, trimming filler words/silence, color grading, subtitles, animation overlays, or assembling a `final.mp4` from clips), use the vendored skill at `.claude/skills/video-use/SKILL.md`. Read `.claude/skills/video-use/install.md` first if `ffmpeg`, Python deps (`uv sync`), or the `ELEVENLABS_API_KEY` haven't been set up yet in the current environment — ask the user to paste the API key rather than guessing it. Then follow `SKILL.md` for the editing workflow, and consult `helpers/` and `skills/manim-video/` for the underlying scripts.
