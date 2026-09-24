"""The silence helper's contract, pinned without a bus.

On a physical bus a timeout is ambiguous: it also matches a dead adapter, a bitrate
mismatch, an unterminated bus or a bus-off interface. The helper exists to disambiguate,
so its own behaviour when the channel is dead is the thing worth pinning.
"""

import pytest

from tests.integration.conftest import assert_silent


class FakeChannel:
    """Replays a scripted sequence of answers; None means "time out"."""

    def __init__(self, answers: list[bytes | None]) -> None:
        self.answers = list(answers)
        self.sent: list[bytes] = []

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self) -> bytes:
        answer = self.answers.pop(0)
        if answer is None:
            raise TimeoutError
        return answer


def test_silence_then_a_good_answer_passes():
    channel = FakeChannel([None, b"\x41\x2f\x7f"])
    assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")
    assert channel.sent == [b"\x3e\x80", b"\x01\x2f"]


def test_an_answered_request_is_not_silent():
    channel = FakeChannel([b"\x7e\x00"])
    with pytest.raises(AssertionError):
        assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")


def test_a_dead_channel_fails_rather_than_passing():
    # The whole point: two timeouts must not read as "the simulator stayed silent".
    channel = FakeChannel([None, None])
    with pytest.raises(AssertionError, match="channel went dead"):
        assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")


def test_a_wrong_probe_answer_fails():
    channel = FakeChannel([None, b"\x41\x2f\x00"])
    with pytest.raises(AssertionError):
        assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")


def test_the_failure_message_names_the_silent_request():
    # A bench failure is read by someone standing at the bus, so the message has to say
    # which request's silence is now unproven, not just "assertion failed".
    channel = FakeChannel([None, None])
    with pytest.raises(AssertionError, match="3e80"):
        assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")
