"""Audio capture: the two sides of a call, each cut into utterances at its pauses.

The client is whatever the speakers play, captured with WASAPI loopback. The
presenter is the microphone. Both arrive as 16 kHz mono, which is what the
speech recogniser takes.
"""

import collections
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np
import pyaudiowpatch as pyaudio

RATE = 16000
FRAME = 480               # samples in one 30 ms analysis frame
FRAME_SECONDS = FRAME / RATE
BACKGROUND = 166          # frames, about five seconds, that the background level is judged from
PRE_ROLL = 10             # frames kept from before speech is detected, so its first sound is not clipped
MIN_RMS = 0.004           # quieter than this is never speech
SPEECH_RATIO = 3.0        # speech is at least this many times louder than the background
MIN_SPEECH = 0.25         # seconds of voiced sound an utterance needs to be kept
STALL = 0.25              # loopback delivers nothing while nothing plays; after this long, count it as silence
LOOPBACK_SUFFIX = " [Loopback]"

CLIENT, ME = "Client", "Me"


class AudioError(Exception):
    pass


@dataclass
class Utterance:
    speaker: str
    audio: np.ndarray   # float32, 16 kHz mono
    started: float      # time.monotonic() when the speech began
    ended: float        # and when it stopped


class Resampler:
    """Converts a mono stream to 16 kHz, a chunk at a time."""

    def __init__(self, rate):
        self.step = rate / RATE
        self.tail = np.zeros(0, np.float32)
        self.position = 0.0  # where the next output sample falls, in input samples from the start of tail

    def __call__(self, samples):
        samples = np.concatenate((self.tail, samples))
        whole = int(self.step)
        if whole == self.step:  # 48 kHz and 32 kHz: average each group of samples
            count = len(samples) // whole
            self.tail = samples[count * whole:]
            return samples[:count * whole].reshape(count, whole).mean(axis=1)
        count = max(int((len(samples) - 1 - self.position) // self.step) + 1, 0)
        at = self.position + self.step * np.arange(count)
        following = self.position + self.step * count
        cut = min(int(following), len(samples))
        self.tail, self.position = samples[cut:], following - cut
        return np.interp(at, np.arange(len(samples)), samples).astype(np.float32)


class Segmenter:
    """Cuts a 16 kHz stream into utterances: speech that ends in `end_silence` seconds of quiet."""

    def __init__(self, speaker, end_silence=0.6, max_length=25.0):
        self.speaker = speaker
        self.end_silence = end_silence
        self.max_length = max_length
        self.pending = np.zeros(0, np.float32)  # samples short of a whole frame
        self.before = collections.deque(maxlen=PRE_ROLL)
        self.recent = collections.deque(maxlen=5)
        self.levels = collections.deque(maxlen=BACKGROUND)
        self.speech = None                      # frames of the utterance being spoken
        self.speaking_since = None

    def feed(self, samples):
        """Take the next samples; return the utterances they complete."""
        samples = np.concatenate((self.pending, samples))
        count = len(samples) // FRAME
        self.pending = samples[count * FRAME:]
        finished = []
        for frame in samples[:count * FRAME].reshape(count, FRAME):
            utterance = self.frame(frame)
            if utterance:
                finished.append(utterance)
        return finished

    def frame(self, frame):
        rms = float(np.sqrt(np.mean(frame * frame)))
        # The background is the quietest tenth of the last few seconds. Unlike a running average,
        # that does not creep up during a long sentence and cut off its quieter last words.
        self.levels.append(rms)
        voiced = rms > max(MIN_RMS, float(np.percentile(self.levels, 10)) * SPEECH_RATIO)
        self.recent.append(voiced)

        if self.speech is None:
            self.before.append(frame)
            if sum(self.recent) >= 3:
                self.speech = list(self.before)
                self.voiced, self.quiet = sum(self.recent), 0
                self.speaking_since = time.monotonic() - len(self.speech) * FRAME_SECONDS
                self.before.clear()
            return None

        self.speech.append(frame)
        if voiced:
            self.voiced, self.quiet = self.voiced + 1, 0
        else:
            self.quiet += 1
        ended = self.quiet * FRAME_SECONDS >= self.end_silence
        if not ended and len(self.speech) * FRAME_SECONDS < self.max_length:
            return None

        keep = len(self.speech) - max(self.quiet - 5, 0)  # drop the closing silence, bar a short tail
        utterance = Utterance(self.speaker, np.concatenate(self.speech[:keep]), self.speaking_since,
                              time.monotonic() - self.quiet * FRAME_SECONDS)
        long_enough = self.voiced * FRAME_SECONDS >= MIN_SPEECH
        self.speech = self.speaking_since = None
        self.recent.clear()
        return utterance if long_enough else None


def devices(audio):
    """The names of (speakers, microphones) that can be captured."""
    wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
    speakers, microphones = [], []
    for index in range(audio.get_device_count()):
        device = audio.get_device_info_by_index(index)
        if device["hostApi"] != wasapi or not device["maxInputChannels"]:
            continue
        if device.get("isLoopbackDevice"):
            speakers.append(device["name"].removesuffix(LOOPBACK_SUFFIX))
        else:
            microphones.append(device["name"])
    return speakers, microphones


def find_device(audio, speaker, name):
    """The device to capture `speaker` from; `name` is empty for the Windows default."""
    try:
        if not name:
            if speaker == CLIENT:
                return audio.get_default_wasapi_loopback()
            default = audio.get_host_api_info_by_type(pyaudio.paWASAPI)["defaultInputDevice"]
            if default < 0:
                raise AudioError("Windows has no default microphone.")
            return audio.get_device_info_by_index(default)
        wanted = name + LOOPBACK_SUFFIX if speaker == CLIENT else name
        wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        for index in range(audio.get_device_count()):
            device = audio.get_device_info_by_index(index)
            if device["hostApi"] == wasapi and device["name"] == wanted and device["maxInputChannels"]:
                return device
    except (OSError, LookupError) as error:
        raise AudioError(f"No {'speakers' if speaker == CLIENT else 'microphone'} to capture: {error}") from error
    raise AudioError(f"Audio device not found: {name}")


class Source(threading.Thread):
    """Captures one device and reports its utterances and its sound level.

    `on_utterance(utterance)` and `on_level(speaker, rms)` are called on this thread.
    """

    def __init__(self, audio, speaker, name, on_utterance=None, on_level=None, end_silence=0.6):
        super().__init__(daemon=True)
        self.speaker = speaker
        self.on_utterance, self.on_level = on_utterance, on_level
        self.segmenter = Segmenter(speaker, end_silence)
        self.chunks = queue.SimpleQueue()
        self.stopping = False
        device = find_device(audio, speaker, name)
        self.device_name = device["name"].removesuffix(LOOPBACK_SUFFIX)
        self.channels = device["maxInputChannels"]
        rate = int(device["defaultSampleRate"])
        self.resample = Resampler(rate)
        try:
            self.stream = audio.open(format=pyaudio.paInt16, channels=self.channels, rate=rate, input=True,
                                     input_device_index=device["index"], frames_per_buffer=rate // 20,
                                     stream_callback=self.receive)
        except OSError as error:
            raise AudioError(f"Cannot open {self.device_name}: {error}") from error

    @property
    def speaking_since(self):
        return self.segmenter.speaking_since

    def receive(self, data, _frames, _time, _status):
        self.chunks.put(data)
        return None, pyaudio.paContinue

    def run(self):
        reported = 0.0
        while not self.stopping:
            try:
                data = self.chunks.get(timeout=STALL)
            except queue.Empty:
                samples = np.zeros(int(STALL * RATE), np.float32)
            else:
                samples = np.frombuffer(data, np.int16).astype(np.float32) / 32768
                samples = self.resample(samples.reshape(-1, self.channels).mean(axis=1))
            now = time.monotonic()
            if self.on_level and now - reported >= 0.08:
                reported = now
                self.on_level(self.speaker, float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0)
            for utterance in self.segmenter.feed(samples):
                if self.on_utterance and not self.stopping:
                    self.on_utterance(utterance)

    def stop(self):
        self.stopping = True
        try:
            self.stream.stop_stream()
            self.stream.close()
        except OSError:
            pass
