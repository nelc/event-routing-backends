"""
Test the LMS root url taken from the tenant that owns the course org.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from event_routing_backends import helpers
from event_routing_backends.helpers import _load_org_tenant_roots, get_course_tenant_root_url
from event_routing_backends.processors.mixins.base_transformer import BaseTransformerMixin

ORG_ROOTS = {
    'niepd': 'https://niepd.futurex.sa',
    'moe': 'https://moe.futurex.sa',
    'rega': 'https://rega.futurex.sa',
}
NIEPD_COURSE = 'course-v1:NIEPD+prg1-2+2025'


def worker_progress_event(course_id=NIEPD_COURSE):
    """A completion-aggregator event as a Celery worker emits it: no context, no host."""
    return {
        'name': 'openedx.completion_aggregator.progress.course',
        'context': {},
        'data': {'course_id': course_id, 'user_id': 755675, 'percent': 0.01},
    }


def request_event(host, course_id=NIEPD_COURSE):
    """An event emitted while serving a learner request on ``host``."""
    return {
        'name': 'edx.course.enrollment.activated',
        'context': {'course_id': course_id, 'host': host},
        'data': {'course_id': course_id},
    }


@override_settings(
    EVENT_ROUTING_BACKEND_USE_COURSE_TENANT_FOR_LMS_URL=True,
    EVENT_ROUTING_BACKEND_USE_HOST_FOR_LMS_URL=True,
    EVENT_ROUTING_BACKEND_FALLBACK_LMS_ROOT_URL='https://lms.futurex.sa',
    EVENT_ROUTING_BACKEND_ORG_HOST_OVERRIDES={},
)
@patch('event_routing_backends.helpers._load_org_tenant_roots', return_value=ORG_ROOTS)
class TestCourseTenantRootUrl(TestCase):
    """
    get_lms_root_url with EVENT_ROUTING_BACKEND_USE_COURSE_TENANT_FOR_LMS_URL on.
    """

    def setUp(self):
        super().setUp()
        helpers._ORG_TENANT_ROOTS.update({'expires_at': 0, 'roots': {}})  # pylint: disable=protected-access

    @override_settings(LMS_ROOT_URL='https://moe.futurex.sa')
    def test_worker_event_ignores_tenant_rewritten_lms_root_url(self, _load):
        """A worker running with moe's settings still reports the NIEPD course on niepd."""
        transformer = BaseTransformerMixin(worker_progress_event())

        self.assertEqual(transformer.get_lms_root_url(), 'https://niepd.futurex.sa')
        self.assertEqual(
            transformer.get_object_iri('courses', NIEPD_COURSE),
            'https://niepd.futurex.sa/courses/course-v1:NIEPD+prg1-2+2025',
        )

    def test_owning_tenant_wins_over_request_host(self, _load):
        transformer = BaseTransformerMixin(request_event('moe.futurex.sa'))

        self.assertEqual(transformer.get_lms_root_url(), 'https://niepd.futurex.sa')

    def test_course_id_from_event_payload(self, _load):
        event = {'name': 'edx.certificate.created', 'context': {}, 'event': {'course_id': NIEPD_COURSE}}

        self.assertEqual(BaseTransformerMixin(event).get_lms_root_url(), 'https://niepd.futurex.sa')

    @override_settings(LMS_ROOT_URL='https://moe.futurex.sa')
    def test_unowned_org_without_host_uses_fallback(self, _load):
        transformer = BaseTransformerMixin(worker_progress_event('course-v1:webinars+w2023+w2023'))

        self.assertEqual(transformer.get_lms_root_url(), 'https://lms.futurex.sa')

    def test_unowned_org_with_host_uses_host(self, _load):
        transformer = BaseTransformerMixin(request_event('cdn.futurex.sa', 'course-v1:webinars+w2023+w2023'))

        self.assertEqual(transformer.get_lms_root_url(), 'https://cdn.futurex.sa')

    @override_settings(LMS_ROOT_URL='https://moe.futurex.sa')
    def test_event_without_course_uses_fallback(self, _load):
        event = {'name': 'edx.ui.lms.link_clicked', 'context': {}, 'data': {}}

        self.assertEqual(BaseTransformerMixin(event).get_lms_root_url(), 'https://lms.futurex.sa')

    @override_settings(EVENT_ROUTING_BACKEND_ORG_HOST_OVERRIDES={'REGA': 'https://SREI.futurex.sa/'})
    def test_override_wins_over_tenant_map(self, _load):
        transformer = BaseTransformerMixin(worker_progress_event('course-v1:rega+srei100+srei100_2023'))

        self.assertEqual(transformer.get_lms_root_url(), 'https://srei.futurex.sa')

    def test_map_is_cached(self, load):
        get_course_tenant_root_url(NIEPD_COURSE)
        get_course_tenant_root_url('course-v1:moe+CS104+2024')

        load.assert_called_once()

    def test_failed_load_keeps_previous_map(self, load):
        get_course_tenant_root_url(NIEPD_COURSE)
        helpers._ORG_TENANT_ROOTS['expires_at'] = 0  # pylint: disable=protected-access
        load.side_effect = Exception('database gone')

        self.assertEqual(get_course_tenant_root_url(NIEPD_COURSE), 'https://niepd.futurex.sa')

    def test_invalid_course_id(self, _load):
        self.assertIsNone(get_course_tenant_root_url('not-a-course'))


