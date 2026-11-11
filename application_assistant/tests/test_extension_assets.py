"""Static security checks for the Chrome extension package."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


EXTENSION_DIRECTORY = Path(__file__).resolve().parents[1] / "browser_extension"


class ExtensionAssetTests(unittest.TestCase):
    def test_extension_never_contains_openai_key_or_candidate_profile(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in EXTENSION_DIRECTORY.iterdir()
            if path.is_file()
        )

        self.assertNotIn("OPENAI_API_KEY", combined)
        self.assertNotIn("candidate_profile", combined)

    def test_manifest_uses_active_tab_and_localhost_only(self) -> None:
        manifest = json.loads((EXTENSION_DIRECTORY / "manifest.json").read_text())

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertIn("activeTab", manifest["permissions"])
        self.assertEqual(manifest["host_permissions"], ["http://127.0.0.1:8765/*"])
        self.assertNotIn("<all_urls>", json.dumps(manifest))

    def test_browser_fill_is_only_bound_to_explicit_fill_button(self) -> None:
        source = (EXTENSION_DIRECTORY / "popup.js").read_text(encoding="utf-8")

        self.assertIn('fillButton.addEventListener("click", fillSelected)', source)
        self.assertEqual(source.count("func: fillAssignments"), 1)
        self.assertNotIn("submit()", source)
        self.assertNotIn(".click()", source)


if __name__ == "__main__":
    unittest.main()
