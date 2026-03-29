"""
Lightweight fallback for environments where pytz is not installed yet.
Render/local installs should use the real pytz package from requirements.txt.
"""

from datetime import timedelta, tzinfo


class _FallbackTimezone(tzinfo):
    def __init__(self, name, offset):
        self._name = name
        self._offset = offset

    def localize(self, value):
        return value.replace(tzinfo=self)

    def utcoffset(self, dt):
        return self._offset

    def dst(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return self._name


def timezone(name):
    if name == "Asia/Kolkata":
        return _FallbackTimezone(name, timedelta(hours=5, minutes=30))
    raise ValueError(f"Unsupported timezone fallback: {name}")
