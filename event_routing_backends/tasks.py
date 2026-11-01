"""
Celery tasks.
"""
from celery import shared_task
from celery.utils.log import get_task_logger
from celery_utils.persist_on_failure import LoggedPersistOnFailureTask, PersistOnFailureTask
from django.conf import settings

from event_routing_backends.processors.transformer_utils.exceptions import EventNotDispatched, EventRejected
from event_routing_backends.utils.http_client import HttpClient
from event_routing_backends.utils.xapi_lrs_client import LrsClient

logger = get_task_logger(__name__)

ROUTER_STRATEGY_MAPPING = {
    'AUTH_HEADERS': HttpClient,
    'XAPI_LRS': LrsClient,
}


@shared_task(bind=True, base=LoggedPersistOnFailureTask)
def dispatch_event_persistent(self, event_name, event, router_type, host_config):
    """
    Send event to configured client.

    Arguments:
        self (object)       :  celery task object to perform celery actions
        event_name (str)    : name of the original event
        event (dict)        : event dictionary to be delivered.
        router_type (str)   : decides the client to use for sending the event
        host_config (dict)  : contains configurations for the host.
    """
    send_event(self, event_name, event, router_type, host_config)


@shared_task(bind=True,)
def dispatch_event(self, event_name, event, router_type, host_config):
    """
    Send event to configured client.

    Arguments:
        self (object)       : celery task object to perform celery actions
        event_name (str)    : name of the original event
        event (dict)        : event dictionary to be delivered.
        router_type (str)   : decides the client to use for sending the event
        host_config (dict)  : contains configurations for the host.
    """
    send_event(self, event_name, event, router_type, host_config)


def send_event(task, event_name, event, router_type, host_config):
    """
    Send event to configured client.

    Arguments:
        task (object, optional) : celery task object to perform celery actions
        event_name (str)        : name of the original event
        event (dict)            : event dictionary to be delivered.
        router_type (str)       : decides the client to use for sending the event
        host_config (dict)      : contains configurations for the host.
    """
    try:
        client_class = ROUTER_STRATEGY_MAPPING[router_type]
    except KeyError:
        logger.error('Unsupported routing strategy detected: {}'.format(router_type))
        return

    try:
        client = client_class(**host_config)
        client.send(event, event_name)
        logger.debug(
            'Successfully dispatched transformed version of edx event "{}" using client: {}'.format(
                event_name,
                client_class
            )
        )
    except EventNotDispatched as exc:
        logger.exception(
            'Exception occurred while trying to dispatch edx event "{}" using client: {}'.format(
                event_name,
                client_class
            ),
            exc_info=True
        )
        # If this function is called synchronously, we want to raise the exception
        # to inform about errors. If it's called asynchronously, we want to retry
        # the celery task till it succeeds or reaches max retries.
        if not task:
            raise exc
        raise task.retry(exc=exc, countdown=getattr(settings, 'EVENT_ROUTING_BACKEND_COUNTDOWN', 30),
                         max_retries=getattr(settings, ''
                                                       'EVENT_ROUTING_BACKEND_MAX_RETRIES', 3))


@shared_task(bind=True)
def dispatch_bulk_events(self, events, router_type, host_config):
    """
    Send a batch of events to the same configured client.

    Arguments:
        self (object)       : celery task object to perform celery actions
        events (list[dict]) : list of event dictionaries to be delivered.
        router_type (str)   : decides the client to use for sending the event
        host_config (dict)  : contains configurations for the host.
    """
    chunk_size = get_bulk_chunk_size(host_config)
    if chunk_size:
        for start in range(0, len(events), chunk_size):
            dispatch_bulk_chunk.delay(events[start:start + chunk_size], router_type, host_config)
        return
    bulk_send_events(self, events, router_type, host_config)


def get_bulk_chunk_size(host_config):
    """
    Return the chunk size configured for this host's URL, or None to send the batch in one request.
    """
    chunk_sizes = getattr(settings, 'EVENT_ROUTING_BACKEND_BULK_CHUNK_SIZES', None) or {}
    chunk_size = chunk_sizes.get((host_config or {}).get('url'))
    return int(chunk_size) if chunk_size else None


