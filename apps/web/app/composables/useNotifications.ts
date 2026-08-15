import type { Environment } from '~/types/environment'
import type { ToastType } from '~/composables/useToast'

export type NotificationKind =
  | 'ready'
  | 'failed'
  | 'ttl'
  | 'cost'
  | 'paused'
  | 'info'
  | 'invite'

export interface AppNotification {
  id: string
  kind: NotificationKind
  title: string
  body?: string
  envId?: string
  envName?: string
  /** In-app deep link (invite accept pages, etc.). */
  href?: string
  ts: number
  read: boolean
}

const STORAGE_KEY = 'lp-notifications'
const MAX_STORED = 50

/**
 * Per-environment snapshot used to detect lifecycle transitions across polls.
 * Module-level so the environments list watcher and a single environment page
 * share one baseline - whichever reconciles a change first emits it, the other
 * sees no delta. Client-only (Nuxt plugin + page setup are guarded).
 */
interface EnvSignature {
  status: string
  appReady: boolean
  ttlWarn: boolean
  costCap: boolean
}
const baseline = new Map<string, EnvSignature>()

function signatureOf(env: Environment): EnvSignature {
  return {
    status: env.status,
    appReady: Boolean(env.app_ready),
    ttlWarn: Boolean(env.ttl_warning),
    costCap: Boolean(env.soft_cost_cap_exceeded),
  }
}

const ICON_FOR: Record<NotificationKind, string> = {
  ready: 'rocket_launch',
  failed: 'error',
  ttl: 'timer',
  cost: 'payments',
  paused: 'pause_circle',
  info: 'info',
  invite: 'mail',
}

export function notificationIcon(kind: NotificationKind): string {
  return ICON_FOR[kind]
}

export function useNotifications() {
  const items = useState<AppNotification[]>('lp-notifications', () => [])
  const loaded = useState<boolean>('lp-notifications-loaded', () => false)
  const toast = useToast()

  function persist() {
    if (!import.meta.client) return
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.value.slice(0, MAX_STORED)))
    } catch {
      // Storage full or unavailable - notifications stay in-memory only.
    }
  }

  function hydrate() {
    if (loaded.value || !import.meta.client) return
    loaded.value = true
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY)
      if (raw) {
        const parsed = JSON.parse(raw) as AppNotification[]
        if (Array.isArray(parsed)) items.value = parsed.slice(0, MAX_STORED)
      }
    } catch {
      // Ignore malformed persisted state.
    }
  }

  const unreadCount = computed(() => items.value.filter((n) => !n.read).length)

  function add(
    entry: Omit<AppNotification, 'id' | 'ts' | 'read'> & { id?: string },
  ): AppNotification {
    const notification: AppNotification = {
      ...entry,
      id: entry.id ?? `n-${Date.now()}-${Math.round(Math.random() * 1e6)}`,
      ts: Date.now(),
      read: false,
    }
    items.value = [notification, ...items.value.filter((n) => n.id !== notification.id)].slice(
      0,
      MAX_STORED,
    )
    persist()
    return notification
  }

  /** Insert or refresh a stable notification id without resetting read state. */
  function upsert(
    entry: Omit<AppNotification, 'ts' | 'read'> & { id: string },
  ): AppNotification {
    const existing = items.value.find((n) => n.id === entry.id)
    if (existing) {
      const updated: AppNotification = {
        ...existing,
        kind: entry.kind,
        title: entry.title,
        body: entry.body,
        envId: entry.envId,
        envName: entry.envName,
        href: entry.href,
      }
      items.value = items.value.map((n) => (n.id === entry.id ? updated : n))
      persist()
      return updated
    }
    return add(entry)
  }

  function removeWhere(predicate: (n: AppNotification) => boolean) {
    const next = items.value.filter((n) => !predicate(n))
    if (next.length === items.value.length) return
    items.value = next
    persist()
  }

  function markAllRead() {
    if (!items.value.some((n) => !n.read)) return
    items.value = items.value.map((n) => (n.read ? n : { ...n, read: true }))
    persist()
  }

  function markRead(id: string) {
    items.value = items.value.map((n) => (n.id === id ? { ...n, read: true } : n))
    persist()
  }

  function remove(id: string) {
    items.value = items.value.filter((n) => n.id !== id)
    persist()
  }

  function clear() {
    items.value = []
    persist()
  }

  /**
   * Diff one environment against the last-seen snapshot and raise a toast +
   * notification for each meaningful lifecycle transition. The first time an
   * environment is seen it is recorded silently, so opening an already-running
   * preview never fires a spurious "ready" event.
   */
  function reconcileEnvironment(env: Environment) {
    if (!import.meta.client) return
    const next = signatureOf(env)
    const prev = baseline.get(env.id)
    baseline.set(env.id, next)
    if (!prev) return

    const emit = (
      kind: NotificationKind,
      title: string,
      body: string | undefined,
      toastType: ToastType,
    ) => {
      add({ kind, title, body, envId: env.id, envName: env.name })
      toast.push({ type: toastType, title, message: body })
    }

    if (next.status === 'RUNNING' && next.appReady && !(prev.status === 'RUNNING' && prev.appReady)) {
      emit('ready', 'Preview is ready', `${env.name} is running and reachable.`, 'success')
    }
    if (next.status === 'FAILED' && prev.status !== 'FAILED') {
      emit('failed', 'Preview failed', env.failure_summary || env.error_message || `${env.name} failed to provision.`, 'error')
    }
    // Pause/resume/destroy are user-initiated and already raise action toasts,
    // so we only surface asynchronous transitions (ready/failed/ttl/cost) here.
    if (next.ttlWarn && !prev.ttlWarn) {
      emit('ttl', 'TTL expiring soon', `${env.name} has 30 minutes or less left - extend it to keep it alive.`, 'warning')
    }
    if (next.costCap && !prev.costCap) {
      emit('cost', 'Cost cap reached', `${env.name} hit the soft cost cap. Destroy it or raise the cap.`, 'error')
    }
  }

  /** Seed baselines for a batch of environments without emitting anything. */
  function reconcileMany(envs: Environment[]) {
    for (const env of envs) reconcileEnvironment(env)
  }

  return {
    items,
    unreadCount,
    hydrate,
    add,
    upsert,
    removeWhere,
    markAllRead,
    markRead,
    remove,
    clear,
    reconcileEnvironment,
    reconcileMany,
  }
}
