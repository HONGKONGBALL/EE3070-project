"""Connect one normally open Raspberry Pi GPIO button to the assistant."""

from threading import Lock

from .assistant_provider import AssistantError


class GpioPushToTalk:
    def __init__(self, pin, assistant):
        from gpiozero import Button

        self.assistant = assistant
        self.lock = Lock()
        self.request_id = None
        self.closed = False
        self.button = Button(pin, pull_up=True, bounce_time=0.035)
        self.button.when_pressed = self.pressed
        self.button.when_released = self.released

    def pressed(self):
        with self.lock:
            if self.closed or self.request_id:
                return
            try:
                self.request_id = self.assistant.press()
            except AssistantError:
                # A busy assistant already owns the microphone.
                return

    def released(self):
        with self.lock:
            if self.request_id:
                self.assistant.release(self.request_id)
                self.request_id = None

    def close(self):
        with self.lock:
            self.closed = True
            if self.request_id:
                self.assistant.release(self.request_id, cancelled=True)
                self.request_id = None
        self.button.close()
