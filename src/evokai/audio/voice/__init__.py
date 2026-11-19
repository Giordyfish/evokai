"""Voice sessions on Pipecat (optional ``voice`` extra).

The browser connects over WebRTC; Silero VAD and Smart Turn v3 decide when the user has finished;
the transcript becomes an Evokai turn in voice mode on the same conversation, with the same tools,
confirmations, budgets and events as text. The turn's ``say`` sentences are spoken by the TTS
provider and every event reaches the client as an RTVI server message.

Nothing here is imported by the core: the voice routes load it only when voice is enabled.
"""
