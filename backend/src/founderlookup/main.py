"""ASGI entry point for the safe local FounderLookup runtime."""

from founderlookup.runtime import create_runtime_app

app = create_runtime_app()
