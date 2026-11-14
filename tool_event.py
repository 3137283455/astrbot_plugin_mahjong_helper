"""Let native command handlers run inside a tool without aborting the agent."""


class ToolEvent:
    __slots__ = ('_event',)

    def __init__(self, event):
        object.__setattr__(self, '_event', event)

    @property
    def __class__(self):
        # Preserve the platform type checks used by existing plugin handlers.
        return self._event.__class__

    def __getattr__(self, name):
        return getattr(self._event, name)

    def __setattr__(self, name, value):
        setattr(self._event, name, value)

    def stop_event(self):
        # The caller already owns this reply. A command's propagation stop
        # must not cancel its pending send; real user cancellation remains
        # visible on the original event to the framework's stop watcher.
        pass
