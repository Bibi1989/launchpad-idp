"""Projects limits, invites, and billing webhook plan flips."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.models.domain import Base, OrgPlan, OrgRole, Organization, OrgMembership, User
from app.services.billing import BillingService
from app.services.email import EmailService, _invite_html
from app.services.plans import PLAN_LIMITS, assert_can_create_workspace
from app.services.projects import ProjectService
from app.services.orgs import OrganizationService


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed_org(session: AsyncSession, *, plan: OrgPlan = OrgPlan.FREE) -> tuple[User, Organization]:
    user = User(
        email=f"owner-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("password123"),
        display_name="Owner",
    )
    session.add(user)
    await session.flush()
    org = Organization(slug=f"org-{uuid4().hex[:8]}", name="Acme", plan=plan)
    session.add(org)
    await session.flush()
    session.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
    await session.flush()
    return user, org


@pytest.mark.asyncio
async def test_free_plan_project_limit(session: AsyncSession) -> None:
    user, org = await _seed_org(session, plan=OrgPlan.FREE)
    orgs = OrganizationService(session)
    ctx = await orgs.resolve_context(user=user, org_id=org.id)
    projects = ProjectService(session)
    await projects.create_project(org=ctx, name="One", slug="one")
    await projects.create_project(org=ctx, name="Two", slug="two")
    with pytest.raises(Exception) as exc:
        await projects.create_project(org=ctx, name="Three", slug="three")
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_pro_plan_allows_more_projects(session: AsyncSession) -> None:
    user, org = await _seed_org(session, plan=OrgPlan.PRO)
    orgs = OrganizationService(session)
    ctx = await orgs.resolve_context(user=user, org_id=org.id)
    projects = ProjectService(session)
    for i in range(PLAN_LIMITS[OrgPlan.PRO].max_projects):
        await projects.create_project(org=ctx, name=f"P{i}", slug=f"p{i}")
    with pytest.raises(Exception) as exc:
        await projects.create_project(org=ctx, name="Overflow", slug="overflow")
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_member_cannot_create_project(session: AsyncSession) -> None:
    owner, org = await _seed_org(session, plan=OrgPlan.PRO)
    member = User(
        email=f"member-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("password123"),
        display_name="Member",
    )
    session.add(member)
    await session.flush()
    session.add(OrgMembership(org_id=org.id, user_id=member.id, role=OrgRole.MEMBER))
    await session.flush()
    orgs = OrganizationService(session)
    ctx = await orgs.resolve_context(user=member, org_id=org.id)
    with pytest.raises(Exception) as exc:
        await ProjectService(session).create_project(org=ctx, name="Nope", slug="nope")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_billing_webhook_sets_pro(session: AsyncSession) -> None:
    _user, org = await _seed_org(session, plan=OrgPlan.FREE)
    billing = BillingService(session)
    await billing.handle_webhook_event(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(org.id),
                    "subscription": "sub_123",
                    "customer": "cus_123",
                    "metadata": {"org_id": str(org.id)},
                }
            },
        }
    )
    await session.refresh(org)
    assert org.plan == OrgPlan.PRO
    assert org.stripe_subscription_id == "sub_123"


@pytest.mark.asyncio
async def test_invite_html_contains_cta() -> None:
    html = _invite_html(
        title="Join Acme",
        eyebrow="Organization invite",
        body_lines=["Welcome"],
        cta_label="Accept invitation",
        invite_url="https://app.example/invite/abc",
    )
    assert "https://app.example/invite/abc" in html
    assert "Accept invitation" in html


def test_email_prefers_resend() -> None:
    settings = MagicMock()
    settings.resend_api_key = "re_test"
    settings.resend_from = "Launchpad <hello@example.com>"
    settings.smtp_host = None
    settings.smtp_from = None
    service = EmailService(settings)
    with patch("app.services.email.httpx.post") as post:
        post.return_value = MagicMock(status_code=200, text="{}")
        ok, err = service.send_org_invite(
            to_email="a@example.com",
            org_name="Acme",
            role="member",
            invite_url="https://app.example/invite/tok",
            invited_by="Ada",
        )
    assert ok is True
    assert err is None
    assert post.called
    payload = post.call_args.kwargs["json"]
    assert "html" in payload
    assert "https://app.example/invite/tok" in payload["html"]


@pytest.mark.asyncio
async def test_workspace_limit_helper(session: AsyncSession) -> None:
    _user, org = await _seed_org(session, plan=OrgPlan.FREE)
    # No workspaces yet: should pass.
    await assert_can_create_workspace(session, org)
    # Saturate free workspace limit via counter mock path: create stub rows.
    from app.models.domain import ProvisioningWorkspace

    for i in range(PLAN_LIMITS[OrgPlan.FREE].max_workspaces):
        session.add(
            ProvisioningWorkspace(
                owner_id=_user.id,
                org_id=org.id,
                name=f"w{i}",
                engine="terraform",
                provider="local",
                root_dir=f"/tmp/w{i}",
                status="ready",
            )
        )
    await session.flush()
    with pytest.raises(Exception) as exc:
        await assert_can_create_workspace(session, org)
    assert exc.value.status_code == 402
