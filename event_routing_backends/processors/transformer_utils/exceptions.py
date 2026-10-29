"""
Exceptions related to Caliper transformation
"""


class EventNotDispatched(Exception):
    """
    Raise this exception when an event is not dispatched
    """


class EventRejected(EventNotDispatched):
    """
    Raise this exception when the receiver refused the content itself (a 4xx answer).

    Sending the same content again will be refused again, so it must not be retried as is.
    """

    def __init__(self, status_code=None):
        """
        Keep the HTTP status the receiver answered with.
        """
        super().__init__(status_code)
        self.status_code = status_code
