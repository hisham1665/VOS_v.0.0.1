"""Capabilities: the individual skills the orchestrator can schedule.

Import :mod:`capabilities.registry` and call ``registry.get(name)`` rather than
importing a capability module directly -- the registry is what keeps the
executor decoupled from the capability set.
"""
