import shutil
import subprocess
import tempfile
import unittest

from core.utils.audio_transcode import transcode_voice_note


def _make_webm_bytes(duration_seconds=0.5):
    with tempfile.NamedTemporaryFile(suffix=".webm") as f:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono",
                "-t", str(duration_seconds),
                "-c:a", "libopus",
                f.name,
            ],
            capture_output=True,
            check=True,
        )
        f.seek(0)
        return f.read()


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class AudioTranscodeTests(unittest.TestCase):
    """
    Verifies the WebM/Opus -> AAC/m4a transcoding used to make voice notes
    playable on Safari/iOS regardless of the recording browser. Skipped
    entirely in any environment without ffmpeg (e.g. a Render deploy that
    hasn't rebuilt the Docker image yet) rather than failing.
    """

    def test_transcodes_webm_to_valid_m4a(self):
        webm_bytes = _make_webm_bytes()
        result = transcode_voice_note(webm_bytes)

        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        # M4A/MP4 container: 'ftyp' box appears within the first few bytes.
        self.assertIn(b"ftyp", result[:32])

    def test_garbage_input_returns_none_not_exception(self):
        result = transcode_voice_note(b"this is not an audio file at all")
        self.assertIsNone(result)

    def test_empty_input_returns_none(self):
        result = transcode_voice_note(b"")
        self.assertIsNone(result)
