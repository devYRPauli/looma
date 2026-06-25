"""Looma - local-first project memory: turns coding-agent history into resumable context.

Pipeline: Claude Code history -> normalized events -> deterministic extraction
-> WorkItems -> candidate memories -> confidence -> promotion -> resume bundle.

See ARCHITECTURE.md (v3) for the full design. This package implements the narrow
Claude-only slice described in the Phase 1 checklist.
"""

__version__ = "2.1.3"
