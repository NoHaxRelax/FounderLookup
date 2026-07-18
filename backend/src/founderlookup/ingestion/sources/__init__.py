"""Source-specific ingestion adapters that satisfy the provider-neutral ports.

Each adapter here implements ``DiscoveryPort`` and/or ``AcquisitionPort`` for one
authoritative public source (for example GitHub developer activity). Adapters
depend only on the ``HttpTransport`` protocol, so contract and unit tests drive
them with a deterministic recorded transport and no network access.
"""
