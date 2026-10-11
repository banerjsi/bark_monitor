import logging
import sys
import time
from pathlib import Path

import tyro

from bark_monitor import logger
from bark_monitor.cli.get_param import Parameters


def _read_parameters() -> Parameters:
    arguments = sys.argv[1:]
    if "--config-file" not in arguments:
        return tyro.cli(Parameters)

    config_index = arguments.index("--config-file")
    try:
        config_file = Path(arguments[config_index + 1])
    except IndexError as error:
        raise ValueError("--config-file requires a path") from error

    parameters = Parameters.read(config_file)
    if "--local" in arguments:
        parameters.local = True
    return parameters


def main() -> None:
    parameters = _read_parameters()

    if parameters.local:
        from bark_monitor.fake_sync import FakeSync
        from bark_monitor.recorders.yamnet_lite_recorder import YamnetLiteRecorder

        logging.basicConfig(level=logging.INFO)
        logger.info("Starting in local-only mode; cloud integrations are disabled")
        recorder = YamnetLiteRecorder(
            sync=FakeSync(),
            output_folder=parameters.output_folder,
            framerate=parameters.microphone_framerate,
            send_text_callback=logging.info,
        )
        recorder.record()
        try:
            while recorder.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping local recorder")
        finally:
            if recorder.running:
                recorder.stop()
        return

    if parameters.nextcloud_parameters is not None:
        from bark_monitor.next_cloud_sync import NextCloudSync

        sync_service = NextCloudSync(
            server=parameters.nextcloud_parameters.server,
            user=parameters.nextcloud_parameters.user,
            passwd=parameters.nextcloud_parameters.passwd,
        )
    elif parameters.google_creds is not None:
        from bark_monitor.google_sync import GoogleSync

        sync_service = GoogleSync(credential_file=parameters.google_creds)
    else:
        raise RuntimeError("Please provide either google_creds or nextcloud_parameters")

    if parameters.api_key is None:
        raise RuntimeError("Please provide a Telegram bot API key")

    from bark_monitor.bots.very_bark_telegram_bot import VeryBarkTelegramBot
    from bark_monitor.recorders.yamnet_recorder import YamnetRecorder

    bot = VeryBarkTelegramBot(
        sync=sync_service,
        api_key=parameters.api_key,
        config_folder=parameters.config_folder,
        accept_new_users=parameters.accept_new_users,
    )

    recorder = YamnetRecorder(
        sync=sync_service,
        output_folder=parameters.output_folder,
        sampling_time_bark_seconds=parameters.sampling_time_bark_seconds,
        http_url=parameters.thingsboard_uri,
        framerate=parameters.microphone_framerate,
        send_text_callback=bot.send_text,
    )
    bot.start(recorder)


if __name__ == "__main__":
    main()
