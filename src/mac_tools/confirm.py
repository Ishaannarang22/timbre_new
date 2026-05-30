"""
confirm.py — the per-call confirmation broker.

ONE ConfirmationBroker exists per phone call. When the LLM asks to do something risky
(CONFIRM class), dispatch() does NOT run it — it stores a single pending action here and
speaks a read-back. The action only executes when the owner says "yes" (confirm_action ->
broker.confirm()). Saying "no"/"cancel" drops it (cancel_action -> broker.cancel()).

Only ONE action can be pending at a time: staging a new action replaces any prior one. That
matches how a phone conversation actually works — you read back the most recent request and
wait for a yes/no before moving on.

`do` is a zero-arg callable that performs the action and returns the SHORT spoken-friendly
string to say back. confirm() runs it and translates any exception into a friendly string so
a wedged action never raises into the voice pipeline.
"""

from typing import Callable


class ConfirmationBroker:
    """Single-slot pending-action store for one phone call."""

    def __init__(self) -> None:
        self._summary: str | None = None
        self._do: Callable[[], str] | None = None

    def stage(self, summary: str, do: Callable[[], str]) -> None:
        """Store a single pending action (replacing any prior one). `summary` is the human
        read-back; `do` runs the action on confirm and returns the spoken result string."""
        self._summary = summary
        self._do = do

    def confirm(self) -> str:
        """Run the pending action, clear it, and return its spoken result. If nothing is
        waiting, say so. Never raises — a failing action returns a friendly string."""
        if self._do is None:
            return "Nothing was waiting."
        do = self._do
        # Clear BEFORE running so a slow/failing action can't be accidentally re-run, and so
        # the broker is immediately ready for the next request.
        self._summary = None
        self._do = None
        try:
            return do()
        except Exception:
            # Defense in depth: handlers shouldn't raise, but if one does we must not let it
            # bubble into the pipeline.
            return "Sorry, that didn't work."

    def cancel(self) -> str:
        """Drop the pending action. Returns a spoken-friendly acknowledgement."""
        if self._do is None:
            return "Nothing to cancel."
        self._summary = None
        self._do = None
        return "Okay, cancelled."

    def pending(self) -> bool:
        """True iff an action is currently staged and waiting for confirmation."""
        return self._do is not None
