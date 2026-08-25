"""Deterministic CSV ingestion for Settlement Witness.

Ingestion turns a documented CSV document into immutable source facts. It reads,
validates and normalises. It decides nothing about reconciliation, and it never
edits a fact that is already stored.
"""
