import logging
import warnings

logger = logging.getLogger(__name__)


def configure_pydub_runtime() -> str | None:
    try:
        import imageio_ffmpeg

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from pydub import AudioSegment

        AudioSegment.converter = ffmpeg_path
        AudioSegment.ffmpeg = ffmpeg_path
        return ffmpeg_path
    except Exception as exc:
        logger.warning("Could not configure bundled FFmpeg for pydub: %s", exc)
        return None