@shared_task(bind=True, base=PersistOnFailureTask)
def dispatch_bulk_chunk(self, events, router_type, host_config):
    """
    Send one chunk of a batch, so a failure only ever resends that chunk.

    - The receiver refused the content (4xx): the chunk is split in two and each half is sent on
      its own, until the refused statement is alone; that statement is then parked.
    - Connection dropped, timeout or 5xx: retried with a doubling delay.
    - Parked = the task fails and PersistOnFailureTask stores it as a FailedTask row, from which
      it can be replayed with `reapply_tasks`. Nothing is silently dropped.

    PersistOnFailureTask, not the Logged variant: that one logs every argument (the statements
    and the router credentials) each time a chunk is queued.

    Arguments:
        self (object)       : celery task object to perform celery actions
        events (list[dict]) : the chunk of event dictionaries to be delivered.
        router_type (str)   : decides the client to use for sending the event
        host_config (dict)  : contains configurations for the host.
    """
    try:
        client_class = ROUTER_STRATEGY_MAPPING[router_type]
    except KeyError:
        logger.error('Unsupported routing strategy detected: {}'.format(router_type))
        return

    try:
        client_class(**host_config).bulk_send(events)
    except EventRejected as exc:
        if len(events) > 1:
            middle = len(events) // 2
            logger.warning('{} refused a chunk of {} events (status {}); splitting it to isolate the refused '
                           'event(s)'.format(host_config.get('url'), len(events), exc.status_code))
            dispatch_bulk_chunk.delay(events[:middle], router_type, host_config)
            dispatch_bulk_chunk.delay(events[middle:], router_type, host_config)
            return
        logger.error('{} refused event {} (status {}); parked as a failed task'.format(
            host_config.get('url'), events[0].get('id'), exc.status_code))
        raise
    except EventNotDispatched as exc:
        countdown = getattr(settings, 'EVENT_ROUTING_BACKEND_COUNTDOWN', 30) * 2 ** self.request.retries
        logger.warning('Attempt {} to send a chunk of {} events to {} failed; next try in {}s unless retries '
                       'are exhausted'.format(self.request.retries + 1, len(events), host_config.get('url'), countdown))
        raise self.retry(exc=exc, countdown=countdown,
                         max_retries=getattr(settings, 'EVENT_ROUTING_BACKEND_BULK_CHUNK_MAX_RETRIES', 8))


def bulk_send_events(task, events, router_type, host_config):
    """
    Send event to configured client.

    Arguments:
        task (object, optional) : celery task object to perform celery actions
        events (list[dict])     : list of event dictionaries to be delivered.
        router_type (str)       : decides the client to use for sending the event
        host_config (dict)      : contains configurations for the host.
    """
    try:
        client_class = ROUTER_STRATEGY_MAPPING[router_type]
    except KeyError:
        logger.error('Unsupported routing strategy detected: {}'.format(router_type))
        return

    try:
        client = client_class(**host_config)
        client.bulk_send(events)
        logger.debug(
            'Successfully bulk dispatched transformed versions of {} events using client: {}'.format(
                len(events),
                client_class
            )
        )
    except EventNotDispatched as exc:
        logger.exception(
            'Exception occurred while trying to bulk dispatch {} events using client: {}'.format(
                len(events),
                client_class
            ),
            exc_info=True
        )
        # If this function is called synchronously, we want to raise the exception
        # to inform about errors. If it's called asynchronously, we want to retry
        # the celery task till it succeeds or reaches max retries.
        if not task:
            raise exc
        raise task.retry(exc=exc, countdown=getattr(settings, 'EVENT_ROUTING_BACKEND_COUNTDOWN', 30),
                         max_retries=getattr(settings, 'EVENT_ROUTING_BACKEND_MAX_RETRIES', 3))
