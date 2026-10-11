import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bark_monitor.cli.get_param import NextCloudParameters, Parameters


class TestConfig(unittest.TestCase):
    def test_local_config_does_not_require_api_key(self) -> None:
        with TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            parameters = Parameters(
                local=True,
                output_folder=folder / "recordings",
                config_folder=folder / "config",
            )
            config_path = folder / "config.json"
            parameters.save(config_path)

            loaded_parameters = Parameters.read(config_path)

        self.assertTrue(loaded_parameters.local)
        self.assertIsNone(loaded_parameters.api_key)

    def test_save_load_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            parameters = Parameters(
                accept_new_users=True,
                api_key="test_api_key",
                output_folder=folder / "test/data",
                config_folder=folder / "config",
            )
            config_path = folder / "config.json"
            parameters.save(config_path)

            new_parameters = Parameters.read(config_path)

            self.assertEqual(new_parameters.output_folder, folder / "test/data")
            self.assertEqual(new_parameters.api_key, "test_api_key")
            self.assertTrue(new_parameters.accept_new_users)

    def test_next_cloud(self) -> None:
        with TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            parameters = Parameters(
                accept_new_users=True,
                api_key="test_api_key",
                output_folder=folder / "test/data",
                config_folder=folder / "config",
                nextcloud_parameters=NextCloudParameters(
                    user="user", server="server", passwd="passwd"
                ),
            )

            parameters.save(folder / "config_nc.json")
