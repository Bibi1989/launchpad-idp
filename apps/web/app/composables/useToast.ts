export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
  id: number
  type: ToastType
  title: string
  message?: string
  /** Auto-dismiss delay in ms. 0 keeps the toast until dismissed. */
  timeout: number
}

export interface ToastOptions {
  title: string
  message?: string
  type?: ToastType
  timeout?: number
}

const DEFAULT_TIMEOUT: Record<ToastType, number> = {
  success: 4_000,
  info: 4_000,
  warning: 6_000,
  error: 8_000,
}

/**
 * Global, app-wide toast notifications. Rendered once by <ToastHost /> in app.vue.
 * Any page or composable can surface feedback via toast.success / .error / etc.
 */
export function useToast() {
  const toasts = useState<Toast[]>('lp-toasts', () => [])
  const counter = useState<number>('lp-toast-counter', () => 0)

  function dismiss(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  function push(opts: ToastOptions): number {
    const type = opts.type ?? 'info'
    const timeout = opts.timeout ?? DEFAULT_TIMEOUT[type]
    const id = ++counter.value
    const toast: Toast = {
      id,
      type,
      title: opts.title,
      message: opts.message,
      timeout,
    }
    // Cap the visible stack so a burst of events can't fill the screen.
    toasts.value = [...toasts.value, toast].slice(-5)
    if (timeout > 0 && import.meta.client) {
      window.setTimeout(() => dismiss(id), timeout)
    }
    return id
  }

  const success = (title: string, message?: string) =>
    push({ type: 'success', title, message })
  const error = (title: string, message?: string) =>
    push({ type: 'error', title, message })
  const warning = (title: string, message?: string) =>
    push({ type: 'warning', title, message })
  const info = (title: string, message?: string) =>
    push({ type: 'info', title, message })

  function clear() {
    toasts.value = []
  }

  return { toasts, push, success, error, warning, info, dismiss, clear }
}

/** Normalize an unknown thrown value into a human-friendly message. */
export function toastError(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}
