"""
Shared turn-taking helpers for the voice agents.

Pulled out so both the local-mic bot and the phone bot can share the same
"patient" endpointing brain without one importing the other's module-level
side effects (prompt loading, asyncio.main, etc.).
"""

from loguru import logger

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3


class PatientSmartTurnV3(LocalSmartTurnAnalyzerV3):
    """Smart Turn v3 with a tunable "are you done?" confidence threshold.

    Upstream hardcodes the complete/incomplete decision at `probability > 0.5`,
    so a borderline pause (model only 51% sure you've finished) ends your turn
    mid-thought. We keep the exact ONNX model and audio handling and only
    re-apply the cutoff with `completion_threshold`. Higher = more patient: the
    model must be quite sure you're finished, so mid-sentence pauses stay
    INCOMPLETE and your turn isn't cut off. On a phone call this is what stops
    the agent from talking over the caller while he's still thinking.
    """

    def __init__(self, *, completion_threshold: float = 0.7, **kwargs):
        super().__init__(**kwargs)
        self._completion_threshold = completion_threshold

    def _predict_endpoint(self, audio_array):
        result = super()._predict_endpoint(audio_array)  # run the model unchanged
        # Re-derive the verdict with OUR threshold instead of the hardcoded 0.5.
        result["prediction"] = 1 if result["probability"] > self._completion_threshold else 0
        verdict = "END TURN" if result["prediction"] == 1 else "keep listening"
        logger.info(
            f"endpoint: prob_done={result['probability']:.2f} "
            f"(thr {self._completion_threshold:.2f}) -> {verdict}"
        )
        return result
