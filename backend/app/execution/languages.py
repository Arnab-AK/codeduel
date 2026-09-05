"""
Single lookup table mapping a submission's declared language to the runner
image that executes it. Kept as a plain dict (not a DB table) because this
is a small, closed set the application code owns and versions alongside the
sandbox images themselves — it changes when we build a new image, not when
a user does something.

Adding a second language later (say, C++) means: build a new image under
execution/images/cpp/ with the same run-a-program-against-stdin contract,
add one entry here. Nothing else in judge.py or docker_runner.py needs to
know a new language exists.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageConfig:
    image: str
    filename: str  # what the submitted source is named inside the sandbox


LANGUAGES: dict[str, LanguageConfig] = {
    "python": LanguageConfig(image="codeduel-python-runner:latest", filename="solution.py"),
}


def get_language_config(language: str) -> LanguageConfig:
    try:
        return LANGUAGES[language]
    except KeyError:
        raise ValueError(f"Unsupported language: {language!r}. Supported: {list(LANGUAGES)}")
