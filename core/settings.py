"""Small, validated plugin settings parsed from AstrBot's native config panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .media import (
    DOWNLOAD_MEDIA_LIMITS,
    VIDEO_MEDIA_LIMITS,
    VOICE_MEDIA_LIMITS,
    MediaLimits,
)


class MediaPreference(str, Enum):
    """How to resolve a request that did not say whether it wants video or audio.

    ``*_first`` keeps both media types available and only changes the default.
    ``*_only`` additionally disables explicit requests for the other type.
    """

    VIDEO_FIRST = "video_first"
    AUDIO_FIRST = "audio_first"
    VIDEO_ONLY = "video_only"
    AUDIO_ONLY = "audio_only"


class AudioFormPreference(str, Enum):
    """Preferred transport when audio was requested without a form word."""

    VOICE_FIRST = "voice_first"
    FILE_FIRST = "file_first"


class DeliveryReply(str, Enum):
    """Whether the LLM speaks one short preface before the delivery tool call.

    The preface is sent before media is prepared, so the user gets immediate
    feedback; the tool then ends the agent loop silently.
    """

    NONE = "none"
    LLM = "llm"


@dataclass(frozen=True, slots=True)
class PluginLimits:
    """Configured delivery budgets with the same shape as ``MediaLimits``."""

    voice: MediaLimits = VOICE_MEDIA_LIMITS
    video: MediaLimits = MediaLimits(
        max_bytes=50 * 1024 * 1024,
        max_duration_ms=None,
        download_timeout_seconds=VIDEO_MEDIA_LIMITS.download_timeout_seconds,
    )
    download: MediaLimits = MediaLimits(
        max_bytes=50 * 1024 * 1024,
        max_duration_ms=None,
        download_timeout_seconds=DOWNLOAD_MEDIA_LIMITS.download_timeout_seconds,
    )

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "PluginLimits":
        if config is None:
            return cls()
        voice_duration_minutes = _bounded_int(
            config.get("voice_duration_minutes"), 15, 1, 60
        )
        voice_size_mb = _bounded_int(config.get("voice_size_mb"), 25, 1, 100)
        video_duration_minutes = _bounded_int(
            config.get("video_duration_minutes"), 0, 0, 600
        )
        video_size_mb = _bounded_int(config.get("video_size_mb"), 50, 1, 500)
        download_duration_minutes = _bounded_int(
            config.get("download_duration_minutes"), 0, 0, 600
        )
        download_size_mb = _bounded_int(config.get("download_size_mb"), 50, 1, 500)

        return cls(
            voice=MediaLimits(
                max_bytes=voice_size_mb * 1024 * 1024,
                max_duration_ms=(
                    voice_duration_minutes * 60 * 1000
                    if voice_duration_minutes > 0
                    else None
                ),
                download_timeout_seconds=VOICE_MEDIA_LIMITS.download_timeout_seconds,
            ),
            video=MediaLimits(
                max_bytes=video_size_mb * 1024 * 1024,
                max_duration_ms=(
                    video_duration_minutes * 60 * 1000
                    if video_duration_minutes > 0
                    else None
                ),
                download_timeout_seconds=VIDEO_MEDIA_LIMITS.download_timeout_seconds,
            ),
            download=MediaLimits(
                max_bytes=download_size_mb * 1024 * 1024,
                max_duration_ms=(
                    download_duration_minutes * 60 * 1000
                    if download_duration_minutes > 0
                    else None
                ),
                download_timeout_seconds=DOWNLOAD_MEDIA_LIMITS.download_timeout_seconds,
            ),
        )


@dataclass(frozen=True, slots=True)
class PluginSettings:
    """The complete user-visible behavior surface of the plugin.

    Safety limits remain hard-coded in ``core/media.py``; this class only owns
    intent resolution, never byte or duration budgets.
    """

    media_preference: MediaPreference = MediaPreference.VIDEO_FIRST
    audio_form_preference: AudioFormPreference = AudioFormPreference.VOICE_FIRST
    delivery_reply: DeliveryReply = DeliveryReply.NONE
    show_uploader: bool = False
    limits: PluginLimits = field(default_factory=PluginLimits)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "PluginSettings":
        if config is None:
            return cls()
        return cls(
            media_preference=_enum_value(
                MediaPreference,
                config.get("media_priority"),
                MediaPreference.VIDEO_FIRST,
            ),
            audio_form_preference=_enum_value(
                AudioFormPreference,
                config.get("audio_form_priority"),
                AudioFormPreference.VOICE_FIRST,
            ),
            delivery_reply=_enum_value(
                DeliveryReply,
                config.get("delivery_reply"),
                DeliveryReply.NONE,
            ),
            show_uploader=bool(config.get("show_uploader", False)),
            limits=PluginLimits.from_mapping(config),
        )

    @property
    def video_allowed(self) -> bool:
        return self.media_preference is not MediaPreference.AUDIO_ONLY

    @property
    def audio_allowed(self) -> bool:
        return self.media_preference is not MediaPreference.VIDEO_ONLY

    @property
    def default_media(self) -> str:
        """Media type used when the LLM reports no explicit video/audio intent."""

        return "video" if self.media_preference.name.startswith("VIDEO") else "audio"

    @property
    def preferred_audio_form(self) -> str:
        """Transport selected for an audio request without an explicit form."""

        return (
            "voice"
            if self.audio_form_preference is AudioFormPreference.VOICE_FIRST
            else "file"
        )

    @property
    def llm_reply_enabled(self) -> bool:
        return self.delivery_reply is DeliveryReply.LLM


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _enum_value(enum_type: Any, value: object, default: Any) -> Any:
    if isinstance(value, enum_type):
        return value
    normalized = " ".join(str(value or "").split()).casefold()
    if not normalized:
        return default
    try:
        return enum_type(normalized)
    except ValueError:
        return default


__all__ = [
    "AudioFormPreference",
    "DeliveryReply",
    "MediaPreference",
    "PluginLimits",
    "PluginSettings",
]
