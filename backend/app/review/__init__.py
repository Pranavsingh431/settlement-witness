"""The human review workflow.

Separate from `app.reconciliation` on purpose. What the baseline concluded and
what a person did about it are two different kinds of record, and keeping them
in one package would make it easy to write code that treats them as one.
"""
