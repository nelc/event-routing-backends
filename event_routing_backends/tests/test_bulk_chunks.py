"""
Tests for chunked bulk delivery: per-chunk retries, isolating refused statements, parking failures.
"""
from http.client import RemoteDisconnected
from unittest.mock import MagicMock, patch

import ddt
from celery_utils.models import FailedTask
from django.test import TestCase, override_settings

from event_routing_backends.processors.transformer_utils.exceptions import EventNotDispatched, EventRejected
from event_routing_backends.tasks import dispatch_bulk_chunk, dispatch_bulk_events
from event_routing_backends.utils.xapi_lrs_client import LrsClient

LRS_URL = 'https://lrs.example.com/xapi'
HOST_CONFIG = {'url': LRS_URL, 'auth_scheme': 'Bearer', 'auth_key': 'test-key'}


def make_events(count):
    return [{'id': f'event-{index}'} for index in range(count)]


def lrs_response(code):
    response = MagicMock()
    response.success = 200 <= code < 300
    response.response.code = code
    return response


@override_settings(EVENT_ROUTING_BACKEND_BULK_CHUNK_SIZES={LRS_URL: 100})
class TestDispatchBulkEventsChunking(TestCase):
    """
    dispatch_bulk_events splits a batch only for the URLs that are listed.
    """

    @patch('event_routing_backends.tasks.dispatch_bulk_chunk.delay')
    def test_listed_url_is_split_into_chunks(self, mocked_delay):
        events = make_events(250)

        dispatch_bulk_events.delay(events, 'XAPI_LRS', HOST_CONFIG)

        self.assertEqual([len(call.args[0]) for call in mocked_delay.call_args_list], [100, 100, 50])
        self.assertEqual([event for call in mocked_delay.call_args_list for event in call.args[0]], events)

    @patch('event_routing_backends.tasks.dispatch_bulk_chunk.delay')
    @patch('event_routing_backends.tasks.bulk_send_events')
    def test_unlisted_url_keeps_one_request(self, mocked_bulk_send_events, mocked_delay):
        events = make_events(250)
        other_host = dict(HOST_CONFIG, url='http://ralph:8100/xAPI/')

        dispatch_bulk_events.delay(events, 'XAPI_LRS', other_host)

        mocked_delay.assert_not_called()
        self.assertEqual(mocked_bulk_send_events.call_args.args[1], events)


@ddt.ddt
@override_settings(EVENT_ROUTING_BACKEND_COUNTDOWN=30, EVENT_ROUTING_BACKEND_BULK_CHUNK_MAX_RETRIES=3)
class TestDispatchBulkChunk(TestCase):
    """
    dispatch_bulk_chunk retries, splits and parks; it never silently drops a statement.
    """

    @patch('event_routing_backends.utils.xapi_lrs_client.RemoteLRS')
    def test_success_sends_once(self, mocked_remote_lrs):
        mocked_remote_lrs.return_value.save_statements.return_value = lrs_response(200)

        dispatch_bulk_chunk.delay(make_events(100), 'XAPI_LRS', HOST_CONFIG)

        self.assertEqual(mocked_remote_lrs.return_value.save_statements.call_count, 1)
        self.assertFalse(FailedTask.objects.exists())

    @patch('event_routing_backends.utils.xapi_lrs_client.RemoteLRS')
    def test_refused_chunk_isolates_the_bad_statement(self, mocked_remote_lrs):
        delivered = []

        def save_statements(statements):
            if any(statement['id'] == 'event-5' for statement in statements):
                return lrs_response(400)
            delivered.extend(statement['id'] for statement in statements)
            return lrs_response(200)

        mocked_remote_lrs.return_value.save_statements.side_effect = save_statements

        dispatch_bulk_chunk.delay(make_events(8), 'XAPI_LRS', HOST_CONFIG)

        self.assertEqual(sorted(delivered), sorted(f'event-{index}' for index in range(8) if index != 5))
        parked = FailedTask.objects.get()
        self.assertEqual(parked.task_name, 'event_routing_backends.tasks.dispatch_bulk_chunk')
        self.assertEqual(parked.args[0], [{'id': 'event-5'}])

    @patch('event_routing_backends.utils.xapi_lrs_client.RemoteLRS')
    def test_dropped_connection_is_retried_then_succeeds(self, mocked_remote_lrs):
        mocked_remote_lrs.return_value.save_statements.side_effect = [
            RemoteDisconnected('Remote end closed connection without response'),
            lrs_response(200),
        ]

        dispatch_bulk_chunk.delay(make_events(100), 'XAPI_LRS', HOST_CONFIG)

        self.assertEqual(mocked_remote_lrs.return_value.save_statements.call_count, 2)
        self.assertFalse(FailedTask.objects.exists())

    @ddt.data(500, 429)
    @patch('event_routing_backends.utils.xapi_lrs_client.RemoteLRS')
    def test_retryable_failure_is_parked_after_the_last_retry(self, status_code, mocked_remote_lrs):
        mocked_remote_lrs.return_value.save_statements.return_value = lrs_response(status_code)
        events = make_events(100)

        with self.assertLogs('event_routing_backends.tasks', level='WARNING') as logs:
            dispatch_bulk_chunk.delay(events, 'XAPI_LRS', HOST_CONFIG)

        self.assertEqual(mocked_remote_lrs.return_value.save_statements.call_count, 4)
        countdowns = [int(line.split('next try in ')[1].split('s')[0]) for line in logs.output if 'next try in' in line]
        self.assertEqual(countdowns, [30, 60, 120, 240])
        parked = FailedTask.objects.get()
        self.assertEqual(parked.args[0], events)


@ddt.ddt
class TestLrsClientBulkSendErrors(TestCase):
    """
    LrsClient.bulk_send tells a refused payload apart from a failure worth retrying.
    """

    def setUp(self):
        super().setUp()
        self.client = LrsClient(url=LRS_URL, auth_scheme='Bearer', auth_key='test-key')
        self.client.lrs_client = MagicMock()

    @ddt.data(400, 413, 422)
    def test_refusal_raises_event_rejected(self, status_code):
        self.client.lrs_client.save_statements.return_value = lrs_response(status_code)

        with self.assertRaises(EventRejected) as raised:
            self.client.bulk_send(make_events(2))

        self.assertEqual(raised.exception.status_code, status_code)

    @ddt.data(500, 502, 408, 429)
    def test_temporary_failure_raises_event_not_dispatched_only(self, status_code):
        self.client.lrs_client.save_statements.return_value = lrs_response(status_code)

        with self.assertRaises(EventNotDispatched) as raised:
            self.client.bulk_send(make_events(2))

        self.assertNotIsInstance(raised.exception, EventRejected)

    @ddt.data(RemoteDisconnected('closed'), ConnectionResetError(), TimeoutError())
    def test_connection_error_raises_event_not_dispatched(self, error):
        self.client.lrs_client.save_statements.side_effect = error

        with self.assertRaises(EventNotDispatched) as raised:
            self.client.bulk_send(make_events(2))

        self.assertNotIsInstance(raised.exception, EventRejected)

    def test_duplicate_is_still_accepted(self):
        self.client.lrs_client.save_statements.return_value = lrs_response(409)

        self.client.bulk_send(make_events(2))
