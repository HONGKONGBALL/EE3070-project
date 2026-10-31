"""Offline QA sink: exercise the real queue with WAV durations, never speaker output."""
from pathlib import Path
import time
import wave


class TimedReviewAudio:
    def __init__(self, speech, directory):
        self.speech, self.directory = speech, Path(directory)
        self.state, self.error, self.last_played = "simulated", "", None
        self.playback_id, self.context, self.deadline = None, None, 0
        self.sequence = 0

    def play(self, clips, text=None, *, context=None):
        self.stop()
        with wave.open(str(self.directory/(clips[0]+".wav")),"rb") as wav:
            duration=wav.getnframes()/wav.getframerate()
        now=time.monotonic();self.sequence+=1
        self.context=dict(context or {});self.playback_id=f"simulated-{self.sequence}"
        self.context.update(phrase_id=clips[0],playback_id=self.playback_id,
                            simulation_started_at=now,duration_s=duration)
        self.deadline=now+duration;self.state="simulated_playing"
        self.speech.record("audio_simulated_start",**self.context,
                           timing_basis="Timed WAV sink; no audio device or pw-play process")
        self.speech._delivery(self.context.get("event_id",""),"started",now)

    def busy(self):
        if self.context and time.monotonic()>=self.deadline:
            context,self.context=self.context,None
            self.last_played=context;self.state="simulated"
            self.speech.record("audio_simulated_done",**context,finished_at=time.monotonic())
            self.speech._delivery(context.get("event_id",""),"completed",time.monotonic())
        return self.context is not None

    def stop(self):
        if self.busy():
            context,self.context=self.context,None
            self.state="simulated"
            self.speech.record("audio_simulated_interrupted",**context)
            self.speech._delivery(context.get("event_id",""),"interrupted",time.monotonic())
