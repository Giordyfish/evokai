"""Confirm labels in Handlebars: ``count`` and ``map`` helpers, the first complete template wins."""

from __future__ import annotations

from typing import Any

import pytest

from evokai.agents.definitions import AgentDef
from evokai.core.errors import BadRequest
from evokai.tools.labels import check_labels, confirm_label, render_label
from tests.conftest import Env

TABLES = {"reasons": {"damaged": "Damaged in transit", "wrong_item": "Wrong item sent"}}
REFUND = [
    'Refund {{count orderIds}} orders as {{map reason "reasons"}}',
    'Refund order {{orderId}} as {{map reason "reasons"}}',
    "Refund order {{orderId}}",
    "Refund {{customer.name}}'s orders",
]


def _agent(labels: dict[str, Any], syntax: str = "handlebars") -> AgentDef:
    return AgentDef.model_validate({
        "name": "helper",
        "instructions": "x",
        "model": "function:x",
        "confirm_labels": labels,
        "confirm_values": TABLES,
        "template_syntax": syntax,
    })


def test_handlebars_labels_choose_first_complete_template() -> None:
    agent = _agent({"order_refund": REFUND})
    label = lambda args: confirm_label(agent, "order_refund", args)  # ruff: ignore[lambda-assignment]
    assert label({"orderIds": ["a", "b"], "reason": "damaged"}) == (
        "Refund 2 orders as Damaged in transit"
    )
    # Empty values count as missing, as before: the next complete template wins.
    assert label({"orderIds": [], "orderId": "o1", "reason": "wrong_item"}) == (
        "Refund order o1 as Wrong item sent"
    )
    assert label({"orderId": "o1", "reason": ""}) == "Refund order o1"
    assert label({"orderId": None, "customer": {"name": "Ada"}}) == "Refund Ada's orders"
    # A dotted path needs its last part too.
    assert label({"customer": {"name": ""}}) == "Run order_refund"
    assert label({}) == "Run order_refund"


def test_map_and_count_helpers() -> None:
    agent = _agent({
        "pick": '{{count ids}} item(s): {{ids}}; reasons {{map reasons "reasons"}}',
        "one": '{{count id}} {{map reason "reasons"}} {{map other "reasons"}}',
        "flag": 'Urgent: {{urgent}}, literal \\{{urgent}}, JSON {"a": 1}',
    })
    args = {"ids": ["x", "y", "z"], "reasons": ["damaged", "unknown"]}
    assert confirm_label(agent, "pick", args) == (
        "3 item(s): x, y, z; reasons Damaged in transit, unknown"
    )
    assert confirm_label(agent, "one", {"id": "x", "reason": "damaged", "other": 7}) == (
        "1 Damaged in transit 7"
    )
    assert confirm_label(agent, "flag", {"urgent": True}) == (
        'Urgent: true, literal {{urgent}}, JSON {"a": 1}'
    )


def test_unknown_table_refused_on_save() -> None:
    check_labels({"order_refund": REFUND}, TABLES)
    with pytest.raises(BadRequest, match="refunds") as e:
        check_labels({"order_refund": 'As {{map reason "refunds"}}'}, TABLES)
    assert e.value.code == "label_template"
    with pytest.raises(BadRequest, match="shout") as e:
        check_labels({"order_refund": "{{shout reason}}"}, TABLES)
    assert e.value.code == "label_template"
    with pytest.raises(BadRequest) as e:
        check_labels({"order_refund": "{{#if reason}}x"}, TABLES)
    assert e.value.code == "label_template"
    with pytest.raises(BadRequest) as e:
        check_labels({"order_refund": "{{map reason table}}"}, TABLES)
    assert e.value.code == "label_template"


async def test_unknown_table_refused_by_the_api(env: Env) -> None:
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    r = await env.client.get("/agent/v1/admin/agents", headers=adm)
    body = next(x for x in r.json()["items"] if x["name"] == "helper")
    body.pop("version")
    body["confirm_labels"] = {"order_refund": 'Refund as {{map reason "refunds"}}'}
    r = await env.client.put("/agent/v1/admin/agents/helper", json=body, headers=adm)
    assert r.status_code == 400 and r.json()["error"] == "label_template", r.text


def test_an_argument_named_like_a_helper_is_never_shadowed() -> None:
    # Handlebars calls a helper for a bare {{count}}: the card would say 1 for count=50.
    with pytest.raises(BadRequest, match=r"this\.count") as e:
        check_labels({"create": "Create {{count}} items"}, TABLES)
    assert e.value.code == "label_template"
    check_labels({"create": "Create {{this.count}} items"}, TABLES)
    agent = _agent({"create": ["Create {{count}} items", "Create {{this.count}} items"]})
    assert confirm_label(agent, "create", {"count": 50}) == "Create 50 items"
    # The log helper would write the arguments to the service's log.
    with pytest.raises(BadRequest) as e:
        check_labels({"create": "{{log count}} Create"}, TABLES)
    assert e.value.code == "label_template"


def test_legacy_labels_unchanged() -> None:
    legacy = [
        "Refund {orderIds|count} orders{onlyUnshipped|map:unshipped} as {reason|map:reasons}",
        "Refund order {orderId} as {reason|map:reasons}",
        "Close {ret.id} {flag}",
    ]
    agent = _agent({"t": legacy}, syntax="legacy")
    args = {"orderIds": ["a", "b"], "reason": "damaged", "onlyUnshipped": True}
    assert confirm_label(agent, "t", args) == "Refund 2 ordersTrue as Damaged in transit"
    assert confirm_label(agent, "t", {"orderId": "o1", "reason": "x"}) == "Refund order o1 as x"
    assert confirm_label(agent, "t", {"ret": {"id": 4}, "flag": False}) == "Close 4 False"
    # Workflow approvals keep the older label grammar.
    assert render_label("Pay {amount}", {"amount": 3}, {}) == "Pay 3"
