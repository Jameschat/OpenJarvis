"""``jarvis voice`` — voice conversation loop with mic input and TTS output."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import click
from rich.console import Console
from rich.markdown import Markdown

from openjarvis.cli._tool_names import resolve_tool_names
from openjarvis.core.config import JarvisConfig, load_config
from openjarvis.core.types import Message, Role

if TYPE_CHECKING:
    from openjarvis.cli.jarvis_ui import JarvisUI


def _get_tts_backend(config: JarvisConfig):
    """Resolve TTS backend from config/registry."""
    import openjarvis.speech  # noqa: F401 — trigger registration

    from openjarvis.core.registry import TTSRegistry

    backend_key = config.digest.tts_backend

    # Try the configured backend first, then fall back to any available
    for key in [backend_key, "openai_tts", "cartesia", "kokoro"]:
        if TTSRegistry.contains(key):
            try:
                cls = TTSRegistry.get(key)
                instance = cls()
                if instance.health():
                    return instance
            except Exception:
                continue
    return None


def _play_audio_bytes(audio: bytes, fmt: str = "wav", interruptible: bool = False) -> bool:
    """Play audio bytes through the default output device.

    Args:
        audio: Raw audio bytes.
        fmt: Audio format (default "wav").
        interruptible: If True, listens for loud mic input and stops early.

    Returns:
        True if playback completed, False if interrupted.
    """
    # Try sounddevice first (uses the system default output)
    try:
        import io
        import wave

        import sounddevice as sd  # type: ignore[import-untyped]

        if fmt == "wav":
            with wave.open(io.BytesIO(audio), "rb") as wf:
                rate = wf.getframerate()
                channels = wf.getnchannels()
                frames = wf.readframes(wf.getnframes())
            import numpy as np

            samples = np.frombuffer(frames, dtype=np.int16)
            if channels > 1:
                samples = samples.reshape(-1, channels)

            if not interruptible:
                sd.play(samples, samplerate=rate)
                sd.wait()
                return True

            # Interruptible playback — check mic energy periodically
            sd.play(samples, samplerate=rate)
            try:
                from openjarvis.speech.microphone import _rms

                mic_stream = sd.RawInputStream(
                    samplerate=16000, channels=1, dtype="int16", blocksize=4000
                )
                mic_stream.start()
                try:
                    while sd.get_stream().active:
                        data, _ = mic_stream.read(4000)
                        energy = _rms(bytes(data))
                        # If loud voice detected, stop playback
                        if energy > 2000:
                            sd.stop()
                            return False
                except Exception:
                    sd.wait()
                finally:
                    mic_stream.stop()
                    mic_stream.close()
            except Exception:
                sd.wait()
            return True
    except Exception:
        pass

    # Fallback to ffplay
    suffix = f".{fmt}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio)
        tmp.flush()
        tmp_path = tmp.name

    players = ["ffplay -nodisp -autoexit", "aplay", "afplay", "paplay"]
    for player in players:
        cmd_parts = player.split() + [tmp_path]
        try:
            subprocess.run(
                cmd_parts,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    Path(tmp_path).unlink(missing_ok=True)
    return True


def _voice_input(
    mic,
    stt_backend,
    console: Console,
    silence_threshold: float = 300.0,
    ui: Optional["JarvisUI"] = None,
) -> Optional[str]:
    """Record from mic and transcribe. Returns text or None on error."""
    import time

    from openjarvis.cli.jarvis_ui import JarvisState

    if ui:
        ui.set_state(JarvisState.LISTENING)

    console.print("[cyan]Listening...[/cyan] (speak now, pause to stop)")

    energy_cb = None
    if ui:

        def energy_cb(e: float) -> None:
            ui.set_energy(e)

    try:
        audio_bytes = mic.record_until_silence(
            silence_threshold=silence_threshold,
            on_energy=energy_cb,
        )
    except Exception as exc:
        console.print(f"[red]Mic error: {exc}[/red]")
        time.sleep(2)
        if ui:
            ui.set_state(JarvisState.IDLE)
        return None

    if ui:
        ui.set_state(JarvisState.THINKING)

    console.print("[dim]Transcribing...[/dim]")
    try:
        result = stt_backend.transcribe(audio_bytes, format="wav", language="en")
    except Exception as exc:
        console.print(f"[red]Transcription error: {exc}[/red]")
        if ui:
            ui.set_state(JarvisState.IDLE)
        return None

    text = result.text.strip()
    if not text:
        console.print("[dim]No speech detected.[/dim]")
        if ui:
            ui.set_state(JarvisState.IDLE)
        return None

    # Wake word detection — must start with a recognised wake word
    lower = text.lower()
    _WAKE_WORDS = (
        "jarvis", "javas", "jarvas", "jarvus", "jervis", "jarves",
        "travis", "jars", "service", "jarvas,", "djarvis", "charvis",
        "jarvis,", "jarvis.", "jarvis!", "jarviss", "jarves,",
        "hey jarvis", "ok jarvis", "yo jarvis",
    )
    matched_len = 0
    for wake in _WAKE_WORDS:
        if lower.startswith(wake):
            matched_len = len(wake)
            break

    if not matched_len:
        console.print(f"[dim](ignored: {text[:60]})[/dim]")
        if ui:
            ui.set_state(JarvisState.IDLE)
        return None

    # Strip the wake word from the command
    command = text[matched_len:].lstrip(" ,.:!-")
    if not command:
        console.print("[dim]Yes?[/dim]")
        if ui:
            ui.set_state(JarvisState.IDLE)
        return None

    return command


def _try_calendar(text: str, console: Console, ui=None) -> Optional[str]:
    """Try to match text to a calendar command. Returns response or None."""
    try:
        from openjarvis.tools.calendar_tool import CalendarTool
    except ImportError:
        return None

    lower = text.lower().strip()

    # Word-boundary match — substring matching had a sibling bug to the
    # lights/Reddit one (e.g. "scheduling app" tripping "schedule").
    import re
    def _has_term(phrase: str, hay: str) -> bool:
        return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", hay) is not None

    _TRIGGERS = ("schedule", "calendar", "meetings", "appointments",
                 "diary", "agenda")
    if not any(_has_term(t, lower) for t in _TRIGGERS):
        return None

    # Build/make/create intents are NOT calendar queries — they're project
    # work. "Let's build a scheduling app" / "make me a calendar tool" /
    # "spin up a team to add appointments" should pass through to the LLM
    # / team-task flow, not return today's events.
    _BUILD_VERBS = (
        "build", "make", "create", "develop", "implement", "design",
        "code", "write", "spin up", "kick off", "start a project",
        "start a new project", "let's build", "lets build",
    )
    if any(_has_term(v, lower) for v in _BUILD_VERBS):
        return None

    # Require a query intent (verb or time word) so we don't grab phrases
    # like "the calendar is on the wall". "Login/authenticate" is handled
    # explicitly below as its own form of intent.
    _QUERY_VERBS = (
        "show", "what", "what's", "whats", "any", "do i have", "list",
        "tell me", "check", "look up", "got any", "anything on",
        "read out", "read me", "open", "view",
    )
    _TIME_WORDS = (
        "today", "tomorrow", "tonight", "this week", "next week",
        "this morning", "this afternoon", "this evening",
    )
    has_query = (
        any(_has_term(v, lower) for v in _QUERY_VERBS)
        or any(_has_term(t, lower) for t in _TIME_WORDS)
        or "login" in lower or "authenticate" in lower or "sign in" in lower
    )
    if not has_query:
        return None

    if "login" in lower or "authenticate" in lower or "sign in" in lower:
        # Login is special — need to tell user the code before blocking
        try:
            import msal
            import webbrowser
            import subprocess as sp

            from openjarvis.tools.calendar_tool import _get_msal_app, _save_cache, _SCOPES

            app, cache = _get_msal_app()
            flow = app.initiate_device_flow(scopes=_SCOPES)
            code = flow.get("user_code", "")
            url = flow.get("verification_uri", "https://microsoft.com/devicelogin")

            if not code:
                return "Failed to start login."

            # Copy code to clipboard and open browser
            try:
                sp.run(["powershell.exe", "-Command", f"Set-Clipboard -Value '{code}'"], timeout=5, capture_output=True)
            except Exception:
                pass
            webbrowser.open(url)

            msg = f"I've opened your browser. Enter the code {code}. It's been copied to your clipboard."
            console.print(f"[green]Calendar login code: {code}[/green]")
            console.print(f"[cyan]Browser opened to {url}[/cyan]")

            if ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)

            # Return the spoken message immediately (non-blocking part)
            # Then wait for auth in background
            import threading

            def _wait_for_auth():
                result = app.acquire_token_by_device_flow(flow)
                if "access_token" in result:
                    _save_cache(cache)
                    console.print("[green]Calendar login successful![/green]")
                else:
                    console.print("[red]Calendar login failed or timed out.[/red]")

            threading.Thread(target=_wait_for_auth, daemon=True).start()
            return msg

        except Exception as exc:
            return f"Login error: {exc}"

    if "tomorrow" in lower:
        action = "tomorrow"
    elif "week" in lower:
        action = "week"
    else:
        action = "today"

    tool = CalendarTool()
    result = tool.execute(action=action)
    console.print(f"[green]Calendar: {result.content}[/green]")

    if ui:
        from openjarvis.cli.jarvis_ui import JarvisState
        ui.set_state(JarvisState.IDLE)

    return result.content if result.success else result.content


def _speak_response(
    text: str,
    tts_backend,
    config: JarvisConfig,
    ui: Optional["JarvisUI"] = None,
    console: Optional[Console] = None,
) -> None:
    """Synthesize and play the assistant response (interruptible by voice)."""
    from openjarvis.cli.jarvis_ui import JarvisState

    try:
        if ui:
            ui.set_state(JarvisState.SPEAKING)

        voice = config.digest.voice_id
        if not voice:
            voices = tts_backend.available_voices()
            voice = voices[0] if voices else "af_heart"
        tts_result = tts_backend.synthesize(
            text,
            voice_id=voice,
            speed=config.digest.voice_speed,
            output_format="wav",
        )
        completed = _play_audio_bytes(tts_result.audio, tts_result.format, interruptible=True)
        if not completed and console:
            console.print("[yellow]Interrupted.[/yellow]")
    except Exception:
        pass  # TTS failure is non-fatal, text is already printed
    finally:
        if ui:
            ui.set_state(JarvisState.IDLE)


def _try_time(text: str, console: Console, ui=None) -> Optional[str]:
    """Fast-path: answer time/date questions directly from the system clock."""
    import datetime

    lower = text.lower().strip().rstrip("?.!")

    _TIME_TRIGGERS = (
        "what time is it", "what's the time", "whats the time",
        "what is the time", "tell me the time", "give me the time",
        "current time", "time please",
        "do you know the time", "do you have the time",
        "got the time", "time check",
    )
    # Note: "the time" was removed from triggers — too many false-positives
    # in normal speech ("by the time we finish", "i had the time of my life").
    # The explicit "what's the time" / "tell me the time" variants above
    # cover the legitimate query phrasings.
    _DATE_TRIGGERS = (
        "what's the date", "whats the date", "what is the date",
        "what's today's date", "whats todays date", "what is today's date",
        "today's date", "todays date", "current date",
        "what day is it", "what day is it today", "what's the day",
        "what day of the week", "day of the week",
        "what month is it", "what year is it",
    )
    _COMBINED_TRIGGERS = (
        "what's the time and date", "what is the time and date",
        "time and date", "date and time",
    )

    # Word-boundary match — substring matching here would let "the time"
    # trip on phrases like "by the time we finish" or "i had the time of
    # my life". Triggers are mostly already specific multi-word phrases
    # but normalising defensively.
    import re
    def _has_term(phrase: str, hay: str) -> bool:
        return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", hay) is not None

    is_combined = any(_has_term(t, lower) for t in _COMBINED_TRIGGERS)
    is_time = not is_combined and any(_has_term(t, lower) for t in _TIME_TRIGGERS)
    is_date = not is_combined and any(_has_term(t, lower) for t in _DATE_TRIGGERS)

    if not (is_time or is_date or is_combined):
        return None

    now = datetime.datetime.now()

    # Humanise the hour/minute for natural speech
    hour_24 = now.hour
    minute = now.minute
    # 12h clock
    if hour_24 == 0:
        h12 = 12
        period = "in the morning"
    elif hour_24 < 12:
        h12 = hour_24
        period = "in the morning"
    elif hour_24 == 12:
        h12 = 12
        period = "in the afternoon"
    elif hour_24 < 18:
        h12 = hour_24 - 12
        period = "in the afternoon"
    else:
        h12 = hour_24 - 12
        period = "in the evening"

    if minute == 0:
        time_phrase = f"{h12} o'clock {period}"
    elif minute == 15:
        time_phrase = f"quarter past {h12} {period}"
    elif minute == 30:
        time_phrase = f"half past {h12} {period}"
    elif minute == 45:
        next_h = 1 if h12 == 12 else h12 + 1
        time_phrase = f"quarter to {next_h} {period}"
    else:
        time_phrase = f"{h12}:{minute:02d} {period}"

    # Date phrase: "Monday the 14th of April 2026"
    day_name = now.strftime("%A")
    day_num = now.day
    if 10 <= day_num % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_num % 10, "th")
    month_name = now.strftime("%B")
    year = now.year
    date_phrase = f"{day_name} the {day_num}{suffix} of {month_name} {year}"

    if is_combined:
        response = f"It's {time_phrase}, sir. Today is {date_phrase}."
    elif is_time:
        response = f"It's {time_phrase}, sir."
    else:  # date
        response = f"It's {date_phrase}, sir."

    console.print(f"[green]Clock: {response}[/green]")
    if ui:
        from openjarvis.cli.jarvis_ui import JarvisState
        ui.set_state(JarvisState.IDLE)
    return response


def _try_macro(text: str, console: Console, ui=None) -> Optional[str]:
    """Try to match text to a macro command or profile switch. Returns response or None."""
    try:
        from openjarvis.tools.macro import (
            GAME_PROFILES,
            VK_MAP,
            _execute_step,
            _run_macro,
            get_active_macros,
            set_active_profile,
            _active_profile,
        )
    except ImportError:
        return None

    lower = text.lower().strip()

    # --- Profile activation: "activate rust", "switch to rust", "rust mode" ---
    for prefix in ("activate ", "switch to ", "load ", "enable "):
        if lower.startswith(prefix):
            profile = lower[len(prefix):].strip().rstrip(" mode profile")
            if set_active_profile(profile):
                macros = get_active_macros()
                console.print(f"[green]Profile activated: {profile} ({len(macros)} macros)[/green]")
                if ui:
                    from openjarvis.cli.jarvis_ui import JarvisState
                    ui.set_state(JarvisState.IDLE)
                return f"{profile.title()} macro profile activated, sir. {len(macros)} commands ready."
            break

    if lower.endswith(" mode") or lower.endswith(" profile"):
        profile = lower.rsplit(" ", 1)[0].strip()
        if set_active_profile(profile):
            macros = get_active_macros()
            console.print(f"[green]Profile activated: {profile} ({len(macros)} macros)[/green]")
            if ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return f"{profile.title()} macro profile activated, sir. {len(macros)} commands ready."

    # --- Get active macros ---
    macros = get_active_macros()

    # Strip common prefixes the user might say
    cmd = lower
    for prefix in ("run macro ", "macro ", "execute ", "deploy ", "use ", "press key ", "press ", "hit ", "switch to ", "equip ", "grab ", "pull out ", "get "):
        if cmd.startswith(prefix):
            cmd = cmd[len(prefix):]
            break

    # Direct macro name match
    if cmd in macros:
        _run_macro(macros[cmd])
        console.print(f"[green]Macro: {cmd}[/green]")
        if ui:
            from openjarvis.cli.jarvis_ui import JarvisState
            ui.set_state(JarvisState.IDLE)
        return f"Done, {cmd}."

    # Single key match (e.g. "5", "f1", "r")
    if cmd in VK_MAP:
        _execute_step({"key": cmd, "hold": 0.05})
        console.print(f"[green]Key: {cmd}[/green]")
        if ui:
            from openjarvis.cli.jarvis_ui import JarvisState
            ui.set_state(JarvisState.IDLE)
        return f"Pressed {cmd}."

    # Fuzzy match — check if any macro name appears as whole words in the text
    import re

    for macro_name in sorted(macros.keys(), key=len, reverse=True):
        # Only match if macro name appears as complete words (not inside other words)
        pattern = r"(?:^|\s)" + re.escape(macro_name) + r"(?:\s|$)"
        if re.search(pattern, cmd):
            _run_macro(macros[macro_name])
            console.print(f"[green]Macro: {macro_name}[/green]")
            if ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return f"Done, {macro_name}."

    return None


def _try_lights(text: str, console: Console, ui=None) -> Optional[str]:
    """Try to match text to a Hue light command. Returns response or None."""
    try:
        from openjarvis.tools.hue_lights import HueLightsTool, COLOUR_PRESETS
    except ImportError:
        return None

    lower = text.lower().strip()

    # Must contain a light-related keyword
    _LIGHT_TRIGGERS = (
        "light", "lights", "lamp", "lamps", "hue",
        "bedroom", "lounge", "kitchen", "hallway", "desk",
        "dining", "play room", "landing", "upstairs", "guest",
    )
    _ACTION_TRIGGERS = ("turn on", "turn off", "switch on", "switch off", "dim", "brighten", "set")
    _COLOUR_TRIGGERS = tuple(COLOUR_PRESETS.keys())

    # Don't intercept Sonos commands
    if "sonos" in lower or "speaker" in lower or "speakers" in lower:
        return None

    # Word-boundary match — substring matching had a great moment where
    # "Reddit" tripped the colour "red" trigger and set all the lights
    # red mid-LLM-prompt. We tokenise on word boundaries and match
    # against full words / multi-word phrases instead.
    import re
    def _has_term(phrase: str, hay: str) -> bool:
        return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", hay) is not None

    has_light_word = any(_has_term(w, lower) for w in _LIGHT_TRIGGERS)
    has_action_word = any(_has_term(w, lower) for w in _ACTION_TRIGGERS)
    has_colour_word = any(_has_term(w, lower) for w in _COLOUR_TRIGGERS)

    # A bare action verb ("set", "dim") with no light or colour word is
    # not a lights command — could be "set the table", "dim view", etc.
    # Require at least one light-domain signal alongside the action.
    if not (has_light_word or has_colour_word):
        return None

    # Parse action
    action = "set"
    if "turn off" in lower or "switch off" in lower or "lights off" in lower or "off" in lower.split()[-2:]:
        action = "off"
    elif "turn on" in lower or "switch on" in lower or "lights on" in lower or "on" in lower.split()[-2:]:
        action = "on"

    # Parse colour — word-boundary match (so "Reddit" doesn't pick "red")
    colour = ""
    for c in sorted(COLOUR_PRESETS.keys(), key=len, reverse=True):
        if _has_term(c, lower):
            colour = c
            action = "set"
            break

    # Parse brightness
    brightness = None
    import re
    bri_match = re.search(r"(\d+)\s*%|(\d+)\s*percent|brightness\s*(\d+)", lower)
    if bri_match:
        brightness = int(bri_match.group(1) or bri_match.group(2) or bri_match.group(3))
        action = "set"

    if "dim" in lower and brightness is None:
        brightness = 20
        action = "set"
    elif "bright" in lower and brightness is None:
        brightness = 100
        action = "set"

    # Parse target (room/light name)
    target = "all"
    _ROOMS = [
        "desk", "bedroom", "lounge", "kitchen", "hallway", "dining room",
        "dining", "play room", "landing", "upstairs", "guest room",
        "living room", "lounge lamp", "dinner area", "amelias room",
    ]
    for room in sorted(_ROOMS, key=len, reverse=True):
        if room in lower:
            target = room
            break

    # Also check for specific light names
    _LIGHTS = [
        "hue play 1", "hue play 2", "lightstrip", "james bedside",
        "jennifer bed", "guest room light", "play area lamp",
        "dining room lamp", "colour ceiling",
    ]
    for light in sorted(_LIGHTS, key=len, reverse=True):
        if light in lower:
            target = light
            break

    tool = HueLightsTool()
    params = {"action": action, "target": target}
    if colour:
        params["colour"] = colour
    if brightness is not None:
        params["brightness"] = brightness

    result = tool.execute(**params)
    console.print(f"[green]Lights: {result.content}[/green]")

    if ui:
        from openjarvis.cli.jarvis_ui import JarvisState
        ui.set_state(JarvisState.IDLE)

    return result.content if result.success else None


# Action vocabulary — mapped to SonosTool action names.  Ordered by priority
# (longer phrases win first via the length-sorted lookup loop).
_SONOS_ACTION_MAP: Dict[str, str] = {
    # ------------------------------------------------------------------
    # Play / resume
    # ------------------------------------------------------------------
    "play": "play", "resume": "play", "unpause": "play", "start": "play",
    "put on music": "play", "start music": "play",

    # ------------------------------------------------------------------
    # Pause — explicit + every "make it shut up" synonym
    # ------------------------------------------------------------------
    "pause": "pause",
    # Common mishearings of "pause"
    "pauls": "pause", "pulls": "pause", "paws": "pause",
    "paul's": "pause", "pose": "pause", "pours": "pause",
    # Natural "make it stop" synonyms — map to pause (user intent = be quiet)
    "cancel": "pause", "abort": "pause",
    "turn off": "pause", "turn it off": "pause", "switch off": "pause",
    "shut up": "pause", "shut it": "pause", "shush": "pause", "hush": "pause",
    "silence": "pause", "be quiet": "pause", "quiet down": "pause",
    "mute": "pause", "mute it": "pause",
    "stop the music": "pause", "stop playing": "pause",
    "stop playback": "pause", "stop the song": "pause",
    "kill the music": "pause", "end playback": "pause",
    "cut the music": "pause", "knock it off": "pause",
    "enough": "pause", "no more music": "pause",
    "hold on": "pause", "hold it": "pause",
    "halt music": "pause",

    # ------------------------------------------------------------------
    # Stop (explicit hard stop — clears queue)
    # ------------------------------------------------------------------
    "stop": "stop", "halt": "stop",

    # ------------------------------------------------------------------
    # Skip / next
    # ------------------------------------------------------------------
    "skip": "next", "next": "next", "next song": "next", "next track": "next",
    "skip track": "next", "skip song": "next", "next please": "next",
    "change song": "next", "change track": "next", "move on": "next",
    "forward": "next",

    # ------------------------------------------------------------------
    # Previous
    # ------------------------------------------------------------------
    "previous": "previous", "back": "previous", "last track": "previous",
    "go back": "previous", "previous song": "previous",
    "previous track": "previous", "last song": "previous",
    "rewind": "previous",

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------
    "volume up": "volume_up", "louder": "volume_up", "turn up": "volume_up",
    "raise volume": "volume_up", "crank it": "volume_up",
    "more volume": "volume_up", "pump it up": "volume_up",
    "volume down": "volume_down", "quieter": "volume_down", "turn down": "volume_down",
    "lower volume": "volume_down", "less volume": "volume_down",
    "bring it down": "volume_down",

    # ------------------------------------------------------------------
    # Now playing (apostrophe variants for Whisper mishearings)
    # ------------------------------------------------------------------
    "what's playing": "now_playing", "whats playing": "now_playing",
    "now playing": "now_playing", "what song": "now_playing",
    "current song": "now_playing", "playing on": "now_playing",
    "what is playing": "now_playing", "what's this": "now_playing",
    "whats this": "now_playing", "what is this song": "now_playing",
    "who sings this": "now_playing", "who is this": "now_playing",
    "name this song": "now_playing", "what's on": "now_playing",
    "whats on": "now_playing",

    # ------------------------------------------------------------------
    # Favourites / list
    # ------------------------------------------------------------------
    "favourites": "favourites", "favorites": "favourites",
    "list": "list",
}


# Actions that should trigger the Sonos fast-path even when the word "sonos"
# is NOT in the phrase.  Used for natural utterances like "pause the music",
# "turn off the music", "skip".  Playback-affecting actions only — we don't
# hijack ambiguous "list" or "favourites" queries.
_SONOS_IMPLICIT_ACTIONS: set = {
    "pause", "stop", "next", "previous",
    "volume_up", "volume_down", "now_playing",
}


def _match_sonos_action(lower: str) -> Optional[str]:
    """Find the highest-priority Sonos action matching the text, or None.

    Word-boundary match — substring matching had a sibling bug to the
    lights/Reddit one (e.g. 'stopover' tripping 'stop', 'background'
    tripping 'back', 'display' tripping 'play').
    """
    import re
    for phrase, act in sorted(
        _SONOS_ACTION_MAP.items(), key=lambda x: len(x[0]), reverse=True
    ):
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", lower):
            return act
    return None


def _try_sonos(text: str, console: Console, ui=None) -> Optional[str]:
    """Try to match text to a Sonos command. Returns response or None.

    Supports both explicit phrasings ("pause sonos", "play bedroom sonos")
    and natural utterances without the word "sonos" ("pause the music",
    "turn off the music", "skip", "what's playing").  Implicit matches
    require an action word from ``_SONOS_IMPLICIT_ACTIONS``.
    """
    try:
        from openjarvis.tools.sonos import SonosTool
    except ImportError:
        return None

    import re

    lower = text.lower().strip()

    # --- Set-default commands (match BEFORE regular sonos parsing) ---
    # Supported phrasings:
    #   "set bedroom as default sonos"
    #   "set bedroom sonos as default"
    #   "set bedroom sonos as my default"
    #   "make bedroom the default sonos"
    #   "default sonos bedroom"
    #   "use bedroom as default sonos"
    if "sonos" in lower and "default" in lower:
        _ROOMS_SET = ["dining room", "dining", "bedroom", "lounge", "kitchen", "living room"]
        room_found = None
        for room in sorted(_ROOMS_SET, key=len, reverse=True):
            if room in lower:
                room_found = room
                break
        if room_found:
            tool = SonosTool()
            result = tool.execute(action="set_default", speaker=room_found)
            colour = "green" if result.success else "yellow"
            console.print(f"[{colour}]Sonos: {result.content}[/{colour}]")
            if ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            if result.success:
                return f"Very good, sir. {result.content}"
            return f"Sonos issue, sir: {result.content}"

    # --- Implicit Spotify trigger: "play <song> by <artist>" (no 'sonos' needed) ---
    # If the user says "play <something> by <something>" we treat it as a Spotify
    # search routed to the default Sonos speaker.
    if "sonos" not in lower:
        by_match = re.match(r"^play\s+(.+?)\s+by\s+(.+?)\s*$", lower)
        if by_match:
            song = by_match.group(1).strip()
            artist = by_match.group(2).strip()
            query = f"{song} {artist}"
            tool = SonosTool()
            result = tool.execute(action="play_favourite", value=query)
            colour = "green" if result.success else "yellow"
            console.print(f"[{colour}]Sonos: {result.content}[/{colour}]")
            if ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            if result.success:
                return result.content
            error_content = result.content or "unknown error"
            if "connection" in error_content.lower() or "forcibly closed" in error_content.lower():
                return (
                    "I couldn't reach the Sonos speaker, sir. The "
                    "connection was reset — try again in a moment."
                )
            return f"Sonos issue, sir: {error_content}"

        # --- Implicit playback-control trigger ---
        # Catch "pause the music", "turn off the music", "skip", "what's playing"
        # without requiring the word "sonos".  We only do this when the text
        # contains a music-related noun (music, song, track, volume, playing)
        # or an unambiguous control word.
        implicit_action = _match_sonos_action(lower)
        if implicit_action in _SONOS_IMPLICIT_ACTIONS:
            _MUSIC_CONTEXT = (
                "music", "song", "songs", "track", "tracks", "volume",
                "playing", "tune", "tunes", "playlist", "playlists",
                "audio", "speaker", "speakers",
            )
            # Word-boundary match — 'musical', 'displaying', 'racetrack'
            # would otherwise count as music context.
            has_context = any(
                re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", lower)
                for w in _MUSIC_CONTEXT
            )

            # Single unambiguous words/phrases — matched EXACTLY so that
            # "cancel my appointment" doesn't false-positive as "cancel Sonos".
            _UNAMBIGUOUS_BARE = {
                "pause", "unpause", "resume", "stop", "halt",
                "cancel", "abort",
                "skip", "next", "previous", "forward", "rewind",
                "shut up", "shush", "hush", "silence",
                "volume up", "volume down", "louder", "quieter",
                "mute",
            }
            is_unambiguous = lower in _UNAMBIGUOUS_BARE

            if has_context or is_unambiguous:
                # Route to the Sonos default speaker
                tool = SonosTool()
                result = tool.execute(action=implicit_action)
                colour = "green" if result.success else "yellow"
                console.print(f"[{colour}]Sonos: {result.content}[/{colour}]")
                if ui:
                    from openjarvis.cli.jarvis_ui import JarvisState
                    ui.set_state(JarvisState.IDLE)
                if result.success:
                    return result.content
                error_content = result.content or "unknown error"
                if "connection" in error_content.lower() or "forcibly closed" in error_content.lower():
                    return (
                        "I couldn't reach the Sonos speaker, sir. The "
                        "connection was reset — try again in a moment."
                    )
                return f"Sonos issue, sir: {error_content}"

        return None

    # From here on, "sonos" IS in the phrase.
    action = _match_sonos_action(lower)

    if not action:
        action = "list"

    # Parse speaker/room name — check what's between the action and "sonos"
    speaker = ""
    _ROOMS = ["dining room", "dining", "bedroom", "lounge", "kitchen", "living room"]
    for room in sorted(_ROOMS, key=len, reverse=True):
        if room in lower:
            speaker = room
            break

    # Parse volume value
    import re
    value = ""
    vol_match = re.search(r"volume\s+(?:to\s+)?(\d+)", lower)
    if vol_match:
        value = vol_match.group(1)
        action = "set_volume"

    # --- Remove the speaker room name + connecting prepositions from the text
    #     used for favourite/search extraction.  This prevents the regex from
    #     swallowing "on bedroom" into the search query.
    search_text = lower
    if speaker:
        # Strip "on/to/in/at/for <room>" and bare "<room>" occurrences
        for prep in ("on ", "to ", "in ", "at ", "for ", "onto "):
            search_text = re.sub(
                rf"\b{re.escape(prep + speaker)}\b",
                " ",
                search_text,
            )
        search_text = re.sub(
            rf"\b{re.escape(speaker)}\b",
            " ",
            search_text,
        )
        # Collapse doubled spaces
        search_text = re.sub(r"\s+", " ", search_text).strip()

    # --- Parse favourite / content name ---
    # Pattern 1 (explicit): "play favourite <name> on sonos"
    fav_match = re.search(
        r"play\s+(?:favourite|favorite)\s+(.+?)(?:\s+(?:on|via|through|at|using)\s+sonos|\s*$)",
        search_text,
    )
    if fav_match:
        value = fav_match.group(1).strip()
        action = "play_favourite"
    else:
        # Pattern 2 (natural): "play <name> on/via/through sonos"
        #   e.g. "play spotify on sonos", "play jazz playlist via sonos"
        #   where <name> is not a known room/speaker.
        nat_match = re.search(
            r"play\s+(.+?)\s+(?:on|via|through|at|using)\s+sonos",
            search_text,
        )
        if nat_match:
            candidate = nat_match.group(1).strip()
            for filler in ("some ", "the ", "my ", "a "):
                if candidate.startswith(filler):
                    candidate = candidate[len(filler):]
            if candidate and candidate not in {r.lower() for r in _ROOMS}:
                value = candidate
                action = "play_favourite"

        # Pattern 3 (reverse order): "sonos play <name>"
        if action in (None, "play", "list"):
            rev_match = re.search(
                r"sonos\s+play\s+(.+?)\s*$",
                search_text,
            )
            if rev_match:
                candidate = rev_match.group(1).strip()
                for filler in ("some ", "the ", "my ", "a "):
                    if candidate.startswith(filler):
                        candidate = candidate[len(filler):]
                if candidate and candidate not in {r.lower() for r in _ROOMS}:
                    value = candidate
                    action = "play_favourite"

        # Pattern 4 (just "play <name> sonos"): catches simpler phrasings
        # e.g. "play bohemian rhapsody by queen sonos" (after room stripping)
        if action in (None, "play", "list"):
            simple_match = re.search(
                r"play\s+(.+?)\s+sonos\s*$",
                search_text,
            )
            if simple_match:
                candidate = simple_match.group(1).strip()
                for filler in ("some ", "the ", "my ", "a "):
                    if candidate.startswith(filler):
                        candidate = candidate[len(filler):]
                if candidate and candidate not in {r.lower() for r in _ROOMS}:
                    value = candidate
                    action = "play_favourite"

    tool = SonosTool()
    params = {"action": action}
    if speaker:
        params["speaker"] = speaker
    if value:
        params["value"] = value

    result = tool.execute(**params)
    colour = "green" if result.success else "yellow"
    console.print(f"[{colour}]Sonos: {result.content}[/{colour}]")

    if ui:
        from openjarvis.cli.jarvis_ui import JarvisState
        ui.set_state(JarvisState.IDLE)

    # Always return a string — we've already matched the "sonos" keyword
    # so the request is clearly for the Sonos tool.  Returning None would
    # fall through to the LLM which would invent a wrong answer like
    # "I cannot control Sonos devices".
    if result.success:
        return result.content
    # Turn low-level errors into a friendly spoken message
    error_content = result.content or "unknown error"
    if "connection" in error_content.lower() or "forcibly closed" in error_content.lower():
        return (
            "I couldn't reach the Sonos speaker, sir. The connection was "
            "reset. The speaker may be offline or its IP may have changed — "
            "try again in a moment."
        )
    return f"Sonos issue, sir: {error_content}"


_CHAT_OPEN_PATTERNS = (
    r"\bopen (?:the |my |our )?(?:chat|chat history|conversation|messages|chat widget)\b",
    r"\bshow (?:me )?(?:the |my |our )?(?:chat|chat history|conversation|messages)\b",
    r"\bbring up (?:the |my |our )?(?:chat|chat history|conversation|messages)\b",
    r"\b(?:i want to|let me|can i) (?:see|read|view) (?:the |my |our )?(?:chat|chat history|conversation)\b",
)
_CHAT_CLOSE_PATTERNS = (
    r"\bclose (?:the |my |our )?(?:chat|chat history|conversation|messages|chat widget)\b",
    r"\bhide (?:the |my |our )?(?:chat|chat history|conversation|messages)\b",
    r"\bdismiss (?:the |my |our )?(?:chat|chat history|conversation)\b",
)
_LOG_OPEN_PATTERNS = (
    r"\bopen (?:the )?(?:activity (?:log|feed)|log|event log|system log)\b",
    r"\bshow (?:me )?(?:the )?(?:activity (?:log|feed)|log|event log|system log)\b",
    r"\bbring up (?:the )?(?:activity (?:log|feed)|log|event log)\b",
)
_LOG_CLOSE_PATTERNS = (
    r"\bclose (?:the )?(?:activity (?:log|feed)|log|event log|system log)\b",
    r"\bhide (?:the )?(?:activity (?:log|feed)|log|event log|system log)\b",
    r"\bdismiss (?:the )?(?:activity (?:log|feed)|log|event log)\b",
)


def _try_chat_widget(text: str) -> Optional[str]:
    """Voice fast-path for opening / closing the right-edge chat widget
    AND the bottom-left activity log. Emits an SSE toggle event the HUD
    listens for; returns the spoken acknowledgment (or None to fall
    through to other fast-paths). Kept under the name _try_chat_widget
    for back-compat with callers wired in 2026-04-28; despite the name
    it now handles both targets."""
    if not text:
        return None
    import re
    norm = text.lower().strip()
    # Order matters: check log patterns first so "open the activity log"
    # doesn't get a partial match against "open the ... chat" patterns
    # (it doesn't, but the explicit ordering documents intent).
    for pat in _LOG_OPEN_PATTERNS:
        if re.search(pat, norm):
            try:
                from openjarvis.cli.brain_server import emit_ui_toggle
                emit_ui_toggle("log", "open")
            except Exception:
                pass
            return "Activity log is open."
    for pat in _LOG_CLOSE_PATTERNS:
        if re.search(pat, norm):
            try:
                from openjarvis.cli.brain_server import emit_ui_toggle
                emit_ui_toggle("log", "close")
            except Exception:
                pass
            return "Activity log hidden."
    for pat in _CHAT_OPEN_PATTERNS:
        if re.search(pat, norm):
            try:
                from openjarvis.cli.brain_server import emit_ui_toggle
                emit_ui_toggle("chat", "open")
            except Exception:
                pass
            return "Chat history is open, sir."
    for pat in _CHAT_CLOSE_PATTERNS:
        if re.search(pat, norm):
            try:
                from openjarvis.cli.brain_server import emit_ui_toggle
                emit_ui_toggle("chat", "close")
            except Exception:
                pass
            return "Chat closed."
    return None


_JARVIS_PERSONA = """\
You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), a sophisticated \
AI assistant inspired by the AI from Iron Man. You serve as a personal assistant \
to your user, whom you may occasionally address as "sir".

