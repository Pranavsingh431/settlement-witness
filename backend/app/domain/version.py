"""The version of the domain contract.

Every decision records the contract version it was made under, so a stored
decision can still be read after the contract moves on. The version changes when
the meaning of a field, an invariant or a code changes, not when a docstring is
edited.
"""

from typing import Final, Literal

DOMAIN_SCHEMA_VERSION: Final = "2.0.0"
"""Semantic version of the domain contract.

Patch: wording and non-behavioural fixes.
Minor: additions that leave existing decisions readable, such as a new exception
code or a new optional field.
Major: anything that changes the meaning of an existing field, invariant or
code, or that removes one.
"""


type DomainSchemaVersion = Literal["2.0.0"]
"""The version as a type, so a decision cannot claim a contract this code does
not implement.

This repeats the string above because a type checker cannot read a variable into
``Literal``. ``test_version_literal_matches_the_constant`` fails if the two ever
disagree, so the duplication cannot rot silently.
"""
