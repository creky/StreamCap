from ..base import FFmpegCommandBuilder

SEGMENTED_MP4_MOVFLAGS = "movflags=+frag_keyframe+empty_moov+default_base_moof"
STREAMABLE_MP4_MOVFLAGS = "+frag_keyframe+empty_moov+default_base_moof"


class MP4CommandBuilder(FFmpegCommandBuilder):
    def build_command(self) -> list[str]:
        command = self._get_basic_ffmpeg_command()
        # fmt: off
        if self.segment_record:
            additional_commands = [
                *self._get_video_codec_options(),
                "-c:a", "aac",
                "-map", "0",
                "-f", "segment",
                "-segment_time", str(self.segment_time),
                "-segment_format", "mp4",
                "-segment_format_options", SEGMENTED_MP4_MOVFLAGS,
                "-reset_timestamps", "1",
                "-flags", "global_header",
                self.full_path,
            ]
        else:
            additional_commands = [
                "-map", "0",
                *self._get_video_codec_options(),
                "-c:a", "copy",
                "-f", "mp4",
                "-movflags", STREAMABLE_MP4_MOVFLAGS,
                self.full_path,
            ]
        # fmt: on

        command.extend(additional_commands)
        return command
