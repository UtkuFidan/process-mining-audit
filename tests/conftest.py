"""Tiny hand-made P2P event log. Each case is built to trigger exactly the controls noted."""

import pandas as pd
import pytest

from pm_audit import controls as c

AFTER, BEFORE, TWO_WAY, CONSIGN = c.AFTER_GR, c.BEFORE_GR, c.TWO_WAY, c.CONSIGNMENT

PO, AP, GR, IR, VI, PAY = (
    c.CREATE_PO,
    c.APPROVAL,
    c.GOODS_RECEIPT,
    c.INVOICE_RECEIPT,
    c.VENDOR_INVOICE,
    c.PAYMENT,
)


def _case(case_id, events, **attrs):
    return [
        {"case_id": case_id, "activity": a, "timestamp": pd.Timestamp(ts), "resource": u, **attrs}
        for a, ts, u in events
    ]


@pytest.fixture
def log():
    rows = []
    # OK: clean flow, different users, business hours, paid in 20 days
    rows += _case(
        "ok",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (AP, "2024-01-02 10:00", "user_2"),
            (GR, "2024-01-10 09:00", "user_3"),
            (IR, "2024-01-12 09:00", "user_4"),
            (PAY, "2024-02-01 09:00", "batch_1"),
        ],
        company="C1",
        vendor="V1",
        item_category=AFTER,
    )
    # inv_first: invoice 5 days before goods receipt
    rows += _case(
        "inv_first",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (IR, "2024-01-05 09:00", "user_4"),
            (GR, "2024-01-10 09:00", "user_3"),
            (PAY, "2024-01-20 09:00", "batch_1"),
        ],
        company="C1",
        vendor="V1",
        item_category=AFTER,
    )
    # pay_first: payment 3 days before invoice receipt; PO created after vendor invoice (maverick)
    rows += _case(
        "pay_first",
        [
            (VI, "2024-01-01 09:00", "NONE"),
            (PO, "2024-01-04 09:00", "user_1"),
            (GR, "2024-01-05 09:00", "user_3"),
            (PAY, "2024-01-06 09:00", "batch_1"),
            (IR, "2024-01-09 09:00", "user_4"),
        ],
        company="C2",
        vendor="V2",
        item_category=BEFORE,
    )
    # sod: user_1 creates + approves (after hours, Saturday) + pays; user_3 books GR and invoice
    rows += _case(
        "sod",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (AP, "2024-01-06 22:30", "user_1"),
            (GR, "2024-01-10 09:00", "user_3"),
            (IR, "2024-01-11 09:00", "user_3"),
            (PAY, "2024-01-12 09:00", "user_1"),
        ],
        company="C2",
        vendor="V2",
        item_category=AFTER,
    )
    # rework: price change after approval and after goods receipt; late payment (60 days)
    rows += _case(
        "rework",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (AP, "2024-01-02 11:00", "user_2"),
            (GR, "2024-01-10 09:00", "user_3"),
            (c.CHANGE_PRICE, "2024-01-15 09:00", "user_1"),
            (IR, "2024-01-16 09:00", "user_4"),
            (PAY, "2024-03-16 09:00", "batch_1"),
        ],
        company="C1",
        vendor="V1",
        item_category=AFTER,
    )
    # no_gr: GR-based verification item invoiced without goods receipt
    rows += _case(
        "no_gr",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (IR, "2024-01-12 09:00", "user_4"),
            (PAY, "2024-01-20 09:00", "batch_1"),
        ],
        company="C1",
        vendor="V1",
        item_category=AFTER,
    )
    # no_inv: payment without any invoice
    rows += _case(
        "no_inv",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (GR, "2024-01-10 09:00", "user_3"),
            (PAY, "2024-01-20 09:00", "batch_1"),
        ],
        company="C2",
        vendor="V2",
        item_category=BEFORE,
    )
    # ok2: same trace as "ok" (variant counting)
    rows += _case(
        "ok2",
        [
            (PO, "2024-02-02 09:00", "user_1"),
            (AP, "2024-02-02 10:00", "user_2"),
            (GR, "2024-02-10 09:00", "user_3"),
            (IR, "2024-02-12 09:00", "user_4"),
            (PAY, "2024-03-01 09:00", "batch_1"),
        ],
        company="C1",
        vendor="V1",
        item_category=AFTER,
    )
    # pay_no_gr: invoice-before-GR item paid with no receipt; created and paid by a batch user
    rows += _case(
        "pay_no_gr",
        [
            (PO, "2024-01-03 09:00", "batch_9"),
            (IR, "2024-01-12 09:00", "user_4"),
            (PAY, "2024-01-30 09:00", "batch_9"),
        ],
        company="C1",
        vendor="V3",
        item_category=BEFORE,
    )
    # two_way: no receipt expected, clean otherwise
    rows += _case(
        "two_way",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (VI, "2024-01-03 00:00", "NONE"),
            (IR, "2024-01-05 09:00", "user_4"),
            (PAY, "2024-01-25 09:00", "batch_1"),
        ],
        company="C2",
        vendor="V3",
        item_category=TWO_WAY,
    )
    # consign: only the controls that apply to consignment stock can see this case
    rows += _case(
        "consign",
        [
            (PO, "2024-01-02 09:00", "user_1"),
            (GR, "2024-01-10 09:00", "user_3"),
            (c.CHANGE_PRICE, "2024-01-12 09:00", "user_1"),
        ],
        company="C1",
        vendor="V3",
        item_category=CONSIGN,
    )
    return pd.DataFrame(rows)
