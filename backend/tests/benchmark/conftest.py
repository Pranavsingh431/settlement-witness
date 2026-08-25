"""Shared fixtures for benchmark tests."""

from pathlib import Path

import pytest

from app.benchmark.generator import CorpusConfig, GeneratedCorpus, generate

PUBLIC_CONFIG_PATH = Path(__file__).resolve().parents[3] / "benchmark" / "public-corpus.json"
"""The committed public configuration, shared with `make benchmark-evaluate`."""


def small_config(seed: int = 4242) -> CorpusConfig:
    """Return a one-pair-per-template configuration, for fast tests."""
    return CorpusConfig(
        corpus_name="test-corpus", seed=seed, controls_per_anomaly=1, extra_controls=0
    )


@pytest.fixture
def small_corpus() -> GeneratedCorpus:
    """Return a generated corpus covering every template once."""
    return generate(small_config())


@pytest.fixture
def public_config() -> CorpusConfig:
    """Return the committed public configuration."""
    return CorpusConfig.model_validate_json(PUBLIC_CONFIG_PATH.read_text(encoding="utf-8"))