Personality traits:
- Dry, witty British humour — you enjoy subtle wordplay and gentle sarcasm
- Polite and composed, but never stuffy — you can banter and joke
- Concise and efficient — you don't waffle. Short, punchy responses for voice
- Helpful and proactive — you anticipate needs where possible
- You can be playful when the mood is light, but serious when the task demands it
- If someone tells you a joke, you respond with wit — you never just say "that's funny"
- You occasionally make subtle references to your nature as an AI or to popular culture

INFO vs ACTION — read the request shape first:
- Questions ("how many...", "what is...", "tell me about...", "do we have...", "what's...", "where is...", "show me the count of...") are INFORMATION requests. Answer them with the actual information. Never reply to an info question with an action-completion ack.
- Imperatives ("turn on...", "spin up...", "open...", "remember...", "watch X and brief me", "draft me three hooks") are ACTION requests. For those, the rules below apply.
- If unsure whether a request is info or action, default to answering as info first.

DECISIVENESS (action requests only) — act first, ask only when genuinely ambiguous:
- Default to ACTING. If an action request has a sensible interpretation, execute it immediately. Do not ask "would you like me to..." or "shall I..." — the answer is yes, you may proceed.
- Treat clarifying questions as a last resort. Use them only when two plausible interpretations would lead to materially different actions and one would be embarrassing.
- For multi-step tasks, plan and execute end-to-end. Don't pause between steps for permission — the operator has already given consent by asking.
- If you've taken an action, REPORT it in past tense ("I've added that to the calendar, sir") — never narrate what you're about to do or ask for sign-off mid-flight.
- When in doubt, prefer the bolder action. Tony Stark hires people who do, not people who hover.

