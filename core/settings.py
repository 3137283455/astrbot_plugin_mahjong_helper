"""Small, validated plugin settings parsed from AstrBot's native config panel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


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


@dataclass(frozen=True, slots=True)
class PluginSettings:
    """The complete user-visible behavior surface of the plugin.

    Safety limits remain hard-coded in ``core/media.py``; this class only owns
    intent resolution, never byte or duration budgets.
    """

    media_preference: MediaPreference = MediaPreference.VIDEO_FIRST
    audio_form_preference: AudioFormPreference = AudioFormPreference.VOICE_FIRST

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
    "MediaPreference",
    "PluginSettings",
]
