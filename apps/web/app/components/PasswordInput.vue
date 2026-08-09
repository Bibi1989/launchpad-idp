<script setup lang="ts">
const model = defineModel<string>({ default: '' })

const props = withDefaults(
  defineProps<{
    id?: string
    label?: string
    autocomplete?: string
    required?: boolean
    disabled?: boolean
    name?: string
  }>(),
  {
    autocomplete: 'current-password',
    required: false,
    disabled: false,
  },
)

const { t } = useI18n()
const visible = ref(false)
const inputId = computed(
  () => props.id || `password-${Math.random().toString(36).slice(2, 9)}`,
)
</script>

<template>
  <label class="block space-y-2" :for="inputId">
    <span v-if="label" class="lp-label">{{ label }}</span>
    <span class="relative block">
      <input
        :id="inputId"
        v-model="model"
        :type="visible ? 'text' : 'password'"
        :name="name"
        :autocomplete="autocomplete"
        :required="required"
        :disabled="disabled"
        class="lp-input w-full pr-11"
      >
      <button
        type="button"
        class="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-[var(--lp-muted)] transition hover:text-[var(--lp-text)]"
        :aria-label="visible ? t('auth.hidePassword') : t('auth.showPassword')"
        :aria-pressed="visible"
        tabindex="0"
        @click="visible = !visible"
      >
        <span class="material-symbols-outlined text-[1.25rem]">
          {{ visible ? 'visibility_off' : 'visibility' }}
        </span>
      </button>
    </span>
  </label>
</template>
