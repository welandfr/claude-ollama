"""Status line rendering. Input is the JSON Claude Code pipes in on stdin."""

import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "statusline", pathlib.Path(__file__).resolve().parent.parent / "statusline.py"
)
statusline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(statusline)


@pytest.mark.parametrize(
    "tokens, expected",
    [
        (65536, "64K"),      # a context window is binary
        (32768, "32K"),
        (480000, "480K"),    # a token count is decimal
        (1000000, "1000K"),
        (21500, "22K"),
        (512, "512"),
        (0, "0"),
    ],
)
def test_human(tokens, expected):
    assert statusline.human(tokens) == expected


def test_renders_model_and_usage():
    out = statusline.render(
        {
            "model": {"display_name": "ornith-1.5:9b"},
            "context_window": {
                "total_input_tokens": 18432,
                "context_window_size": 65536,
                "used_percentage": 28.1,
            },
        }
    )
    assert out == "ornith-1.5:9b | Context: 18K/64K (28%)"


def test_before_the_first_response_shows_only_the_model():
    out = statusline.render(
        {"model": {"display_name": "ornith-1.5:9b"}, "context_window": {"context_window_size": 65536}}
    )
    assert out == "ornith-1.5:9b"


def test_falls_back_to_the_model_id():
    assert statusline.render({"model": {"id": "ornith-1.5:9b"}}) == "ornith-1.5:9b"


def test_empty_input_does_not_raise():
    assert statusline.render({}) == "unknown model"


def test_a_200k_window_is_shown_as_is():
    """Not special-cased: seeing 200K is how you notice the limit is unset."""
    out = statusline.render(
        {
            "model": {"display_name": "ornith-1.5:9b"},
            "context_window": {
                "total_input_tokens": 40000,
                "context_window_size": 200000,
                "used_percentage": 20,
            },
        }
    )
    assert "200K" in out
