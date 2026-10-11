import sounddevice

for i, device in enumerate(sounddevice.query_devices()):
    if device["max_input_channels"] > 0:
        print(
            "Input Device id ",
            i,
            " - ",
            device["name"],
            " framerate ",
            device["default_samplerate"],
        )
