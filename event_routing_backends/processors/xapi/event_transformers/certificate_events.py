"""
Transformers for certificates related events.
"""
from tincan import Activity, ActivityDefinition, Extensions, LanguageMap, Verb

from event_routing_backends.processors.openedx_filters.decorators import openedx_filter
from event_routing_backends.processors.xapi import constants
from event_routing_backends.processors.xapi.registry import XApiTransformersRegistry
from event_routing_backends.processors.xapi.transformer import XApiTransformer


@XApiTransformersRegistry.register('edx.certificate.created')
class GeneratedCertificatesTransformer(XApiTransformer):
    """
    Base transformer for certificate events.
    """
    _verb = Verb(
        id=constants.XAPI_VERB_EARNED,
        display=LanguageMap({constants.EN: constants.EARNED}),
    )

    @openedx_filter(
        filter_type='event_routing_backends.processors.xapi.certificate_events.generated_certificates.get_object',
    )
    def get_object(self):
        """
        Get object for xAPI transformed event.

        Returns:
            `Activity`
        """
        name = f"Certificate {self.get_data('data.course_id')}"
        object_id = self.get_object_iri('certificates', self.get_data('data.certificate_id', required=True))

        return Activity(
            id=object_id,
            definition=ActivityDefinition(
                type=constants.XAPI_ACTIVITY_CERTIFICATE,
                name=LanguageMap({constants.EN: name}),
                extensions=Extensions({
                    constants.XAPI_ACTIVITY_MODE: self.get_data('data.enrollment_mode')
                })
            ),
        )
