# ruff: noqa: E501
"""Synthetic walkthrough documents packaged with the deployed backend.

The public preview must work without a reviewer downloading files and without
relying on Vercel's treatment of repository-root data directories.  These four
documents are byte-for-byte copies of ``data/fixtures/ingestion``; the API test
keeps the two locations in sync.
"""

PAYMENT_EVENTS = b"""provider_event_id,event_id,payment_id,merchant_id,event_type,amount_minor,currency,occurred_at
pe-0001,evt-0001,pay-0001,merch-01,CAPTURE,1000000,INR,2026-08-20T09:15:00+05:30
pe-0002,evt-0002,pay-0002,merch-01,CAPTURE,250000,INR,2026-08-20T10:02:11+05:30
pe-0003,evt-0003,pay-0001,merch-01,REFUND,150000,INR,2026-08-22T14:30:00+05:30
pe-0004,evt-0004,pay-0003,merch-02,CAPTURE,75000,INR,2026-08-20T11:47:59+05:30
pe-0005,evt-0005,pay-0003,merch-02,CHARGEBACK,75000,INR,2026-08-23T08:00:00+05:30
"""

SETTLEMENT_LINES = b"""provider_event_id,settlement_line_id,payout_id,payment_id,gross_minor,fee_minor,tax_minor,adjustment_minor,net_minor,currency,occurred_at
sl-0001,line-0001,payout-0001,pay-0001,1000000,20000,3600,0,976400,INR,2026-08-21T18:00:00+05:30
sl-0002,line-0002,payout-0001,pay-0002,250000,5000,900,0,244100,INR,2026-08-21T18:00:00+05:30
sl-0003,line-0003,payout-0002,pay-0003,75000,1500,270,-500,72730,INR,2026-08-22T18:00:00+05:30
"""

PAYOUTS = b"""provider_event_id,payout_id,merchant_id,net_minor,currency,utr,occurred_at
po-0001,payout-0001,merch-01,1220500,INR,UTR2026082100001,2026-08-21T19:30:00+05:30
po-0002,payout-0002,merch-02,72730,INR,,2026-08-22T19:30:00+05:30
"""

BANK_TRANSACTIONS = b"""provider_event_id,bank_transaction_id,bank_reference,direction,amount_minor,currency,occurred_at
bt-0001,BANKTXN0001,UTR2026082100001,CREDIT,1220500,INR,2026-08-21T20:05:00+05:30
"""
