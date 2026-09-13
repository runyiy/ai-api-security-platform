"""Default-disabled B/J/W seam. No production transport is installed."""

from app.ai.proposals.codec import ProposalRejected


def production_admission(*, evidence=None):
    """No accepted complete-input proof or operational topology exists yet.

    This is deliberately not a configurable boolean, receipt cast, environment
    switch, or injectable secret/DNS callback. Synthetic construction is a
    separate, Unix-socket-only fixture in this package.
    """
    raise ProposalRejected('PROVIDER_DISABLED')
