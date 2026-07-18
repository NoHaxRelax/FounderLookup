"""ASGI entry point for the FounderLookup API."""

from founderlookup.api import create_app

app = create_app()
