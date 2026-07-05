"""
Voice-note transcoding — normalizes every voice note to AAC audio in an .m4a
container at upload time.

Why this exists: MediaRecorder on Chrome/Firefox/Android produces
audio/webm;codecs=opus by default. WebKit (Safari/iOS) cannot decode the
WebM container at all — not a missing codec, a hard platform limitation —
so any voice note recorded on a non-Safari device is permanently unplayable
for every iOS listener. Transcoding to AAC/m4a server-side, once, at upload
time fixes this for every future listener regardless of recording device.
"""
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_TRANSCODE_TIMEOUT_SECONDS = 15

# Already broadly compatible with Safari/iOS — re-encoding would just burn
# CPU for no playback-compatibility benefit.
SKIP_TRANSCODE_MIMES = {"audio/mp4", "audio/x-m4a"}


def transcode_voice_note(file_bytes: bytes) -> bytes | None:
    """
    Transcode raw audio bytes (webm/ogg/wav/etc.) to AAC-in-m4a via ffmpeg.

    Returns the transcoded bytes, or None on any failure (missing ffmpeg
    binary, non-zero exit, timeout, corrupt input) — callers should treat
    None as "keep the original upload," never as a reason to fail the
    request. A slightly-less-compatible voice note beats a failed send.
    """
    in_path = out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as in_file:
            in_file.write(file_bytes)
            in_path = in_file.name

        out_path = in_path + ".m4a"

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", in_path,
                "-vn",
                "-c:a", "aac",
                "-b:a", "64k",
                "-movflags", "+faststart",
                out_path,
            ],
            capture_output=True,
            timeout=_TRANSCODE_TIMEOUT_SECONDS,
        )

        if result.returncode != 0:
            logger.warning(
                "Voice note transcode failed (ffmpeg exit %s): %s",
                result.returncode,
                result.stderr.decode("utf-8", errors="replace")[-500:],
            )
            return None

        with open(out_path, "rb") as f:
            return f.read()

    except FileNotFoundError:
        logger.warning("Voice note transcode skipped: ffmpeg binary not found")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Voice note transcode timed out after %ss", _TRANSCODE_TIMEOUT_SECONDS)
        return None
    except Exception:
        logger.exception("Voice note transcode failed unexpectedly")
        return None
    finally:
        for path in (in_path, out_path):
            if path and os.path.exists(path):
                os.remove(path)
