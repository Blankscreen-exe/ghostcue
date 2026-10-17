"""Speech to text, on this machine, with faster-whisper."""

import os

from .audio import RATE

ENGLISH, URDU, MIXED = "en", "ur", "mix"
TURBO = "large-v3-turbo"


class Transcriber:
    """Turns an utterance into text.

    `language` is "en" for a call in English, "ur" for one in Urdu (with English words in it),
    or "mix" for one that moves between the two. On a mixed call each utterance is first
    checked for being plain English, because reading English as Urdu gives nonsense.
    """

    def __init__(self, size="base", language=ENGLISH, terms=""):
        # Download the model through the Windows certificate store, so it also works behind
        # an antivirus or company proxy that re-signs HTTPS.
        import truststore
        truststore.inject_into_ssl()
        import ctranslate2
        from faster_whisper import WhisperModel

        self.language = language
        # The English-only models are the more accurate ones for English; turbo has none.
        name = f"{size}.en" if language == ENGLISH and size != TURBO else size
        # Words the recogniser should expect, so that product names come out spelled right.
        self.terms = terms.strip() or None
        self.model = None
        if ctranslate2.get_cuda_device_count():
            try:
                self.model = self.load(WhisperModel, name, device="cuda", compute_type="float16")
            except (RuntimeError, ValueError):  # a graphics card without the CUDA libraries it needs
                pass
        if self.model is None:
            self.model = self.load(WhisperModel, name, device="cpu", compute_type="int8",
                                   cpu_threads=max(1, min(os.cpu_count() or 4, 8)))

    @staticmethod
    def load(base, name, **options):
        class Model(base):
            """Lends the sound it analysed for the language check to the transcription that follows.

            Analysing an utterance is most of the work of reading it. The library does it once to
            tell the language and again to transcribe, which here would double the wait.
            """
            lend = False
            analysed = None

            def encode(self, features):
                if self.lend and self.analysed is not None:
                    analysed, self.analysed = self.analysed, None
                    return analysed
                self.analysed = super().encode(features)
                return self.analysed

        return Model(name, **options)

    def transcribe(self, audio):
        """The words in 16 kHz mono float32 audio; empty if it holds no speech."""
        language = self.language
        model = self.model
        model.analysed, model.lend = None, False
        if language == MIXED:
            language = URDU
            try:
                if model.detect_language(audio, vad_filter=True)[0] == ENGLISH:
                    language = ENGLISH
                model.lend = True
            except (ValueError, IndexError, RuntimeError):  # nothing in the audio to judge by
                model.analysed = None
        try:
            segments, _info = model.transcribe(
                audio, language=language, beam_size=1, hotwords=self.terms, condition_on_previous_text=False,
                without_timestamps=True, vad_filter=True,
                # One pass, and no more words than the time could hold: when the recogniser loses the
                # thread it otherwise retries and rambles, and a call cannot wait half a minute for that.
                temperature=0.0, max_new_tokens=int(len(audio) / RATE * 12) + 16)
            segments = list(segments)
        finally:
            model.lend = False
        return " ".join(segment.text.strip() for segment in segments if segment.no_speech_prob < 0.7).strip()
