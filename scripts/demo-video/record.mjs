/**
 * Record a short end-to-end Launchpad product walkthrough (Chromium video).
 *
 * Usage:
 *   node record.mjs
 * Env:
 *   BASE_URL=http://localhost:3000
 *   API_BASE=http://127.0.0.1:8000/api/v1
 *   DEMO_EMAIL=demovideo@example.com
 *   DEMO_PASSWORD=DemoVideo123!
 */
import { chromium } from 'playwright'
import { mkdirSync, existsSync, readdirSync, copyFileSync, rmSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = join(__dirname, 'out')
const RAW_DIR = join(OUT_DIR, 'raw')
const BASE_URL = process.env.BASE_URL || 'http://localhost:3000'
const API_BASE = process.env.API_BASE || 'http://127.0.0.1:8000/api/v1'
const EMAIL = process.env.DEMO_EMAIL || 'demovideo@example.com'
const PASSWORD = process.env.DEMO_PASSWORD || 'DemoVideo123!'
const TOKEN_KEY = 'launchpad_access_token'
const ORG_KEY = 'launchpad_active_org_id'

async function hold(page, ms = 1800) {
  await page.waitForTimeout(ms)
}

async function apiLogin() {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API login failed (${res.status}): ${text.slice(0, 300)}`)
  }
  return res.json()
}

async function ensureOrg(accessToken) {
  const listRes = await fetch(`${API_BASE}/orgs`, {
    headers: { Accept: 'application/json', Authorization: `Bearer ${accessToken}` },
  })
  if (!listRes.ok) {
    throw new Error(`List orgs failed (${listRes.status})`)
  }
  const orgs = await listRes.json()
  if (Array.isArray(orgs) && orgs.length > 0) {
    return orgs[0]
  }
  const createRes = await fetch(`${API_BASE}/orgs`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ name: "Demo Video's organization" }),
  })
  if (!createRes.ok) {
    const text = await createRes.text()
    throw new Error(`Create org failed (${createRes.status}): ${text.slice(0, 300)}`)
  }
  return createRes.json()
}

async function persistSession(page, { accessToken, orgId }) {
  await page.evaluate(
    ({ tokenKey, orgKey, accessToken: token, orgId: activeOrg }) => {
      localStorage.setItem(tokenKey, token)
      if (activeOrg) localStorage.setItem(orgKey, activeOrg)
    },
    { tokenKey: TOKEN_KEY, orgKey: ORG_KEY, accessToken, orgId },
  )
}

async function gotoAuthed(page, path) {
  await page.goto(`${BASE_URL}${path}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForLoadState('networkidle').catch(() => {})
  // Auth middleware may bounce unauthenticated clients back to login.
  if (page.url().includes('/login')) {
    throw new Error(`Still on login after navigating to ${path}`)
  }
  await hold(page, 1200)
}

async function loginViaUi(page) {
  await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForLoadState('networkidle').catch(() => {})
  await hold(page, 1500)

  const emailInput = page.locator('form input[type="email"]').first()
  await emailInput.waitFor({ state: 'visible', timeout: 15000 })
  await emailInput.fill(EMAIL)

  const passwordInput = page.locator('form input[type="password"]').first()
  await passwordInput.fill(PASSWORD)
  await hold(page, 700)

  // Critical: do NOT match "Sign in with SSO" - only the password form submit.
  const submit = page.locator('form button.lp-btn-primary[type="submit"]').first()
  await submit.click()

  await page.waitForURL(
    (url) => !url.pathname.includes('/login'),
    { timeout: 30000 },
  )
  await hold(page, 1200)
}

mkdirSync(RAW_DIR, { recursive: true })
if (existsSync(RAW_DIR)) {
  for (const name of readdirSync(RAW_DIR)) {
    rmSync(join(RAW_DIR, name), { recursive: true, force: true })
  }
}

const browser = await chromium.launch({
  headless: true,
  args: ['--disable-dev-shm-usage'],
})
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  recordVideo: {
    dir: RAW_DIR,
    size: { width: 1440, height: 900 },
  },
  colorScheme: 'dark',
})
const page = await context.newPage()