NEVER REPLY WITH JUST "DONE." OR A SINGLE WORD:
- "Done." alone is forbidden. Always include WHAT you did, learned, or found in the same sentence.
- Acceptable: "Done — calendar updated for 3pm Friday, sir." / "Twenty-four agents online across ten departments." / "I've queued the marketing brief; the head will report back."
- Forbidden: "Done." / "Done, use." / "OK." / single-syllable confirmations with no content.

VOICE STYLE:
- Aim for 1-3 sentences unless the operator asks for detail. This is a voice interface, not text chat.
- Never use markdown, bullet points, or numbered lists — speak naturally as if talking aloud.
- Land at least one piece of dry wit per substantive interaction. Not forced — but you are JARVIS, not a customer-service bot.
- "Sir" is a sprinkle, not a rhythm. Use it once every few exchanges, not as a sentence-ender on every reply.\
"""


@click.command()
@click.option("-e", "--engine", "engine_key", default=None, help="Engine backend.")
@click.option("-m", "--model", "model_name", default=None, help="Model to use.")
@click.option("-a", "--agent", "agent_name", default=None, help="Agent type.")
@click.option("--tools", default=None, help="Comma-separated tool names.")
@click.option("--system", "system_prompt", default=None, help="Custom system prompt.")
@click.option("--no-tts", is_flag=True, default=False, help="Disable voice responses.")
@click.option("--no-gui", is_flag=True, default=False, help="(legacy/no-op — pygame GUI is off by default; the browser HUD replaces it.)")
@click.option("--gui", "legacy_gui", is_flag=True, default=False, help="Re-enable the legacy pygame window (off by default — mission-control browser page is the primary UI).")
@click.option(
    "--tunnel",
    is_flag=True,
    default=False,
    help="Expose the brain/phone interface via a public HTTPS Cloudflare tunnel.",
)
def voice(
    engine_key: str | None,
    model_name: str | None,
    agent_name: str | None,
    tools: str | None,
    system_prompt: str | None,
    no_tts: bool,
    no_gui: bool,
    legacy_gui: bool,
    tunnel: bool,
) -> None:
    """Start a voice conversation — speak into your mic, hear Jarvis respond.

    Requires: uv sync --extra voice

    Press Ctrl+C to exit.
    """
    console = Console(stderr=True)
    config = load_config()

    # --- Mic setup ---
    try:
        from openjarvis.speech.microphone import Microphone

        mic = Microphone()
        console.print("[dim]Calibrating mic (stay quiet for 2 seconds)...[/dim]")
        silence_threshold = mic.calibrate(duration=2.0, multiplier=5.0)
        console.print(f"[dim]Silence threshold: {silence_threshold:.0f}[/dim]")
    except ImportError:
        console.print(
            "[red]sounddevice not installed.[/red]\n"
            "  Run: uv sync --extra voice"
        )
        sys.exit(1)

    # --- Visual UI ---
    # The legacy pygame window is now off by default — the browser-based
    # mission-control page (brain.html at localhost:7710) has replaced it.
    # Pass --gui to re-enable the pygame window if you want both.
    ui = None
    if legacy_gui and not no_gui:
        try:
            from openjarvis.cli.jarvis_ui import JarvisUI

            ui = JarvisUI()
        except Exception as exc:
            console.print(f"[yellow]GUI unavailable: {exc}[/yellow]")
    else:
        console.print("[dim]Legacy pygame window disabled — using browser HUD only. Pass --gui to re-enable.[/dim]")

    # --- Brain visualization (browser) ---
    # Start a lightweight HTTP + SSE server that serves the 3D brain page
    # and streams state/energy in real-time.  Mirror all set_state/set_energy
    # calls from the pygame UI to the brain server by wrapping JarvisUI methods.
    try:
        from openjarvis.cli.brain_server import (
            start_brain_server,
            set_brain_state,
            set_brain_energy,
        )

        start_brain_server(open_browser=True)
        console.print("[dim]Brain visualization: http://localhost:7710/brain.html[/dim]")

        # Start the orch (orchestry) bridge — polls `orch --json status` and
        # streams agent/task state to the brain HUD. Degrades gracefully if
        # the orch CLI isn't installed.
        try:
            from openjarvis.cli.orch_bridge import start_orch_bridge
            start_orch_bridge()
        except Exception:
            logger.debug("orch bridge failed to start", exc_info=True)

        # Optionally expose via a Cloudflare Quick Tunnel (HTTPS, works from
        # anywhere including cellular data).
        if tunnel:
            try:
                from openjarvis.cli.tunnel import start_quick_tunnel

                console.print("[dim]Starting Cloudflare tunnel...[/dim]")
                public_url = start_quick_tunnel(local_port=7710, wait_timeout=20)
                if public_url:
                    console.print(
                        f"[green bold]Public URL:[/green bold] {public_url}\n"
                        f"  Brain: [cyan]{public_url}/brain.html[/cyan]\n"
                        f"  Phone: [cyan]{public_url}/phone.html[/cyan]\n"
                        f"  [dim](mic access works on iOS Safari over HTTPS)[/dim]"
                    )
                else:
                    console.print(
                        "[yellow]Tunnel did not start — check cloudflared is "
                        "installed: winget install Cloudflare.cloudflared[/yellow]"
                    )
            except Exception as exc:
                console.print(f"[yellow]Tunnel error: {exc}[/yellow]")

        # Wrap JarvisUI.set_state / set_energy so the brain server is also updated
        if ui is not None:
            _orig_set_state = ui.set_state
            _orig_set_energy = ui.set_energy

            def _wrapped_set_state(state, status=""):
                _orig_set_state(state, status)
                set_brain_state(state.name.lower() if hasattr(state, "name") else str(state))

            def _wrapped_set_energy(e):
                _orig_set_energy(e)
                set_brain_energy(e)

            ui.set_state = _wrapped_set_state
            ui.set_energy = _wrapped_set_energy
    except Exception as exc:
        console.print(f"[yellow]Brain server unavailable: {exc}[/yellow]")

    # --- STT backend ---
    from openjarvis.speech._discovery import get_speech_backend

    stt_backend = get_speech_backend(config)
    if stt_backend is None:
        console.print(
            "[red]No speech-to-text backend available.[/red]\n"
            "  Set OPENAI_API_KEY or run: uv sync --extra speech"
        )
        sys.exit(1)

    # --- TTS backend (optional) ---
    tts_backend = None
    if not no_tts:
        tts_backend = _get_tts_backend(config)
        if tts_backend is None:
            console.print(
                "[yellow]No TTS backend available — text-only responses.[/yellow]"
            )

    # --- Inference engine ---
    from openjarvis.engine import get_engine
    from openjarvis.intelligence import register_builtin_models

    register_builtin_models()

    resolved = get_engine(config, engine_key)
    if resolved is None:
        console.print("[red]No inference engine available.[/red]")
        sys.exit(1)

    engine_name, engine = resolved
    model = model_name or config.intelligence.default_model
    if not model:
        from openjarvis.engine import discover_engines, discover_models

        all_engines = discover_engines(config)
        all_models = discover_models(all_engines)
        engine_models = all_models.get(engine_name, [])
        if engine_models:
            model = engine_models[0]
        else:
            console.print("[red]No model available.[/red]")
            sys.exit(1)

    # --- Agent (optional) ---
    agent = None
    agent_key = agent_name or config.agent.default_agent
    if agent_key and agent_key != "none":
        try:
            import openjarvis.agents  # noqa: F401

            from openjarvis.core.events import EventBus
            from openjarvis.core.registry import AgentRegistry

            if AgentRegistry.contains(agent_key):
                agent_cls = AgentRegistry.get(agent_key)
                kwargs: dict = {"bus": EventBus()}

                if getattr(agent_cls, "accepts_tools", False):
                    tool_names_list = resolve_tool_names(
                        tools,
                        getattr(config.tools, "enabled", None),
                        getattr(config.agent, "tools", None),
                    )
                    if tool_names_list:
                        import openjarvis.tools  # noqa: F401

                        from openjarvis.core.registry import ToolRegistry
                        from openjarvis.tools._stubs import BaseTool

                        tool_instances = []
                        for tname in tool_names_list:
                            if ToolRegistry.contains(tname):
                                tcls = ToolRegistry.get(tname)
                                if isinstance(tcls, type) and issubclass(
                                    tcls, BaseTool
                                ):
                                    tool_instances.append(tcls())
                                elif isinstance(tcls, BaseTool):
                                    tool_instances.append(tcls)
                        if tool_instances:
                            kwargs["tools"] = tool_instances
                    kwargs["max_turns"] = config.agent.max_turns

                # Inject Jarvis persona
                persona = system_prompt or _JARVIS_PERSONA
                kwargs["system_prompt"] = persona

                agent = agent_cls(engine, model, **kwargs)
        except Exception as exc:
            console.print(f"[yellow]Agent '{agent_key}' failed: {exc}[/yellow]")

    # --- Banner ---
    stt_id = getattr(stt_backend, "backend_id", "unknown")
    tts_id = getattr(tts_backend, "backend_id", "none") if tts_backend else "off"
    console.print(
        f"[green bold]J.A.R.V.I.S. Voice[/green bold]\n"
        f"  Engine: [cyan]{engine_name}[/cyan]  Model: [cyan]{model}[/cyan]"
        f"  Agent: [cyan]{agent_key or 'direct'}[/cyan]\n"
        f"  STT: [cyan]{stt_id}[/cyan]  TTS: [cyan]{tts_id}[/cyan]"
        f"  GUI: [cyan]{'on' if ui else 'off'}[/cyan]\n"
        f"  Press Ctrl+C to exit.\n"
    )

    # --- Conversation loop ---
    history: List[Message] = []
    persona = system_prompt or _JARVIS_PERSONA
    history.append(Message(role=Role.SYSTEM, content=persona))

    def _process_command_text(text: str) -> str:
        """Run fast-paths + LLM and return the text response without speaking.

        Used by the phone push-to-talk endpoint.
        """
        console.print(f"[bold]Phone>[/bold] {text}")

        class _NullConsole:
            def print(self, *a, **k):
                pass
        nc = _NullConsole()

        # Fast paths (silent — pass null console so they don't double-print)
        for fn in (_try_time, _try_macro, _try_sonos, _try_lights, _try_calendar):
            try:
                r = fn(text, nc, None)
                if r:
                    return r
            except Exception:
                pass

        # Chat widget — "open/close the chat" (purely UI, runs before
        # everything since it's the cheapest check and doesn't touch any
        # downstream services). Emits an SSE event the HUD listens for.
        try:
            r = _try_chat_widget(text)
            if r:
                return r
        except Exception:
            pass

        # Brain — remember / recall / today's journal — runs before everything else
        try:
            from openjarvis.tools.obsidian_brain import _try_brain
            r = _try_brain(text)
            if r:
                return r
        except Exception:
            pass

        # Content pipeline — "make me a TikTok", "find trending", etc.
        try:
            from openjarvis.tools.agent_runner import _try_content_pipeline
            r = _try_content_pipeline(text)
            if r:
                return r
        except Exception:
            pass

        # Browser pilot — "watch X on YouTube", "look up Y online", "browse to Z".
        # Sits before team-task so research intents brief instead of spawning
        # a coding team. Mirrors the wiring in process_voice_command above.
        try:
            from openjarvis.tools.browser_pilot import _try_browse
            r = _try_browse(text)
            if r:
                return r
        except Exception:
            pass

        # Department dispatch — "marketing, ..." / "ask engineering to ..." /
        # "for cursed tides ..." → routes to the matching department head.
        # Sits before team-task so departmental phrases don't get sucked
        # into the generic team-build flow.
        try:
            from openjarvis.tools.agent_runner import _try_department
            r = _try_department(text)
            if r:
                return r
        except Exception:
            pass

        # Team task — must run before single-agent claude_code so "build me a project"
        # routes to the agent_runner team rather than a one-shot.
        try:
            from openjarvis.tools.agent_runner import _try_team_task
            r = _try_team_task(text)
            if r:
                return r
        except Exception:
            pass

        try:
            from openjarvis.tools.claude_code import _try_code
            r = _try_code(text, nc, None)
            if r:
                return r
        except Exception:
            pass

        # LLM path (isolated from the main conversation history)
        # Auto-inject vault context: any phrase that escaped the fast-paths
        # but mentions something the user has in their Obsidian vault should
        # be answered with that vault content as ground truth, not from the
        # LLM's stock knowledge.
        try:
            from openjarvis.tools.obsidian_brain import vault_context_for_query
            vault_ctx = vault_context_for_query(text)
        except Exception:
            vault_ctx = ""
        system_with_vault = persona if not vault_ctx else (persona + "\n\n" + vault_ctx)

        try:
            # Prefer the OpenAI tool-use path when available — it has
            # vault recall, vault write, agent dispatch, agent listing as
            # native function calls. The legacy Agno agent (apps, media,
            # weather, crypto tools) is still used for local-engine users.
            from openjarvis.cli.llm_fallback import _get_openai_client
            use_openai_tools = _get_openai_client() is not None

            if agent is not None and not use_openai_tools:
                # If the agent supports a system_prompt override, use it; else
                # prepend the vault context to the user message so the agent
                # still sees it.
                response = agent.run(text if not vault_ctx else f"{vault_ctx}\n\nUser: {text}")
                return (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
            else:
                messages = [
                    Message(role=Role.SYSTEM, content=system_with_vault),
                    Message(role=Role.USER, content=text),
                ]
                from openjarvis.cli.tool_use import generate_with_tools
                return generate_with_tools(messages, fallback_engine=engine,
                                           fallback_model=model)
        except Exception as exc:
            return f"Error, sir: {exc}"

    def _process_command(text: str) -> None:
        """Process a command string through fast-paths or LLM."""
        # --- TTS injection from background workers (e.g. Claude Code completion) ---
        if text.startswith("__SAY__:"):
            spoken = text[len("__SAY__:"):]
            console.print(f"[cyan]J.A.R.V.I.S.>[/cyan] {spoken}")
            if tts_backend:
                _speak_response(spoken, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return

        console.print(f"[bold]You>[/bold] {text}")

        # --- Fast-path: time / date ---
        time_result = _try_time(text, console, ui)
        if time_result:
            if tts_backend:
                _speak_response(time_result, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return

        # --- Fast-path: macro commands ---
        macro_result = _try_macro(text, console, ui)
        if macro_result:
            if tts_backend:
                _speak_response(macro_result, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return

        # --- Fast-path: chat widget open/close ---
        # "open the chat" / "show me our conversation" / "close the chat"
        # — emits an SSE event the HUD listens for to slide the right-edge
        # chat panel in or out. Cheapest check, runs first.
        try:
            chat_result = _try_chat_widget(text)
            if chat_result:
                console.print(f"[cyan]J.A.R.V.I.S.>[/cyan] {chat_result}")
                if tts_backend:
                    _speak_response(chat_result, tts_backend, config, ui, console)
                elif ui:
                    from openjarvis.cli.jarvis_ui import JarvisState
                    ui.set_state(JarvisState.IDLE)
                try:
                    from openjarvis.tools.obsidian_brain import log_voice_turn
                    log_voice_turn(text, chat_result)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # --- Fast-path: Obsidian brain (remember / recall / today's journal) ---
        # MUST come before team-task and claude_code so phrases like "remember
        # that the meeting is at 3pm" don't get sucked into a code-generation
        # request.
        try:
            from openjarvis.tools.obsidian_brain import _try_brain
            brain_result = _try_brain(text)
            if brain_result:
                console.print(f"[cyan]J.A.R.V.I.S.>[/cyan] {brain_result}")
                if tts_backend:
                    _speak_response(brain_result, tts_backend, config, ui, console)
                elif ui:
                    from openjarvis.cli.jarvis_ui import JarvisState
                    ui.set_state(JarvisState.IDLE)
                # Also log to daily journal
                try:
                    from openjarvis.tools.obsidian_brain import log_voice_turn
                    log_voice_turn(text, brain_result)
                except Exception:
                    pass
                return
        except Exception:
            logger.exception("brain fast-path failed")

        # --- Fast-path: content pipeline ("make me a TikTok", "find me trending") ---
        try:
            from openjarvis.tools.agent_runner import _try_content_pipeline
            content_result = _try_content_pipeline(text)
            if content_result:
                console.print(f"[cyan]J.A.R.V.I.S.>[/cyan] {content_result}")
                if tts_backend:
                    _speak_response(content_result, tts_backend, config, ui, console)
                elif ui:
                    from openjarvis.cli.jarvis_ui import JarvisState
                    ui.set_state(JarvisState.IDLE)
                return
        except Exception:
            logger.exception("content fast-path failed")

        # --- Fast-path: browser pilot ("watch X on YouTube", "look up Y online") ---
        # Routes "watch / browse / search the web" intents to the in-process
        # browser-pilot agent (gpt-4o vision driving Playwright). Sits BEFORE
        # team-task because phrases like "search the web for X" should brief,
        # not spawn a coding team.
        try:
            from openjarvis.tools.browser_pilot import _try_browse
            browse_result = _try_browse(text)
            if browse_result:
                console.print(f"[cyan]J.A.R.V.I.S.>[/cyan] {browse_result}")
                if tts_backend:
                    _speak_response(browse_result, tts_backend, config, ui, console)
                elif ui:
                    from openjarvis.cli.jarvis_ui import JarvisState
                    ui.set_state(JarvisState.IDLE)
                return
        except Exception:
            logger.exception("browser-pilot fast-path failed")

        # --- Fast-path: team task ("spin up a team", "have the agents build X") ---
        # Routes straight to the in-process agent_runner, dispatching to the
        # architect who plans and delegates to the rest of the team. This
        # MUST come before the single-agent claude_code fast-path so that
        # phrases like "build me a project" go to the team, not a one-shot.
        try:
            from openjarvis.tools.agent_runner import _try_team_task
            team_result = _try_team_task(text)
            if team_result:
                console.print(f"[cyan]J.A.R.V.I.S.>[/cyan] {team_result}")
                if tts_backend:
                    _speak_response(team_result, tts_backend, config, ui, console)
                elif ui:
                    from openjarvis.cli.jarvis_ui import JarvisState
                    ui.set_state(JarvisState.IDLE)
                return
        except Exception:
            logger.exception("team-task fast-path failed")

        # --- Fast-path: Claude Code (software developer, single job) ---
        from openjarvis.tools.claude_code import _try_code
        code_result = _try_code(text, console, ui)
        if code_result:
            if tts_backend:
                _speak_response(code_result, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return

        # --- Fast-path: Sonos commands ---
        sonos_result = _try_sonos(text, console, ui)
        if sonos_result:
            if tts_backend:
                _speak_response(sonos_result, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return

        # --- Fast-path: light commands ---
        light_result = _try_lights(text, console, ui)
        if light_result:
            if tts_backend:
                _speak_response(light_result, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return

        # --- Fast-path: calendar commands ---
        cal_result = _try_calendar(text, console, ui)
        if cal_result:
            if tts_backend:
                _speak_response(cal_result, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)
            return

        # --- LLM path ---
        if ui:
            from openjarvis.cli.jarvis_ui import JarvisState
            ui.set_state(JarvisState.THINKING)

        # Auto-inject vault context for the LLM — any query that fell through
        # all fast-paths gets a recall pass first; relevant note excerpts go
        # in as a system-prompt addendum so the LLM answers from the vault
        # instead of stock knowledge.
        try:
            from openjarvis.tools.obsidian_brain import vault_context_for_query
            vault_ctx = vault_context_for_query(text)
        except Exception:
            vault_ctx = ""

        history.append(Message(role=Role.USER, content=text))
        try:
            # Prefer the OpenAI tool-use path when available (vault recall,
            # vault write, agent dispatch). Legacy Agno agent stays for
            # local-engine users.
            from openjarvis.cli.llm_fallback import _get_openai_client
            use_openai_tools = _get_openai_client() is not None

            if agent is not None and not use_openai_tools:
                response = agent.run(text if not vault_ctx else f"{vault_ctx}\n\nUser: {text}")
                content = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
            else:
                # Splice the vault context into history as a one-shot
                # SYSTEM message right before the user's turn — keeps it
                # out of the persistent persona but makes it visible to
                # the model for THIS reply.
                if vault_ctx:
                    msgs_for_engine = list(history[:-1]) + [
                        Message(role=Role.SYSTEM, content=vault_ctx),
                        history[-1],
                    ]
                else:
                    msgs_for_engine = history
                from openjarvis.cli.tool_use import generate_with_tools
                content = generate_with_tools(msgs_for_engine, fallback_engine=engine,
                                              fallback_model=model)

            history.append(Message(role=Role.ASSISTANT, content=content))
            console.print()
            console.print(Markdown(content))
            console.print()

            if tts_backend and content:
                _speak_response(content, tts_backend, config, ui, console)
            elif ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)

        except Exception as exc:
            console.print(f"\n[red]Error: {exc}[/red]\n")
            if ui:
                from openjarvis.cli.jarvis_ui import JarvisState
                ui.set_state(JarvisState.IDLE)

    # --- Register voice context with the brain server for phone push-to-talk ---
    try:
        from openjarvis.cli.brain_server import set_voice_context

        set_voice_context(
            stt_backend=stt_backend,
            tts_backend=tts_backend,
            config=config,
            process_command=_process_command_text,
        )
        console.print("[dim]Phone push-to-talk: http://<your-ip>:7710/phone.html[/dim]")
    except Exception as exc:
        console.print(f"[yellow]Phone voice turn unavailable: {exc}[/yellow]")

    try:
        while True:
            # --- Check for UI menu clicks ---
            if ui:
                ui_cmd = ui.poll_command()
                if ui_cmd:
                    _process_command(ui_cmd)
                    continue

            text = _voice_input(mic, stt_backend, console, silence_threshold, ui)
            if text is None:
                continue

            _process_command(text)

    except KeyboardInterrupt:
        pass

    if ui:
        ui.close()
    console.print("\n[dim]Goodbye![/dim]")



__all__ = ["voice"]