@override_settings(EVENT_ROUTING_BACKEND_USE_HOST_FOR_LMS_URL=True, LMS_ROOT_URL='https://moe.futurex.sa')
@patch('event_routing_backends.processors.mixins.base_transformer.get_course_tenant_root_url')
class TestCourseTenantRootUrlOff(TestCase):
    """
    With the toggle off nothing changes.
    """

    def test_worker_event_keeps_lms_root_url(self, lookup):
        self.assertEqual(BaseTransformerMixin(worker_progress_event()).get_lms_root_url(), 'https://moe.futurex.sa')
        lookup.assert_not_called()

    def test_request_event_keeps_host(self, lookup):
        transformer = BaseTransformerMixin(request_event('niepd.futurex.sa'))

        self.assertEqual(transformer.get_lms_root_url(), 'https://niepd.futurex.sa')
        lookup.assert_not_called()


class TestLoadOrgTenantRoots(TestCase):
    """
    Reading the org to root url map from eox-tenant.
    """

    def test_without_eox_tenant(self):
        with patch('event_routing_backends.helpers.apps.get_model', side_effect=LookupError):
            self.assertEqual(_load_org_tenant_roots(), {})

    def test_reads_tenant_configs(self):
        model = MagicMock()
        model.objects.order_by.return_value.values_list.return_value = [
            (103, {'LMS_ROOT_URL': 'https://moe.futurex.sa', 'course_org_filter': ['moe']}),
            (154, {'LMS_ROOT_URL': 'https://ECDH.futurex.sa/', 'course_org_filter': 'ECDH'}),
            (200, {'LMS_ROOT_URL': 'https://rega.futurex.sa', 'course_org_filter': ['rega']}),
            (201, {'LMS_ROOT_URL': 'https://srei.futurex.sa', 'course_org_filter': ['rega']}),
            (202, {'course_org_filter': ['norooturl']}),
            (203, None),
        ]

        with patch('event_routing_backends.helpers.apps.get_model', return_value=model):
            with self.assertLogs('event_routing_backends.helpers', level='WARNING'):
                roots = _load_org_tenant_roots()

        self.assertEqual(roots, {
            'moe': 'https://moe.futurex.sa',
            'ecdh': 'https://ecdh.futurex.sa',
            'rega': 'https://rega.futurex.sa',
        })
        model.objects.order_by.assert_called_once_with('id')
