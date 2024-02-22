"""
Transformers for completion related events.
"""
from django.utils.functional import cached_property
from opaque_keys.edx.keys import UsageKey  # pylint: disable=import-error
from tincan import Activity, ActivityDefinition, LanguageMap, Verb
from xmodule.modulestore.django import modulestore  # pylint: disable=import-error

from event_routing_backends.processors.openedx_filters.decorators import openedx_filter
from event_routing_backends.processors.xapi import constants
from event_routing_backends.processors.xapi.registry import XApiTransformersRegistry
from event_routing_backends.processors.xapi.transformer import XApiTransformer


class BaseCompletionTransformer(XApiTransformer):
    """
    Base transformer for completion events.
    """
    _verb = Verb(
        id=constants.XAPI_VERB_COMPLETED,
        display=LanguageMap({constants.EN: constants.COMPLETED}),
    )
    object_type = None
    object_id = None

    @openedx_filter(
        filter_type="event_routing_backends.processors.xapi.completion_events.base_completion.get_object",
    )
    def get_object(self):
        """
        Get object for xAPI transformed event.

        Returns:
            `Activity`
        """
        if not self.object_type or not self.object_id:
            raise NotImplementedError()

        return Activity(
            id=self.object_id,
            definition=ActivityDefinition(
                type=self.object_type,
            ),
        )


@XApiTransformersRegistry.register("edx.completion_aggregator.completion.chapter")
@XApiTransformersRegistry.register("edx.completion_aggregator.completion.sequential")
class ModuleCompletionTransformer(BaseCompletionTransformer):
    """
    Transformer for events generated when a user completes a section or subsection.
    """
    object_type = constants.XAPI_ACTIVITY_MODULE

    @cached_property
    def object_id(self):
        """This property returns the object identifier for the module completion transformer."""
        return super().get_object_iri("xblock", self.get_data("data.block_id", required=True))


@XApiTransformersRegistry.register("edx.completion_aggregator.completion.vertical")
class LessonCompletionTransformer(ModuleCompletionTransformer):
    """
    Transformer for events generated when a user completes an unit.
    """
    object_type = constants.XAPI_ACTIVITY_LESSON

    def transform(self):
        """
        Return transformed `Statement` object.

        `BaseTransformer`'s `transform` method will return dict containing
        xAPI objects in transformed fields. Here we return a `Statement` object
        constructed using those fields.

        Returns:
            `Statement`
        """
        block_id = self.get_data("data.block_id", required=True)
        usage_key = UsageKey.from_string(block_id)
        vertical = modulestore().get_item(usage_key)

        return {} if vertical.graded else super().transform()


@XApiTransformersRegistry.register("edx.completion_aggregator.completion.course")
class CourseCompletionTransformer(BaseCompletionTransformer):
    """
    Transformer for event generated when a user completes a course.
    """
    object_type = constants.XAPI_ACTIVITY_COURSE

    @cached_property
    def object_id(self):
        """This property returns the object identifier for the course completion transformer."""
        return super().get_object_iri("courses", self.get_data("data.course_id", required=True))

    def get_context_activities(self):
        """The XApiTransformer class implements this method and returns in the parent key
        an activity that contains the course metadata however this is not necessary in
        cases where a transformer uses the course metadata as object since the data is
        redundant and a course cannot be its own parent, therefore this must return None.

        Returns:
            None
        """
        return None
