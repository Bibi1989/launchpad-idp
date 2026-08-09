"""Organization plan limits (Free / Pro)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import OrgPlan, Organization, Project, ProvisioningWorkspace


@dataclass(frozen=True, slots=True)
class PlanLimits:
    max_projects: int
    max_workspaces: int


PLAN_LIMITS: dict[OrgPlan, PlanLimits] = {
    OrgPlan.FREE: PlanLimits(max_projects=2, max_workspaces=5),
    OrgPlan.PRO: PlanLimits(max_projects=10, max_workspaces=20),
}

PRO_MONTHLY_EUR = 27


def limits_for_plan(plan: OrgPlan | str | None) -> PlanLimits:
    if isinstance(plan, OrgPlan):
        return PLAN_LIMITS.get(plan, PLAN_LIMITS[OrgPlan.FREE])
    raw = (str(plan or "free")).strip().lower()
    try:
        return PLAN_LIMITS[OrgPlan(raw)]
    except ValueError:
        return PLAN_LIMITS[OrgPlan.FREE]


async def count_org_projects(session: AsyncSession, org_id) -> int:
    result = await session.execute(
        select(func.count()).select_from(Project).where(Project.org_id == org_id)
    )
    return int(result.scalar_one())


async def count_org_workspaces(session: AsyncSession, org_id) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ProvisioningWorkspace)
        .where(ProvisioningWorkspace.org_id == org_id)
    )
    return int(result.scalar_one())


async def assert_can_create_project(session: AsyncSession, org: Organization) -> None:
    limits = limits_for_plan(org.plan)
    current = await count_org_projects(session, org.id)
    if current >= limits.max_projects:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "plan_project_limit",
                "message": (
                    f"Plan '{org.plan.value if isinstance(org.plan, OrgPlan) else org.plan}' "
                    f"allows at most {limits.max_projects} project(s). Upgrade to Pro."
                ),
                "plan": org.plan.value if isinstance(org.plan, OrgPlan) else str(org.plan),
                "max_projects": limits.max_projects,
                "current": current,
            },
        )


async def assert_can_create_workspace(session: AsyncSession, org: Organization) -> None:
    limits = limits_for_plan(org.plan)
    current = await count_org_workspaces(session, org.id)
    if current >= limits.max_workspaces:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "plan_workspace_limit",
                "message": (
                    f"Plan '{org.plan.value if isinstance(org.plan, OrgPlan) else org.plan}' "
                    f"allows at most {limits.max_workspaces} workspace(s). Upgrade to Pro."
                ),
                "plan": org.plan.value if isinstance(org.plan, OrgPlan) else str(org.plan),
                "max_workspaces": limits.max_workspaces,
                "current": current,
            },
        )
