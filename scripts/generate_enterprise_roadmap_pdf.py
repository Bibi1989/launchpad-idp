"""Generate Launchpad Enterprise Roadmap PDF."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "Launchpad-Enterprise-Roadmap.pdf"

ACCENT = colors.HexColor("#3b82f6")
MUTED = colors.HexColor("#64748b")
TEXT = colors.HexColor("#0f172a")
LIGHT_BG = colors.HexColor("#f1f5f9")


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontSize=26,
            leading=30,
            textColor=TEXT,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontSize=12,
            leading=16,
            textColor=MUTED,
            spaceAfter=20,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontSize=16,
            leading=20,
            textColor=ACCENT,
            spaceBefore=14,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontSize=13,
            leading=16,
            textColor=TEXT,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            textColor=TEXT,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            leftIndent=14,
            bulletIndent=0,
            textColor=TEXT,
            spaceAfter=4,
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base["Normal"],
            fontSize=8,
            textColor=MUTED,
            alignment=TA_LEFT,
        ),
    }


def p(text: str, style: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(text, styles[style])


def bullet_list(items: list[str], styles: dict[str, ParagraphStyle]) -> list:
    return [p(f"• {item}", "bullet", styles) for item in items]


def table(data: list[list[str]], col_widths: list[float] | None = None) -> Table:
    t = Table(data, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


def on_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(inch, 0.45 * inch, "Launchpad — Enterprise Roadmap")
    canvas.drawRightString(
        letter[0] - inch,
        0.45 * inch,
        f"Page {canvas.getPageNumber()}",
    )
    canvas.restoreState()


def build_pdf() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    styles = build_styles()
    story: list = []

    story.append(p("Launchpad Enterprise Roadmap", "title", styles))
    story.append(
        p(
            f"Strategic features and changes to drive company adoption — {date.today():%B %d, %Y}",
            "subtitle",
            styles,
        )
    )
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 12))

    story.append(p("Executive summary", "h1", styles))
    story.append(
        p(
            "Launchpad already unifies ephemeral previews, multi-cloud IaC workspaces, manifest deploy, "
            "GitHub App integration, org RBAC, OIDC SSO, audit logs, cost caps, drift scanning, and "
            "Dockerfile/CI scaffolding. The gap for company adoption is enterprise trust, golden paths, "
            "and workflow depth—not more isolated features.",
            "body",
            styles,
        )
    )

    story.append(p("Current strengths", "h1", styles))
    story.extend(
        bullet_list(
            [
                "Unified preview + IaC provision + manifest deploy in one portal",
                "Local kind → cloud promotion path for incremental adoption",
                "Governance: namespace quotas, network policies, TTL, soft cost caps",
                "GitHub App: PR comments, commit status, webhook rebuilds",
                "OIDC SSO with group→role mapping and org invites",
                "Audit logs, drift scanning, Dockerfile tooling, workspace IDE + terminal",
                "Multi-cloud: GCP, AWS, Azure, Cloudflare, and local kind",
            ],
            styles,
        )
    )

    story.append(p("Enterprise adoption blockers", "h1", styles))
    story.append(
        table(
            [
                ["Area", "You have", "Companies expect"],
                [
                    "Identity",
                    "OIDC + group→role",
                    "SAML + SCIM, enforced MFA via IdP",
                ],
                [
                    "Governance",
                    "Quotas, TTL, cost caps",
                    "Kyverno/OPA policy packs, prod approval gates",
                ],
                [
                    "Secrets",
                    "Session-injected creds",
                    "Vault / cloud SM + External Secrets",
                ],
                [
                    "Audit",
                    "Per-environment audit API",
                    "Org-wide export, retention, SIEM integration",
                ],
                [
                    "Isolation",
                    "Namespace governance",
                    "Dedicated clusters/cells per team or env class",
                ],
                [
                    "Reliability",
                    "OCI deploy pack",
                    "HA reference arch, backup/restore, SLO monitoring",
                ],
            ],
            col_widths=[1.1 * inch, 2.0 * inch, 2.9 * inch],
        )
    )
    story.append(Spacer(1, 10))
    story.append(
        p(
            "Without these, platform teams treat Launchpad as a dev toy—not something for 200+ engineers.",
            "body",
            styles,
        )
    )

    story.append(PageBreak())

    story.append(p("High-impact differentiators", "h1", styles))

    sections = [
        (
            "1. PR-native preview environments",
            "Your killer wedge. Extend GitHub integration beyond comments and commit status:",
            [
                "Stable preview URL per PR (e.g. pr-42.preview.company.com)",
                "Auto smoke test against preview URL before marking status green",
                "“Open in Launchpad” deep link from GitHub Checks",
                "Auto-destroy on PR merge/close",
                "Optional: post screenshot or Lighthouse score to the PR",
            ],
            "Developers feel this daily. Backstage catalogs don’t do previews; many preview tools don’t do IaC.",
        ),
        (
            "2. Golden path service catalog",
            "Evolve catalog templates into org-approved service definitions:",
            [
                "Service definitions with owner, tier, SLO, runbook, on-call",
                "Scorecards: Dockerfile, CI, monitoring, drift-free",
                "One-click Create service → repo + Dockerfile + CI + K8s + workspace",
                "Template versioning; org-approved stacks only",
            ],
            "Position as: Backstage catalog + preview environments + Terraform generation in one product.",
        ),
        (
            "3. Dependency-aware previews",
            "Preview envs that only deploy the app feel hollow:",
            [
                "Ephemeral Postgres/Redis (operator or Helm subchart)",
                "DB seed from fixture or snapshot",
                "Internal DNS so api and web preview together",
                "Preview stack from monorepo path filters",
            ],
            "Major gap vs Okteto/Humanitec—compelling for real production-like apps.",
        ),
        (
            "4. Promotion pipeline with approvals",
            "Extend promote_environment_to_cloud into a governed path:",
            [
                "preview (PR) → staging (auto on merge) → production (manual approval)",
                "GitHub Environment protection rules generated by Launchpad",
                "Approval UI + audit who approved",
                "Diff of manifest/IaC between stages; rollback to known-good revision",
            ],
            "Companies buy controlled change—not just fast deploys.",
        ),
        (
            "5. FinOps that finance understands",
            "Extend hourly/accrued cost and soft caps:",
            [
                "Cost per team/project/service",
                "Budget alerts in Slack",
                "Estimated cost before launch on the Launch page",
                "Idle detection: pause envs with no traffic for 24h",
                "Monthly export for cloud billing reconciliation",
            ],
            "Platform teams must justify the tool to finance.",
        ),
        (
            "6. Policy-as-code center",
            "Turn governance into an org admin UI:",
            [
                "No hostPath, no privileged, require runAsNonRoot",
                "Block deploy if Trivy/SAST exceeds org threshold",
                "Required labels/annotations on all workloads",
                "Exceptions with expiry and approver",
            ],
            "Sell governed self-service—the phrase enterprise buyers use.",
        ),
        (
            "7. Platform engineering dashboard",
            "Control tower for the internal platform team:",
            [
                "Active previews, median time-to-ready, failure rate",
                "Top failing repos / templates",
                "Drift count by workspace",
                "DORA-style metrics and budget leaderboard",
            ],
            "Makes Launchpad the operational hub—not just a launch button.",
        ),
    ]

    for title, intro, bullets, note in sections:
        story.append(p(title, "h2", styles))
        story.append(p(intro, "body", styles))
        story.extend(bullet_list(bullets, styles))
        story.append(p(f"<i>{note}</i>", "body", styles))
        story.append(Spacer(1, 4))

    story.append(PageBreak())

    story.append(p("Workflow integrations", "h1", styles))
    story.extend(
        bullet_list(
            [
                "Slack/Teams: env ready, failed, TTL expiring, budget exceeded",
                "Jira/Linear: link preview URL to ticket; auto-comment on issue",
                "PagerDuty/Opsgenie: service catalog ties to on-call",
                "Datadog/New Relic: auto-inject tracing env vars in preview manifests",
            ],
            styles,
        )
    )
    story.append(
        p("Each integration is a checkbox on an enterprise RFP.", "body", styles)
    )

    story.append(p("Production-ready polish", "h1", styles))
    story.extend(
        bullet_list(
            [
                "Custom domains + TLS for previews",
                "Environment sharing with scoped reviewer access",
                "Read-only mode for auditors",
                "Public API + Terraform provider for launchpad_environment resources",
                "White-label: org logo, custom docs, branded status pages",
                "Multi-region: launch preview closest to reviewer",
            ],
            styles,
        )
    )

    story.append(p("Suggested roadmap", "h1", styles))
    story.append(
        table(
            [
                ["Phase", "Timeline", "Deliverables"],
                [
                    "Phase 1",
                    "3–6 weeks",
                    "PR preview URLs, auto-destroy on PR close, smoke-test gate, Slack notifications",
                ],
                [
                    "Phase 2",
                    "6–10 weeks",
                    "Service catalog + software templates, ephemeral DB/redis, cost-before-launch",
                ],
                [
                    "Phase 3",
                    "10–16 weeks",
                    "Staging/prod promotion with approvals, policy UI, org audit export",
                ],
                [
                    "Phase 4",
                    "Ongoing",
                    "SCIM, Vault integration, HA multi-tenant deployment, Terraform provider",
                ],
            ],
            col_widths=[0.9 * inch, 1.1 * inch, 4.0 * inch],
        )
    )

    story.append(Spacer(1, 12))
    story.append(p("Competitive positioning", "h1", styles))
    story.append(
        table(
            [
                ["Competitor", "Their strength", "Your angle"],
                [
                    "Backstage",
                    "Catalog, plugins",
                    "Ship previews and infra—not just docs",
                ],
                [
                    "Humanitec / Qovery",
                    "Orchestration, dependencies",
                    "Multi-cloud IaC + previews without vendor lock-in",
                ],
                [
                    "Okteto / DevPod",
                    "Dev environments",
                    "Governed previews + prod promotion path",
                ],
                [
                    "Porter / Coherence",
                    "PaaS simplicity",
                    "Bring your cloud; we generate Terraform/Pulumi",
                ],
            ],
            col_widths=[1.4 * inch, 1.8 * inch, 2.8 * inch],
        )
    )

    story.append(Spacer(1, 12))
    story.append(p("Strategic choice", "h1", styles))
    story.append(
        p(
            "Pick your primary buyer: (1) Platform engineering → catalog, policy, FinOps, SIEM, "
            "promotion pipelines. (2) Product engineering → PR previews, dependencies, GitHub UX. "
            "Lead with PR previews + golden paths for adoption; add policy + FinOps + identity for "
            "enterprise contracts.",
            "body",
            styles,
        )
    )
    story.append(
        p(
            "Unique combo: local kind → cloud workspace → manifest deploy → ephemeral preview → promote, "
            "all governed. Almost no competitor does this end-to-end in one portal.",
            "body",
            styles,
        )
    )

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=letter,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Launchpad Enterprise Roadmap",
        author="Launchpad",
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return OUTPUT


if __name__ == "__main__":
    path = build_pdf()
    print(f"Generated: {path}")
