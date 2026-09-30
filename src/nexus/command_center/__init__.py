"""NEXUS Command Center: a read-only view layer over existing NEXUS records (ADR 0013).

The Command Center reads the resident engineer's state tree, the agents' private memory
stores (read-only), the lab scenario catalog, and bounded diagnostic health, and serves
sanitized view models plus a server-to-client event stream. It records no decision, sends
no notification, calls no model, and touches neither GitHub nor Slack.
"""
