import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mangobd.rapid_x import RapidXClient, _read_env_file


class RapidXConfigurationTests(unittest.TestCase):
    def test_reads_dotenv_without_mutating_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("RAPID_X_API_KEY=test-only\nRAPID_X_HOST=example.test\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                client = RapidXClient(env_file=path, cache_dir=Path(directory) / "cache")
                self.assertEqual(client.api_key, "test-only")
                self.assertEqual(client.host, "example.test")
                self.assertNotIn("RAPID_X_API_KEY", os.environ)

    def test_environment_takes_precedence_over_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("RAPID_X_API_KEY=file-key\n", encoding="utf-8")
            with patch.dict(os.environ, {"RAPID_X_API_KEY": "env-key"}, clear=True):
                client = RapidXClient(env_file=path, cache_dir=Path(directory) / "cache")
                self.assertEqual(client.api_key, "env-key")

    def test_dotenv_parser_supports_export_and_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("export RAPID_X_API_KEY='quoted'\n", encoding="utf-8")
            self.assertEqual(_read_env_file(path)["RAPID_X_API_KEY"], "quoted")

    def test_custom_ca_bundle_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            ca_bundle = Path(directory) / "ca.pem"
            path.write_text("RAPID_X_API_KEY=test-only\n", encoding="utf-8")
            ca_bundle.write_text("test-ca\n", encoding="utf-8")
            with patch("mangobd.rapid_x.ssl.create_default_context") as create_context:
                RapidXClient(
                    env_file=path,
                    cache_dir=Path(directory) / "cache",
                    ca_bundle=ca_bundle,
                )
            create_context.assert_called_once_with(cafile=str(ca_bundle))


if __name__ == "__main__":
    unittest.main()
