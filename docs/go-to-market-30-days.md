# Launchpad: 30-day go-to-market checklist

Companion to `docs/Launchpad-ICP-Value-Gaps.md`. Goal: a **stable public demo**, a **short path to first success**, and **5–15 real users**, not a feature explosion.

---

## Week 1 - Make the product showable

- [ ] Prod Settings + Launch path works (no credentials/i18n crashes)
- [ ] Real Kubernetes path for demos (not simulate-only)
- [ ] Landing page shows the end-to-end demo video (`/` → `#demo`)
- [ ] Health check monitored (`/api/v1/health`) and Postgres backup exists
- [ ] One-page invite: login → Settings (optional) → Launch Local → Open app

**Exit:** You can run the happy path on `launchpad-idp.online` without apology.

---

## Week 2 - Package the story

- [ ] 2–3 minute narrative: problem → Launch preview → status / PR → optional provision
- [ ] README + landing CTA point at the same three steps
- [ ] GitHub App install notes for pilots (what they click, what Launchpad does)
- [ ] Record or refresh demo if UI changed (`scripts/demo-video/`)

**Exit:** A stranger can understand the product from landing + video alone.

---

## Week 3 - First users (ICP only)

Invite **5–15** people who match ICP (platform / DevEx at 20–200 eng companies), not open internet.

- [ ] Personal outreach with calendar slot or async Loom of their first launch
- [ ] Watch first session (or ask for screen notes): where they fail
- [ ] Log top 3 blockers and fix them the same week
- [ ] Capture one quote or screenshot (with permission)

**Exit:** At least 3 people completed Launch → Running without you driving the mouse.

---

## Week 4 - Decide what to scale

Based on week-3 feedback:

- [ ] Product: ship the highest-ROI preview gap (stable PR URL, destroy on close, or clearer onboarding)
- [ ] Ops: if concurrent use grows, plan managed Postgres/Redis and worker capacity
- [ ] Distribution: pick **one** channel (community post, pilot offer, or investor/demo day) and ship it
- [ ] Update ICP doc with what users actually cared about

**Exit:** Written list of “next 90 days” priorities tied to real usage, not roadmap fantasy.

---

## Do not do in the first 30 days

- Full SAML/SCIM / Vault / HA rebuild before weekly active launchers exist
- Broad Product Hunt launch on a flaky Settings page
- Building every enterprise checkbox from the roadmap at once

---

## Metrics to watch

| Metric | Why |
|--------|-----|
| Preview success rate (Launch → Running) | Trust |
| Time to first Running for a new user | Onboarding |
| Weekly active launchers | Habit |
| Top error in browser / API logs | What to fix next |

---

## Related

- ICP / value / gaps: `docs/Launchpad-ICP-Value-Gaps.md`
- Enterprise depth: `docs/Launchpad-Enterprise-Roadmap.md`
- Demo recorder: `scripts/demo-video/README.md`
- Landing demo asset: `apps/web/public/videos/launchpad-demo.mp4`
