"""Channels beyond the web panel: an agent's mailbox, a Microsoft Teams bot and a Slack app.

- ``config``: what each channel accepts (secrets only as environment variable names);
- ``mapping``: how a person of the channel becomes the application's subject;
- ``email``, ``teams``, ``slack``: each channel's own protocol (parsing, verification, replies);
- ``service``: deliveries become server runs (e-mail) or turns (Teams, Slack), and their answers
  replies, confirmations and questions;
- ``store``: channels and their threads.
"""
