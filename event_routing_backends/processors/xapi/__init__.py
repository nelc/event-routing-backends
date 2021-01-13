"""
xAPI processors and spec implementation.
"""


from edx_toggles.toggles import SettingToggle

# .. toggle_name: XAPI_EVENTS_ENABLED
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Allow sending events to external servers via xAPI.
# .. toggle_warnings: Do not enable sending of xAPI events until there has
#   been a thorough review of PII implications and safeguards put in place to
#   prevent accidental leakage of novel event fields to third parties. See
#   ARCHBOM-1655 for details.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2020-01-01
# .. toggle_target_removal_date: 2021-07-01
# .. toggle_tickets: https://openedx.atlassian.net/browse/ARCHBOM-1658
XAPI_EVENTS_ENABLED = SettingToggle("XAPI_EVENTS_ENABLED", default=False)