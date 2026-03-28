"""Configurable weights for PRD safety score G_s = A*w1 + H*w2 + G*w3 - R*w4 (task 0.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pydantic_settings import BaseSettings, SettingsConfigDict


class SafetyWeightsSettings(BaseSettings):
    """Load weights from environment; defaults remain explicit and documented."""

    model_config = SettingsConfigDict(
        env_prefix="GUVENLI_IZLER_W_",
        env_file=".env",
        extra="ignore",
    )

    w1: float = 0.35
    w2: float = 0.25
    w3: float = 0.30
    w4: float = 0.10


@dataclass(frozen=True)
class SafetyWeights:
    w1: float
    w2: float
    w3: float
    w4: float

    @classmethod
    def from_env(cls) -> "SafetyWeights":
        try:
            settings = SafetyWeightsSettings()
            return cls(
                w1=settings.w1,
                w2=settings.w2,
                w3=settings.w3,
                w4=settings.w4,
            )
        except Exception:
            return cls(w1=0.35, w2=0.25, w3=0.30, w4=0.10)


def compute_segment_score(
    illumination: float,
    mobility: float,
    official_security_proximity: float,
    user_reported_risk: float,
    weights: SafetyWeights | None = None,
) -> float:
    """Return G_s for normalized inputs in [0, 1]."""
    w = weights or SafetyWeights.from_env()
    return (
        illumination * w.w1
        + mobility * w.w2
        + official_security_proximity * w.w3
        - user_reported_risk * w.w4
    )


def weights_as_dict(weights: SafetyWeights) -> Mapping[str, float]:
    return {"w1": weights.w1, "w2": weights.w2, "w3": weights.w3, "w4": weights.w4}
