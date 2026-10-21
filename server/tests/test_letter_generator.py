"""Cover letter validation."""

import pytest

from cv_server.pipeline.letter_generator import MAX_WORDS, MIN_WORDS, LetterGenerator

from server_helpers import FakeLLM


def letter(words):
    return " ".join(["word"] * words)


def test_accepts_a_letter_of_plausible_length():
    LetterGenerator._validate_letter(letter(MIN_WORDS + 10))


@pytest.mark.parametrize("words", [MIN_WORDS - 1, MAX_WORDS + 1])
def test_rejects_letters_outside_the_word_range(words):
    with pytest.raises(ValueError, match="words"):
        LetterGenerator._validate_letter(letter(words))


@pytest.mark.parametrize("placeholder", ["[Company Name]", "<<role>>"])
def test_rejects_unfilled_placeholders(placeholder):
    text = letter(MIN_WORDS) + " " + placeholder
    with pytest.raises(ValueError, match="placeholder"):
        LetterGenerator._validate_letter(text)


def test_rejects_non_ascii():
    with pytest.raises(ValueError, match="non-ASCII"):
        LetterGenerator._validate_letter(letter(MIN_WORDS) + " — dash")


def test_strips_code_fences_the_model_adds_anyway():
    llm = FakeLLM()
    llm["letter"].replies = ["```\n" + letter(MIN_WORDS + 10) + "\n```"]
    text = LetterGenerator(llm, system_prompt="write").generate(
        "a posting", profile={}, candidate_data={}
    )
    assert text == letter(MIN_WORDS + 10)
