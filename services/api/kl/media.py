"""Diagrams, audio, and video (Q8, K11): opt-in per team, through models the team chose.

- Transcripts a source already has (WebVTT, SubRip) become sections, one per minute or so, each headed by the time it
  starts ("At 00:01:23"), so a citation points to the moment.
- Audio and video are read only by a speech model the team chose (an OpenAI-compatible /audio/transcriptions), into
  the same timestamped sections; without one they aren't indexed, and the sync says why.
- Diagrams and images are described by a vision model the team chose (their text and structure), beside any text OCR
  finds; without one, OCR alone, as before.

Every model call is egress: a team's model is used only for content its allowed classifications include (invariant 7),
which `media_for` checks before handing the extractors anything."""
import re
from dataclasses import dataclass
from collections.abc import Callable

TRANSCRIPT_FORMATS = (".vtt", ".srt")
AUDIO_FORMATS = (".mp3", ".m4a", ".wav", ".ogg", ".flac")
VIDEO_FORMATS = (".mp4", ".webm", ".mov")
SECTION_SECONDS = 60
CUE = re.compile(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->")
VISION_PROMPT = ("Describe this image for search: transcribe any text in it, and say what it shows. For a diagram, name "
                 "its boxes and how the arrows connect them. Plain sentences, no preamble.")

TEAM_MEDIA_SQL = """
SELECT v.provider AS v_provider, v.model AS v_model, v.base_url AS v_base, v.credential_ref AS v_ref, v.allowed_classifications AS v_allowed,
       s.provider AS s_provider, s.model AS s_model, s.base_url AS s_base, s.credential_ref AS s_ref, s.allowed_classifications AS s_allowed
FROM team_media t
LEFT JOIN model_endpoints v ON v.id = t.vision_model_id
LEFT JOIN model_endpoints s ON s.id = t.speech_model_id
WHERE t.workspace_id = %s"""


@dataclass
class Media:
    vision: Callable[[bytes, str], str] | None = None             # (image bytes, its extension) -> a description
    speech: Callable[[bytes, str], list[tuple[float, str]]] | None = None  # (audio or video, its file name) -> segments


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcript_markdown(segments: list[tuple[float, str]]) -> str:
    """Timestamped segments as Markdown: a section per minute or so, each headed by when it starts."""
    lines, window = [], None
    for start, text in sorted(segments):
        text = " ".join(text.split())
        if not text:
            continue
        if window is None or start - window >= SECTION_SECONDS:
            window = start
            lines += ["", f"## At {stamp(start)}", ""]
        lines.append(text)
    return "\n".join(lines)


def parse_cues(text: str) -> list[tuple[float, str]]:
    """A WebVTT or SubRip file's cues as (start in seconds, text)."""
    out, start, buf = [], None, []
    for line in text.replace("\r", "").split("\n") + [""]:
        m = CUE.search(line)
        if m:
            if start is not None and buf:
                out.append((start, " ".join(buf)))
            hours, minutes, seconds, millis = (int(g or 0) for g in m.groups())
            start, buf = hours * 3600 + minutes * 60 + seconds + millis / 1000, []
        elif not line.strip():
            if start is not None and buf:
                out.append((start, " ".join(buf)))
            start, buf = (start if not buf else None), []
        elif start is not None and not line.strip().isdigit() and not line.startswith(("WEBVTT", "NOTE")):
            buf.append(re.sub(r"<[^>]+>", "", line).strip())
    return out


def media_for(conn, workspace_id, classification: str = "internal") -> Media | None:
    """The media models a team chose, each only if it may receive content of this classification (egress)."""
    from .gateway import describe_image, transcribe
    from .vault import vault
    model_key = lambda ref: vault.get(ref).get("api_key") if ref else None  # read at the moment of use
    row = conn.execute(TEAM_MEDIA_SQL, (workspace_id,)).fetchone()
    if not row:
        return None
    media = Media()
    if row["v_model"] and classification in (row["v_allowed"] or []):
        key = model_key(row["v_ref"])
        media.vision = lambda data, ext, r=row, key=key: describe_image(r["v_provider"], r["v_model"], r["v_base"], key, data, ext, VISION_PROMPT)
    if row["s_model"] and classification in (row["s_allowed"] or []):
        key = model_key(row["s_ref"])
        media.speech = lambda data, name, r=row, key=key: transcribe(r["s_provider"], r["s_model"], r["s_base"], key, data, name)
    return media if media.vision or media.speech else None


if __name__ == "__main__":
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nWelcome to the cutover rehearsal.\n\n00:01:05.500 --> 00:01:09.000\n<v Olivia>We fail over at noon.\n"
    cues = parse_cues(vtt)
    assert cues == [(1.0, "Welcome to the cutover rehearsal."), (65.5, "We fail over at noon.")], cues
    srt = "1\n00:00:02,000 --> 00:00:03,000\nHello\n\n2\n01:00:00,000 --> 01:00:01,000\nAn hour in\n"
    assert parse_cues(srt) == [(2.0, "Hello"), (3600.0, "An hour in")], parse_cues(srt)
    md = transcript_markdown(cues)
    assert "## At 00:00:01" in md and "## At 00:01:05" in md, md
    print("media: ok")
