"""App version, single source of truth (pyproject reads it dynamically).

Distinct from config.PROTOCOL_VERSION, which must track prospect-og's VERSION
for server compatibility.

When bumping: the matching `v<version>` tag must not already exist on GitHub,
and updater.is_newer() compares numerically, so this has to be strictly greater
than every published release or the self-updater never offers anything.
"""

APP_VERSION = "1.1.7"
