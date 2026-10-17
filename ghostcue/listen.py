"""Print what is heard on each side of a call. A check of the audio devices and the speech model."""

import argparse
import queue
import time

import pyaudiowpatch as pyaudio

from . import audio, config
from .audio import CLIENT, ME, AudioError, Source
from .stt import Transcriber


def main():
    settings = config.load()
    parser = argparse.ArgumentParser(description="Print what is heard on each side of a call.")
    parser.add_argument("--model", default=settings["speech_model"], choices=config.SPEECH_MODELS)
    parser.add_argument("--language", default=settings["call_language"], choices=config.CALL_LANGUAGES)
    parser.add_argument("--no-mic", action="store_true", help="listen to the speakers only")
    parser.add_argument("--devices", action="store_true", help="list the audio devices, then exit")
    args = parser.parse_args()

    ports = pyaudio.PyAudio()
    if args.devices:
        for kind, names in zip(("Speakers", "Microphones"), audio.devices(ports)):
            print(f"{kind}:", *names, sep="\n  ")
        return

    print(f"Loading the speech model {args.model}...")
    transcriber = Transcriber(args.model, args.language, settings["terms"])
    utterances = queue.SimpleQueue()
    wanted = [(CLIENT, settings["speaker_device"])] + ([] if args.no_mic else [(ME, settings["mic_device"])])
    for speaker, name in wanted:
        try:
            source = Source(ports, speaker, name, utterances.put)
        except AudioError as error:
            print(f"{speaker}: {error}")
            continue
        source.start()
        print(f"{speaker}: listening to {source.device_name}")
    print("Ctrl+C to stop.\n")
    try:
        while True:
            try:
                utterance = utterances.get(timeout=0.5)  # a timeout, so that Ctrl+C is noticed
            except queue.Empty:
                continue
            text = transcriber.transcribe(utterance.audio)
            if text:
                delay = time.monotonic() - utterance.ended
                print(f"{utterance.speaker:>6}  {text}   [{delay:.1f}s after they stopped]")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
