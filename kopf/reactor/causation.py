"""
Detection of the event causes, based on the resource state.

The low-level watch-events are highly limited in information on what
caused them, and they only notify that the object was changed somehow:

* ``ADDED`` for the newly created objects (or for the first-time listing).
* ``MODIFIED`` for the changes of any field, be that metainfo, spec, or status.
* ``DELETED`` for the actual deletion of the object post-factum.

The conversion of low-level *events* to high level *causes* is done by
checking the object's state and comparing it to the saved last-seen state.

This allows to track which specific fields were changed, and if are those
changes are important enough to call the handlers: e.g. the ``status`` changes
are ignored, so as some selected system fields of the ``metainfo``.

For deletion, the cause is detected when the object is just marked for deletion,
not when it is actually deleted (as the events notify): so that the handlers
could execute on the yet-existing object (and its children, if created).
"""
import logging
from typing import NamedTuple, Text, Mapping, MutableMapping, Optional, Any, Union

from kopf.reactor import registries
from kopf.structs import diffs
from kopf.structs import finalizers
from kopf.structs import lastseen

# Constants for event types, to prevent a direct usage of strings, and typos.
# They are not exposed by the framework, but are used internally. See also: `kopf.on`.
NEW = 'new'
CREATE = 'create'
UPDATE = 'update'
DELETE = 'delete'
NOOP = 'noop'
FREE = 'free'
GONE = 'gone'

# The human-readable names of these causes. Will be capitalised when needed.
TITLES = {
    CREATE: 'creation',
    UPDATE: 'update',
    DELETE: 'deletion',
}


class Cause(NamedTuple):
    """
    The cause is what has caused the whole reaction as a chain of handlers.

    Unlike the low-level Kubernetes watch-events, the cause is aware
    of actual field changes, including multi-handler changes.
    """
    logger: Union[logging.Logger, logging.LoggerAdapter]
    resource: registries.Resource
    event: Text
    body: MutableMapping
    patch: MutableMapping
    diff: Optional[diffs.Diff] = None
    old: Optional[Any] = None
    new: Optional[Any] = None


def detect_cause(
        event: Mapping,
        **kwargs
) -> Cause:
    """
    Detect the cause of the event to be handled.

    This is a purely computational function with no side-effects.
    The causes are then consumed by `custom_object_handler`,
    which performs the actual handler invocation, logging, patching,
    and other side-effects.
    """
    body = event['object']

    # The object was really deleted from the cluster. But we do not care anymore.
    if event['type'] == 'DELETED':
        return Cause(
            event=GONE,
            body=body,
            **kwargs)

    # The finalizer has been just removed. We are fully done.
    if finalizers.is_deleted(body) and not finalizers.has_finalizers(body):
        return Cause(
            event=FREE,
            body=body,
            **kwargs)

    if finalizers.is_deleted(body):
        return Cause(
            event=DELETE,
            body=body,
            **kwargs)

    # For a fresh new object, first block it from accidental deletions without our permission.
    # The actual handler will be called on the next call.
    if not finalizers.has_finalizers(body):
        return Cause(
            event=NEW,
            body=body,
            **kwargs)

    # For an object seen for the first time (i.e. just-created), call the creation handlers,
    # then mark the state as if it was seen when the creation has finished.
    if not lastseen.has_state(body):
        return Cause(
            event=CREATE,
            body=body,
            **kwargs)

    # The previous step triggers one more patch operation without actual changes. Ignore it.
    # Either the last-seen state or the status field has changed.
    if not lastseen.is_state_changed(body):
        return Cause(
            event=NOOP,
            body=body,
            **kwargs)

    # And what is left, is the update operation on one of the useful fields of the existing object.
    old, new, diff = lastseen.get_state_diffs(body)
    return Cause(
        event=UPDATE,
        body=body,
        diff=diff,
        old=old,
        new=new,
        **kwargs)
