from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.domain import (
    Base,
    OrgMembership,
    OrgPlan,
    OrgRole,
    Organization,
    User,
)
from app.services.billing import BillingService


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest_asyncio.fixture
async def seed_admin_org(session: AsyncSession) -> tuple[User, Organization]:
    user = User(
        email=f"owner-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("password123"),
        display_name="Owner",
    )
    session.add(user)
    await session.flush()

    org = Organization(
        slug=f"org-{uuid4().hex[:8]}",
        name="Acme",
        plan=OrgPlan.FREE,
        plan_updated_at=datetime.now(UTC),
    )
    session.add(org)
    await session.flush()

    session.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.ADMIN))
    await session.flush()
    await session.commit()
    return user, org


@pytest.mark.asyncio
async def test_checkout_resolves_prod_to_price_when_default_price_missing(
    session: AsyncSession,
    seed_admin_org: tuple[User, Organization],
) -> None:
    user, org = seed_admin_org

    settings = MagicMock()
    settings.stripe_secret_key = "sk_test_123"
    settings.stripe_price_id_pro = "prod_test_123"
    settings.public_app_url = "http://localhost:3000"

    # Mock stripe client with just the calls we hit.
    stripe = MagicMock()
    stripe.Customer.create.return_value = {"id": "cus_test_123"}
    stripe.Product.retrieve.return_value = {"default_price": None}
    stripe.Price.list.return_value = {"data": [{"id": "price_test_456"}]}
    stripe.checkout.Session.create.return_value = {"url": "https://checkout.test/ok"}

    billing = BillingService(session, settings=settings)

    with patch.object(billing, "_require_stripe", return_value=stripe):
        url = await billing.create_checkout_session(user=user, org_id=org.id)

    assert url == "https://checkout.test/ok"
    assert stripe.checkout.Session.create.called
    _, kwargs = stripe.checkout.Session.create.call_args
    assert kwargs["line_items"][0]["price"] == "price_test_456"

