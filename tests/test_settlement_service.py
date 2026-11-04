from datetime import datetime
from decimal import Decimal

import pytest

from settlement.models.models import SettlementStatus
from settlement.services.settlement_service import SettlementService


@pytest.fixture
def service():
    return SettlementService()


@pytest.fixture
def period():
    return datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59)


def test_list_settlements_filters_by_merchant_and_status(service, period):
    # Include all four combinations so ignoring either filter fails the test.
    expected = service.calculate_settlement("M-001", *period)
    service.process_settlement(expected.settlement_id)
    service.calculate_settlement("M-001", *period)
    other_completed = service.calculate_settlement("M-002", *period)
    service.process_settlement(other_completed.settlement_id)
    service.calculate_settlement("M-002", *period)

    result = service.list_settlements(merchant_id="M-001", status=SettlementStatus.COMPLETED)

    assert [record.settlement_id for record in result] == [expected.settlement_id]


def test_process_settlement_returns_none_for_unknown_id(service, period):
    existing = service.calculate_settlement("M-001", *period)

    assert service.process_settlement("STL-NOT-FOUND") is None
    assert service.list_settlements() == [existing]
    assert existing.status == SettlementStatus.PENDING
    assert existing.processed_at is None


def test_calculate_settlement_with_no_orders(service, period):
    assert service.get_orders() == []

    record = service.calculate_settlement("M-001", *period)

    assert record.order_count == 0
    for amount in (record.total_sales, record.total_fee, record.net_amount):
        assert isinstance(amount, Decimal)
        assert amount == Decimal("0")
    assert record.merchant_id == "M-001"
    assert record.period_start == period[0]
    assert record.period_end == period[1]
    assert record.status == SettlementStatus.PENDING
    assert record.processed_at is None
    assert service.list_settlements(merchant_id="M-001") == [record]
