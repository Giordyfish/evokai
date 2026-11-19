"""Long-term memory: what agents remember about a person across conversations, with consent.

Three kinds, all about how the person works, never about their customers:

- ``preference``: how they like answers («prefers rounded amounts»), always recalled;
- ``semantic``: facts about their work context («looks after the poetry shelf»), recalled when
  relevant to the message;
- ``episodic``: a short summary of a finished conversation, for agents that opt in
  (``memory.episodes``), recalled by that agent when relevant.

Consent is per application (``memory_mode``: ``off``, ``ask``, ``on``) and per person (recorded at
token mint with ``memoryConsent``, or by the person in the chat panel). Writes pass the guard's
``memory.write`` control; recalled memories reach the model spotlighted as untrusted data. The
person lists, edits and deletes what is remembered from the chat panel (session API); export and
erase include memories; the application's ``memory_ttl_days`` expires them.
"""
