from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.settings import (
    AudioFormPreference,
    DeliveryReply,
    MediaPreference,
    PluginSettings,
)


class PluginSettingsTests(unittest.TestCase):
    def test_defaults_preserve_current_behavior(self) -> None:
        settings = PluginSettings.from_mapping(None)

        self.assertEqual(settings.media_preference, MediaPreference.VIDEO_FIRST)
        self.assertEqual(
            settings.audio_form_preference, AudioFormPreference.VOICE_FIRST
        )
        self.assertTrue(settings.video_allowed)
        self.assertTrue(settings.audio_allowed)
        self.assertEqual(settings.default_media, "video")
        self.assertEqual(settings.preferred_audio_form, "voice")
        self.assertEqual(settings.delivery_reply, DeliveryReply.NONE)
        self.assertFalse(settings.llm_reply_enabled)

    def test_mapping_values_are_parsed(self) -> None:
        settings = PluginSettings.from_mapping(
            {
                "media_priority": "audio_only",
                "audio_form_priority": "file_first",
                "delivery_reply": "llm",
            }
        )

        self.assertEqual(settings.media_preference, MediaPreference.AUDIO_ONLY)
        self.assertEqual(settings.audio_form_preference, AudioFormPreference.FILE_FIRST)
        self.assertFalse(settings.video_allowed)
        self.assertTrue(settings.audio_allowed)
        self.assertEqual(settings.default_media, "audio")
        self.assertEqual(settings.preferred_audio_form, "file")
        self.assertEqual(settings.delivery_reply, DeliveryReply.LLM)
        self.assertTrue(settings.llm_reply_enabled)

    def test_invalid_values_fall_back_to_defaults(self) -> None:
        settings = PluginSettings.from_mapping(
            {
                "media_priority": "unknown",
                "audio_form_priority": "unknown",
            }
        )

        self.assertEqual(settings.media_preference, MediaPreference.VIDEO_FIRST)
        self.assertEqual(
            settings.audio_form_preference, AudioFormPreference.VOICE_FIRST
        )

    def test_schema_is_valid_and_matches_enum_values(self) -> None:
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text())

        self.assertEqual(
            schema["media_priority"]["options"],
            [item.value for item in MediaPreference],
        )
        self.assertEqual(
            schema["audio_form_priority"]["options"],
            [item.value for item in AudioFormPreference],
        )
        self.assertEqual(schema["media_priority"]["default"], "video_first")
        self.assertEqual(schema["audio_form_priority"]["default"], "voice_first")
        self.assertEqual(
            schema["delivery_reply"]["options"],
            [item.value for item in DeliveryReply],
        )
        self.assertEqual(schema["delivery_reply"]["default"], "none")


if __name__ == "__main__":
    unittest.main()
