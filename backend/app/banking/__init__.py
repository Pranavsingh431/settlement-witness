"""Bank finality: whether a payout reached the merchant's bank account.

Separate from `app.reconciliation` because it answers a different question about
different evidence. Reconciliation asks whether the provider's own records agree
with each other. This asks whether a bank says the money arrived, and only a
bank statement can answer that.

Nothing here reads or writes a reconciliation decision.
"""
