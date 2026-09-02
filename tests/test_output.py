import pytest

from sharkrail.output import capture_output


def test_output_budget_is_measured_in_utf8_bytes():
    captured = capture_output("你好".encode(), b"tail", 4)

    assert captured.retained_bytes == 4
    assert captured.truncated_bytes == 6
    assert captured.truncated is True
    assert captured.decoding_errors is True


def test_output_budget_is_shared_between_streams():
    captured = capture_output(b"out", b"error", 5)

    assert captured.stdout == "out"
    assert captured.stderr == "er"
    assert captured.retained_bytes == 5
    assert captured.truncated_bytes == 3


def test_negative_output_budget_is_rejected():
    with pytest.raises(ValueError, match="greater than or equal to zero"):
        capture_output(b"", b"", -1)
