"""Microphone recording with silence-based stop detection."""

from __future__ import annotations

import io
import struct
import wave
from typing import Callable, List, Optional, Tuple


def _rms(data: bytes) -> float:
    """Compute RMS energy of 16-bit PCM audio bytes."""
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", data[: count * 2])
    return (sum(s * s for s in samples) / count) ** 0.5


def _to_wav(chunks: List[bytes], sample_rate: int, channels: int) -> bytes:
    """Pack raw PCM chunks into a WAV byte buffer."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for chunk in chunks:
            wf.writeframes(chunk)
    return buf.getvalue()


def _probe_working_config() -> Tuple[Optional[int], int, int]:
    """Find a device + sample rate + channel combo that actually opens.

    Returns (device, sample_rate, channels).  ``device=None`` means the
    PortAudio default.
    """
    import sounddevice as sd  # type: ignore[import-untyped]

    # Build candidate list: (device_id_or_None, rate, channels)
    candidates: list[Tuple[Optional[int], int, int]] = []

    # 1. PortAudio default (device=None) at common rates
    for rate in [44100, 48000, 16000]:
        for ch in [1, 2]:
            candidates.append((None, rate, ch))

    # 2. Enumerate all input devices across all host APIs
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        native_rate = int(dev["default_samplerate"])
        max_ch = dev["max_input_channels"]
        for ch in [1, min(max_ch, 2)]:
            candidates.append((i, native_rate, ch))

    for device, rate, ch in candidates:
        try:
            with sd.RawInputStream(
                device=device,
                samplerate=rate,
                channels=ch,
                dtype="int16",
                blocksize=1024,
            ) as s:
                s.read(1024)
            return device, rate, ch
        except Exception:
            continue

    raise RuntimeError(
        "No working microphone configuration found. "
        "Check your audio device settings."
    )


class Microphone:
    """Record audio from the default input device.

    Requires the ``sounddevice`` package (``uv sync --extra voice``).

    Auto-probes for a working device / sample-rate / channel combination
    on init.  Pass explicit values to skip probing.
    """

    def __init__(
        self,
        device: Optional[int] = None,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None,
        chunk_duration: float = 0.2,
    ) -> None:
        if sample_rate is not None and channels is not None:
            self.device = device
            self.sample_rate = sample_rate
            self.channels = channels
        else:
            self.device, self.sample_rate, self.channels = _probe_working_config()

        self.chunk_duration = chunk_duration
        self._blocksize = int(self.sample_rate * chunk_duration)

    def _open_stream(self):
        import sounddevice as sd  # type: ignore[import-untyped]

        return sd.RawInputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=self._blocksize,
        )

    def calibrate(self, duration: float = 2.0, multiplier: float = 3.0) -> float:
        """Measure ambient noise and return a silence threshold.

        Args:
            duration: How long to sample ambient noise (seconds).
            multiplier: Threshold = max ambient RMS * multiplier.

        Returns:
            Recommended silence_threshold value.
        """
        chunks_needed = int(duration / self.chunk_duration)
        energies = []
        with self._open_stream() as stream:
            for _ in range(max(chunks_needed, 2)):
                data, _ = stream.read(self._blocksize)
                energies.append(_rms(bytes(data)))

        max_ambient = max(energies) if energies else 300.0
        # Floor at 400 so real ambient noise (fans, keyboards, distant voices)
        # doesn't get mistaken for speech and block silence detection.
        return max(max_ambient * multiplier, 400.0)

    def record_until_silence(
        self,
        silence_threshold: float = 400.0,
        silence_duration: float = 1.5,
        max_duration: float = 15.0,
        min_speech_duration: float = 0.6,
        on_energy: Optional[Callable[[float], None]] = None,
    ) -> bytes:
        """Record from the mic until silence is detected.

        Args:
            silence_threshold: RMS energy below which audio is considered silent.
            silence_duration: Seconds of continuous silence to stop recording.
            max_duration: Absolute max recording time in seconds.
            min_speech_duration: Minimum accumulated speech before we'll
                consider stopping on silence. Prevents short false-positives
                (keyboard clicks, "uh", etc.) from ending the recording early.
            on_energy: Optional callback called each chunk with normalised
                       energy (0.0-1.0) relative to *silence_threshold*.

        Returns:
            WAV-encoded audio bytes.
        """
        chunks: List[bytes] = []
        silent_chunks = 0
        speech_chunks = 0
        max_silent = int(silence_duration / self.chunk_duration)
        max_chunks = int(max_duration / self.chunk_duration)
        min_speech_chunks = max(1, int(min_speech_duration / self.chunk_duration))
        has_speech = False

        with self._open_stream() as stream:
            for _ in range(max_chunks):
                data, _overflowed = stream.read(self._blocksize)
                raw = bytes(data)
                chunks.append(raw)

                energy = _rms(raw)

                if on_energy is not None:
                    norm = max(0.0, min(1.0, energy / (silence_threshold * 4)))
                    on_energy(norm)

                if energy < silence_threshold:
                    silent_chunks += 1
                else:
                    silent_chunks = 0
                    speech_chunks += 1
                    if speech_chunks >= min_speech_chunks:
                        has_speech = True

                # Only stop on silence after enough real speech has accumulated
                if has_speech and silent_chunks >= max_silent:
                    break

        return _to_wav(chunks, self.sample_rate, self.channels)

    def record_fixed(self, duration: float = 5.0) -> bytes:
        """Record for a fixed duration.

        Args:
            duration: Recording length in seconds.

        Returns:
            WAV-encoded audio bytes.
        """
        num_chunks = int(duration / self.chunk_duration)
        chunks: List[bytes] = []

        with self._open_stream() as stream:
            for _ in range(num_chunks):
                data, _overflowed = stream.read(self._blocksize)
                chunks.append(bytes(data))

        return _to_wav(chunks, self.sample_rate, self.channels)


__all__ = ["Microphone"]
