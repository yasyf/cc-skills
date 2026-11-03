from __future__ import annotations

from long_running import breakage
import pytest


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("It ships sand ahead of api, breaking the order: tenant users' sidebar shows 'index unavailable' until api deploys there.", "It ships sand ahead of api, breaking the order: tenant users' sidebar shows 'index unavailable' until api deploys there."),
        ("Kennedy's release will crash any tenant live-update pod created before Oct 30 when it reaches it.", "Kennedy's release will crash any tenant live-update pod created before Oct 30 when it reaches it."),
        ("Two Acme runs that crashed at 11:31 and 11:36 AM were never retried.", "Two Acme runs that crashed at 11:31 and 11:36 AM were never retried."),
        ("Users on those clusters would see that.", None),
        ("No Acme/Orb/Zeta crashloop group returned.", None),
        ("The signal crash is not in it for the three tenants.", None),
        ("How should the drive learn that customers are failing?", None),
        ("Tenant chats are working again since 2:15 PM after the crash.", None),
        ("The tenant-api incident: Acme users see 'index unavailable'.", None),
        ("tenant-api-fix reports Acme users see 'index unavailable'.", None),
        ("The sand dry run plans 1 replace on the tenants and 0 deletes.", None),
        ("It carries stall-watch opening customer-break only on run crashes, APM errors or an uncovered count.", None),
    ],
    ids=["predicted-by-a-release", "will-crash", "past-customer-failure", "conditional", "negated", "is-not", "question", "recovered", "names-an-incident", "incident-lane", "no-harm", "names-the-alarm"],
)
def test_a_sentence_names_customer_breakage_only_when_customers_fail_now_or_from_a_change_already_moving(text, said):
    assert breakage.customer_break(text) == said
