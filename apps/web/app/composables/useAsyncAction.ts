import type { Reactive } from 'vue'
import { reactive, ref } from 'vue'
import type { ToastType } from '~/composables/useToast'

export interface ActionFeedback {
  type?: ToastType
  title: string
  message?: string
}

/** A feedback value, or a plain string treated as the toast title, or nothing to suppress. */
type FeedbackInput = ActionFeedback | string | null | undefined

export interface AsyncActionOptions<TResult> {
  /** Toast shown when the action resolves. Return null/undefined to stay silent. */
  success?: FeedbackInput | ((result: TResult) => FeedbackInput)
  /** Toast shown when the action throws. Defaults to a generic error toast. */
  error?: FeedbackInput | ((err: unknown) => FeedbackInput)
  /** Runs after a successful toast, e.g. to assign the result or navigate. */
  onSuccess?: (result: TResult) => void | Promise<void>
  /** Runs on failure with the resolved message, e.g. to set an inline error ref. */
  onError?: (message: string, err: unknown) => void
}

export type AsyncAction<TArgs extends unknown[], TResult> = Reactive<{
  /** True while the action is in flight; bind to button disabled/label state. */
  pending: boolean
  /** Invoke the action. Returns the result, or undefined if it was busy or failed. */
  run: (...args: TArgs) => Promise<TResult | undefined>
}>

function toFeedback(input: FeedbackInput): ActionFeedback | null {
  if (!input) return null
  return typeof input === 'string' ? { title: input } : input
}

/**
 * Wraps an async action with the busy + try/catch + toast + inline-error ceremony
 * that every mutating handler otherwise repeats. `define(fn, opts)` returns a
 * reactive `{ pending, run }` so template bindings unwrap `pending` correctly
 * (plain `{ pending: Ref }` stays truthy forever in templates).
 */
export function useAsyncAction() {
  const toast = useToast()

  function define<TArgs extends unknown[], TResult>(
    fn: (...args: TArgs) => Promise<TResult>,
    options: AsyncActionOptions<TResult> = {},
  ): AsyncAction<TArgs, TResult> {
    const pending = ref(false)

    async function run(...args: TArgs): Promise<TResult | undefined> {
      if (pending.value) return undefined
      pending.value = true
      try {
        const result = await fn(...args)
        const fb = toFeedback(
          typeof options.success === 'function' ? options.success(result) : options.success,
        )
        if (fb) toast.push({ type: fb.type ?? 'success', title: fb.title, message: fb.message })
        await options.onSuccess?.(result)
        return result
      } catch (err) {
        const fb =
          toFeedback(typeof options.error === 'function' ? options.error(err) : options.error)
          ?? { title: 'Action failed', message: toastError(err, 'Something went wrong.') }
        toast.push({ type: fb.type ?? 'error', title: fb.title, message: fb.message })
        options.onError?.(fb.message ?? fb.title, err)
        return undefined
      } finally {
        pending.value = false
      }
    }

    return reactive({ pending, run })
  }

  return { define }
}
