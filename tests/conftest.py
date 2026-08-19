"""Shared pytest configuration for the NodePulse Home Assistant integration.

Makes the repo root importable so ``custom_components.nodepulse`` resolves for
the HA integration tests (``pytest_homeassistant_custom_component`` scans the
repo for ``custom_components`` itself, but being explicit here is harmless and
helps plain-pytest environments). The pure-logic test module installs its own
fake package in ``sys.modules`` and is unaffected.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)