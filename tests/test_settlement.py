"""
이커머스 정산 시스템 - pytest 테스트 스위트
[AI 활용 CI/CD 교육] Day 2·3 실습 워크북 Part 5

실행:
  PYTHONPATH=src pytest tests/ -v --cov=settlement --cov-report=term-missing

AI 활용 포인트:
  이 파일을 Claude.ai에 붙여넣고 "테스트 케이스를 보강해줘" 라고 물어보세요
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from settlement.main import app
from settlement.main import svc as app_svc
from settlement.models.models import Order, OrderStatus, SettlementStatus
from settlement.services.settlement_service import SettlementService

# ── 픽스처 ────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def svc():
    return SettlementService()


@pytest.fixture
def sample_order():
    return Order(
        order_id=f"TEST-{uuid.uuid4().hex[:6]}",
        merchant_id="M-TEST",
        customer_id="C-001",
        amount=Decimal("100000"),
        fee_rate=Decimal("0.03"),
    )


# ── 모델 단위 테스트 ──────────────────────────────────────────────────


class TestOrderModel:
    def test_fee_amount(self):
        """수수료 3% 계산"""
        o = Order(order_id="T1", merchant_id="M", customer_id="C", amount=Decimal("100000"))
        assert o.fee_amount == Decimal("3000")  # 100,000 × 3%

    def test_net_amount(self):
        """실 정산액 = 매출 - 수수료"""
        o = Order(order_id="T2", merchant_id="M", customer_id="C", amount=Decimal("100000"))
        assert o.net_amount == Decimal("97000")

    def test_default_status_pending(self):
        o = Order(order_id="T3", merchant_id="M", customer_id="C", amount=Decimal("50000"))
        assert o.status == OrderStatus.PENDING

    def test_negative_amount_raises(self):
        with pytest.raises(ValidationError):
            Order(order_id="T4", merchant_id="M", customer_id="C", amount=Decimal("-1"))

    def test_fee_rounding(self):
        """소수점 수수료 반올림 (원 단위)"""
        o = Order(
            order_id="T5",
            merchant_id="M",
            customer_id="C",
            amount=Decimal("33333"),
            fee_rate=Decimal("0.03"),
        )
        # 33333 × 0.03 = 999.99 → 1000 (반올림)
        assert o.fee_amount == Decimal("1000")


# ── 서비스 단위 테스트 ────────────────────────────────────────────────


class TestSettlementService:
    def test_add_and_complete_order(self, svc, sample_order):
        svc.add_order(sample_order)
        done = svc.complete_order(sample_order.order_id)
        assert done is not None
        assert done.status == OrderStatus.COMPLETED
        assert done.completed_at is not None

    def test_complete_nonexistent_returns_none(self, svc):
        assert svc.complete_order("NONE-EXIST") is None

    def test_get_orders_filters_by_merchant(self, svc):
        first = Order(order_id="GET-1", merchant_id="M-A", customer_id="C", amount=Decimal("100"))
        second = Order(order_id="GET-2", merchant_id="M-B", customer_id="C", amount=Decimal("100"))
        svc.add_order(first)
        svc.add_order(second)

        assert svc.get_orders("M-A") == [first]
        assert svc.get_orders() == [first, second]

    def test_calculate_settlement_basic(self, svc):
        """3건 주문 정산 계산 기본 케이스"""
        merchant = "M-CALC"
        amounts = [Decimal("50000"), Decimal("100000"), Decimal("200000")]
        for i, amt in enumerate(amounts):
            o = Order(order_id=f"O-{i}", merchant_id=merchant, customer_id="C", amount=amt)
            svc.add_order(o)
            svc.complete_order(o.order_id)

        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc) + timedelta(hours=1)
        rec = svc.calculate_settlement(merchant, start, end)

        expected_sales = sum(amounts)
        expected_fee = sum(a * Decimal("0.03") for a in amounts)

        assert rec.order_count == 3
        assert rec.total_sales == expected_sales
        # 정수 비교 (양쪽 모두 quantize 결과)
        assert rec.total_fee.quantize(Decimal("1")) == expected_fee.quantize(Decimal("1"))
        assert rec.net_amount == expected_sales - rec.total_fee
        assert rec.status == SettlementStatus.PENDING

    def test_pending_orders_excluded(self, svc):
        """PENDING 상태 주문은 정산 제외"""
        o = Order(order_id="PEND-1", merchant_id="M-X", customer_id="C", amount=Decimal("100000"))
        svc.add_order(o)  # 완료 처리 안 함

        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc) + timedelta(hours=1)
        rec = svc.calculate_settlement("M-X", start, end)

        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")

    def test_process_settlement(self, svc, sample_order):
        svc.add_order(sample_order)
        svc.complete_order(sample_order.order_id)

        rec = svc.calculate_settlement(
            "M-TEST",
            datetime.now(timezone.utc) - timedelta(hours=1),
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        done = svc.process_settlement(rec.settlement_id)

        assert done.status == SettlementStatus.COMPLETED
        assert done.processed_at is not None

    def test_list_settlements_filter(self, svc):
        """merchant_id 필터 동작 확인"""
        for m in ["M-A", "M-B"]:
            o = Order(order_id=f"O-{m}", merchant_id=m, customer_id="C", amount=Decimal("10000"))
            svc.add_order(o)
            svc.complete_order(o.order_id)
            svc.calculate_settlement(
                m,
                datetime.now(timezone.utc) - timedelta(hours=1),
                datetime.now(timezone.utc) + timedelta(hours=1),
            )

        result = svc.list_settlements(merchant_id="M-A")
        assert all(r.merchant_id == "M-A" for r in result)

    def test_list_settlements_filters_by_status(self, svc):
        now = datetime.now(timezone.utc)
        first = svc.calculate_settlement("M-A", now - timedelta(hours=1), now + timedelta(hours=1))
        second = svc.calculate_settlement("M-B", now - timedelta(hours=1), now + timedelta(hours=1))
        svc.process_settlement(second.settlement_id)

        assert svc.list_settlements(status=SettlementStatus.PENDING) == [first]
        assert svc.list_settlements(status=SettlementStatus.COMPLETED) == [second]

    def test_process_nonexistent_settlement_returns_none(self, svc):
        assert svc.process_settlement("MISSING") is None

    def test_process_settlement_marks_failed_when_processing_errors(self, svc, monkeypatch):
        now = datetime.now(timezone.utc)
        record = svc.calculate_settlement(
            "M-FAIL", now - timedelta(hours=1), now + timedelta(hours=1)
        )

        def raise_transfer_error(*args, **kwargs):
            raise RuntimeError("은행 이체 실패")

        monkeypatch.setattr(
            "settlement.services.settlement_service.logger.info", raise_transfer_error
        )
        result = svc.process_settlement(record.settlement_id)

        assert result.status == SettlementStatus.FAILED
        assert result.error_message == "은행 이체 실패"


# ── API 통합 테스트 ───────────────────────────────────────────────────


class TestAPI:
    def test_lifespan_seeds_sample_data(self):
        with TestClient(app) as app_client:
            response = app_client.get("/api/v1/orders?merchant_id=M-001")

        assert response.status_code == 200
        assert len(response.json()) >= 5

    def test_health(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_ready(self, client):
        res = client.get("/ready")
        assert res.status_code == 200

    def test_create_order(self, client):
        payload = {
            "order_id": f"API-{uuid.uuid4().hex[:6]}",
            "merchant_id": "M-API",
            "customer_id": "C-001",
            "amount": "75000",
            "fee_rate": "0.03",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        res = client.post("/api/v1/orders", json=payload)
        assert res.status_code == 201
        assert res.json()["order_id"] == payload["order_id"]

    def test_list_settlements(self, client):
        res = client.get("/api/v1/settlements")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_settlements_filter(self, client):
        res = client.get("/api/v1/settlements?merchant_id=M-001")
        assert res.status_code == 200

    def test_complete_order_and_return_completed_status(self, client):
        order_id = f"COMPLETE-{uuid.uuid4().hex[:6]}"
        payload = {
            "order_id": order_id,
            "merchant_id": "M-API",
            "customer_id": "C-001",
            "amount": "10000",
        }
        assert client.post("/api/v1/orders", json=payload).status_code == 201

        response = client.put(f"/api/v1/orders/{order_id}/complete")

        assert response.status_code == 200
        assert response.json()["status"] == "completed"

    def test_complete_missing_order_returns_404(self, client):
        response = client.put("/api/v1/orders/DOES-NOT-EXIST/complete")

        assert response.status_code == 404
        assert response.json()["detail"] == "주문을 찾을 수 없습니다: DOES-NOT-EXIST"

    def test_create_settlement(self, client):
        order_id = f"SETTLE-{uuid.uuid4().hex[:6]}"
        payload = {
            "order_id": order_id,
            "merchant_id": "M-SETTLE",
            "customer_id": "C-001",
            "amount": "10000",
        }
        client.post("/api/v1/orders", json=payload)
        client.put(f"/api/v1/orders/{order_id}/complete")

        now = datetime.now(timezone.utc)
        response = client.post(
            "/api/v1/settlements",
            json={
                "merchant_id": "M-SETTLE",
                "period_start": (now - timedelta(hours=1)).isoformat(),
                "period_end": (now + timedelta(hours=1)).isoformat(),
            },
        )

        assert response.status_code == 201
        assert response.json()["order_count"] == 1

    def test_create_settlement_returns_safe_500_on_service_error(self, client, monkeypatch):
        def raise_calculation_error(*args, **kwargs):
            raise RuntimeError("내부 계산 오류")

        monkeypatch.setattr(app_svc, "calculate_settlement", raise_calculation_error)
        now = datetime.now(timezone.utc)
        response = client.post(
            "/api/v1/settlements",
            json={
                "merchant_id": "M-ERROR",
                "period_start": (now - timedelta(hours=1)).isoformat(),
                "period_end": (now + timedelta(hours=1)).isoformat(),
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "정산 생성 중 오류가 발생했습니다."

    def test_process_missing_settlement_returns_404(self, client):
        response = client.post("/api/v1/settlements/DOES-NOT-EXIST/process")

        assert response.status_code == 404