try {
  // Pre-create org so middleware does not trap the walkthrough on onboarding.
  const session = await apiLogin()
  const org = await ensureOrg(session.access_token)
  const orgId = String(org.id || org.org_id || '')

  // Show login briefly, then authenticate reliably.
  try {
    await loginViaUi(page)
  } catch (uiErr) {
    console.warn('UI login failed, falling back to token inject:', uiErr.message || uiErr)
    await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded' })
    await persistSession(page, { accessToken: session.access_token, orgId })
    await page.goto(`${BASE_URL}/home`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  }

  // Always seal session after UI login so subsequent routes stay authenticated
  // even if Nuxt state was partially hydrated.
  await persistSession(page, { accessToken: session.access_token, orgId })
  if (!page.url().includes('/home') && !page.url().includes('/onboarding')) {
    await page.goto(`${BASE_URL}/home`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  }
  await page.waitForLoadState('networkidle').catch(() => {})
  await hold(page, 1500)

  // Finish onboarding UI if still shown (org already exists via API).
  if (page.url().includes('/onboarding/org')) {
    await persistSession(page, { accessToken: session.access_token, orgId })
    await page.goto(`${BASE_URL}/home`, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await hold(page, 1500)
  }

  if (page.url().includes('/login')) {
    throw new Error('Authentication failed: still on /login after login + token inject')
  }

  await gotoAuthed(page, '/home')
  await hold(page, 2200)

  await gotoAuthed(page, '/catalog')
  await hold(page, 2200)
  const templateCard = page
    .locator('a,button')
    .filter({ hasText: /fastapi|template|create workspace|use template/i })
    .first()
  if (await templateCard.count()) {
    await templateCard.hover().catch(() => {})
    await hold(page, 1200)
  }

  await gotoAuthed(page, '/launch')
  await hold(page, 2400)
  await page.mouse.wheel(0, 420)
  await hold(page, 1600)
  await page.mouse.wheel(0, -420)
  await hold(page, 1000)

  await gotoAuthed(page, '/environments')
  await hold(page, 2200)
  const envLink = page.locator('a[href*="/environments/"]').first()
  if (await envLink.count()) {
    await envLink.click()
    await page.waitForLoadState('networkidle').catch(() => {})
    if (!page.url().includes('/login')) {
      await hold(page, 2600)
      await page.mouse.wheel(0, 500)
      await hold(page, 1600)
    }
  }

  await gotoAuthed(page, '/workspaces')
  await hold(page, 2200)
  const wsLink = page.locator('a[href*="/workspaces/"]').first()
  if (await wsLink.count()) {
    await wsLink.click()
    await page.waitForLoadState('networkidle').catch(() => {})
    if (!page.url().includes('/login')) {
      await hold(page, 2400)
      await page.mouse.wheel(0, 400)
      await hold(page, 1400)
    }
  }

  await gotoAuthed(page, '/provision')
  await hold(page, 2000)
  await gotoAuthed(page, '/settings')
  await hold(page, 2200)
  await page.mouse.wheel(0, 380)
  await hold(page, 1400)
  await gotoAuthed(page, '/fleet')
  await hold(page, 1800)
  await gotoAuthed(page, '/docs')
  await hold(page, 1800)

  await gotoAuthed(page, '/home')
  await hold(page, 2500)
} catch (err) {
  console.error('Walkthrough error:', err)
  try {
    await page.screenshot({ path: join(OUT_DIR, 'demo-failure.png'), fullPage: true })
  } catch {
    // ignore
  }
  await hold(page, 1500)
  await context.close().catch(() => {})
  await browser.close().catch(() => {})
  process.exit(1)
}

await context.close()
await browser.close()

const webms = readdirSync(RAW_DIR).filter((f) => f.endsWith('.webm'))
if (!webms.length) {
  console.error('No Playwright video produced')
  process.exit(1)
}

const rawPath = join(RAW_DIR, webms[0])
const stamped = join(OUT_DIR, `launchpad-demo-raw-${Date.now()}.webm`)
copyFileSync(rawPath, stamped)

const mp4Path = join(OUT_DIR, 'launchpad-end-to-end-demo.mp4')
const ffmpeg = spawnSync(
  'ffmpeg',
  [
    '-y',
    '-i',
    stamped,
    '-vf',
    'scale=1280:-2,fps=30,format=yuv420p',
    '-c:v',
    'libx264',
    '-preset',
    'fast',
    '-crf',
    '22',
    '-an',
    mp4Path,
  ],
  { encoding: 'utf8' },
)

if (ffmpeg.status !== 0) {
  console.error(ffmpeg.stderr || ffmpeg.stdout)
  console.log('Raw video saved at:', stamped)
  process.exit(1)
}

console.log(JSON.stringify({ raw: stamped, mp4: mp4Path }, null, 2))
