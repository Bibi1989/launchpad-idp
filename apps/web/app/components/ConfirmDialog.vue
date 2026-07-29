<script setup lang="ts">
const open = defineModel<boolean>('open', { default: false })

withDefaults(
  defineProps<{
    title: string
    message: string
    confirmLabel?: string
    cancelLabel?: string
    danger?: boolean
    busy?: boolean
  }>(),
  {
    confirmLabel: 'Yes, destroy',
    cancelLabel: 'No',
    danger: true,
    busy: false,
  },
)

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

const titleId = useId()
const messageId = useId()

function onCancel() {
  if (!open.value) return
  open.value = false
  emit('cancel')
}

function onConfirm() {
  emit('confirm')
}

function onKeydown(event: KeyboardEvent) {
  if (!open.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    onCancel()
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/55 p-4"
      role="presentation"
      @click.self="onCancel"
    >
      <div
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="messageId"
        class="w-full max-w-md space-y-4 rounded-lg border border-[var(--lp-line)] bg-[var(--lp-panel)] p-5 shadow-2xl"
      >
        <h3 :id="titleId" class="text-base font-semibold text-[var(--lp-text)]">
          {{ title }}
        </h3>
        <p :id="messageId" class="text-sm leading-relaxed text-[var(--lp-muted)]">
          {{ message }}
        </p>
        <div class="flex justify-end gap-2">
          <button
            type="button"
            class="lp-btn-ghost px-3 py-1.5 text-[12px]"
            :disabled="busy"
            @click="onCancel"
          >
            {{ cancelLabel }}
          </button>
          <button
            type="button"
            class="px-3 py-1.5 text-[12px]"
            :class="danger ? 'lp-btn-danger' : 'lp-btn-primary'"
            :disabled="busy"
            @click="onConfirm"
          >
            {{ busy ? 'Working…' : confirmLabel }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
