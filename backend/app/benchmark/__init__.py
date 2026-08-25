"""Seeded scenario generation and the evaluator harness.

Generates controlled synthetic reconciliation cases, runs them through the real
ingestion and reconciliation paths, and grades the result against an oracle
built from the scenario specification rather than from the system under test.

Everything here produces synthetic data. It is not, and never resembles, any
real merchant's records.
"""
