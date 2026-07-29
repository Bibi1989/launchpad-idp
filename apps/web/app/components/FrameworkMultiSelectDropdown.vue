<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { FRAMEWORK_OPTIONS, type FrameworkOption } from '~/types/provisioning'

const props = withDefaults(
  defineProps<{
    modelValue?: FrameworkOption[]
    disabled?: boolean
    placeholder?: string
  }>(),
  {
    modelValue: () => [],
    disabled: false,
    placeholder: 'Select Frameworks / Stacks',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: FrameworkOption[]]
}>()

const isOpen = ref(false)
const searchQuery = ref('')
const triggerRef = ref<HTMLElement | null>(null)

const dropdownStyle = ref<Record<string, string>>({
  position: 'fixed',
  top: '0px',
  left: '0px',
  width: '320px',
  zIndex: '999999',
})

function updatePosition() {
  if (!triggerRef.value || !isOpen.value) return
  const rect = triggerRef.value.getBoundingClientRect()
  const spaceBelow = window.innerHeight - rect.bottom
  const spaceAbove = rect.top

  let top = rect.bottom + 4
  if (spaceBelow < 320 && spaceAbove > spaceBelow) {
    top = Math.max(8, rect.top - 324)
  }

  dropdownStyle.value = {
    position: 'fixed',
    top: `${top}px`,
    left: `${Math.max(8, Math.min(rect.left, window.innerWidth - 330))}px`,
    width: `${Math.max(rect.width, 300)}px`,
    zIndex: '999999',
  }
}

function toggleDropdown() {
  if (props.disabled) return
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    updatePosition()
  }
}

function closeDropdown() {
  isOpen.value = false
}

function handleWindowScrollOrResize() {
  if (isOpen.value) {
    updatePosition()
  }
}

function handleClickOutside(event: MouseEvent) {
  if (!isOpen.value) return
  const target = event.target as Node
  if (triggerRef.value && triggerRef.value.contains(target)) return
  const panel = document.getElementById('framework-dropdown-panel')
  if (panel && panel.contains(target)) return
  closeDropdown()
}

onMounted(() => {
  window.addEventListener('scroll', handleWindowScrollOrResize, true)
  window.addEventListener('resize', handleWindowScrollOrResize)
  document.addEventListener('mousedown', handleClickOutside)
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', handleWindowScrollOrResize, true)
  window.removeEventListener('resize', handleWindowScrollOrResize)
  document.removeEventListener('mousedown', handleClickOutside)
})

const selectedSet = computed(() => new Set(props.modelValue || []))

function isChecked(id: FrameworkOption): boolean {
  return selectedSet.value.has(id)
}

function toggleOption(id: FrameworkOption) {
  const current = [...(props.modelValue || [])]
  const idx = current.indexOf(id)
  if (idx >= 0) {
    current.splice(idx, 1)
  } else {
    current.push(id)
  }
  emit('update:modelValue', current)
}

function clearAll() {
  emit('update:modelValue', [])
}

function selectAll() {
  emit('update:modelValue', FRAMEWORK_OPTIONS.map((f) => f.id))
}

const categories = [
  { id: 'frontend', title: '⚡ Frontend & Meta-Frameworks' },
  { id: 'python', title: '🐍 Python Backends' },
  { id: 'node', title: '🟢 Node.js Backends' },
  { id: 'backend', title: '☕ JVM, Systems & Generic' },
]

const filteredGroups = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return categories
    .map((cat) => {
      const items = FRAMEWORK_OPTIONS.filter(
        (item) =>
          item.category === cat.id &&
          (q === '' || item.label.toLowerCase().includes(q) || item.id.toLowerCase().includes(q)),
      )
      return { ...cat, items }
    })
    .filter((group) => group.items.length > 0)
})

const selectedSummaryText = computed(() => {
  const list = props.modelValue || []
  if (list.length === 0) return props.placeholder
  if (list.length === 1) {
    const found = FRAMEWORK_OPTIONS.find((f) => f.id === list[0])
    return found ? found.label : list[0]
  }
  const first = FRAMEWORK_OPTIONS.find((f) => f.id === list[0])
  return `${first ? first.label : list[0]} (+${list.length - 1} more)`
})
</script>

<template>
  <div class="relative w-full">
    <button
      ref="triggerRef"
      type="button"
      class="lp-input flex w-full items-center justify-between gap-2 text-left text-xs transition"
      :class="[
        disabled ? 'cursor-not-allowed opacity-50' : 'hover:border-[var(--lp-accent)]',
        isOpen ? 'border-[var(--lp-accent)] ring-1 ring-[var(--lp-accent)]/30' : '',
      ]"
      :disabled="disabled"
      @click="toggleDropdown"
    >
      <span class="truncate font-medium" :class="modelValue.length > 0 ? 'text-[var(--lp-text)]' : 'text-[var(--lp-muted)]'">
        {{ selectedSummaryText }}
      </span>
      <div class="flex items-center gap-1.5 shrink-0">
        <span
          v-if="modelValue.length > 0"
          class="rounded bg-[var(--lp-accent)]/20 px-1.5 py-0.5 text-[10px] font-semibold text-[var(--lp-accent)]"
        >
          {{ modelValue.length }}
        </span>
        <span class="material-symbols-outlined text-sm text-[var(--lp-muted)] transition-transform" :class="{ 'rotate-180': isOpen }">
          expand_more
        </span>
      </div>
    </button>

    <Teleport to="body">
      <div
        v-if="isOpen"
        id="framework-dropdown-panel"
        :style="dropdownStyle"
        class="rounded-xl border border-[var(--lp-line)] bg-[var(--lp-panel)] p-3 shadow-2xl backdrop-blur-md space-y-2.5 max-h-[320px] overflow-y-auto"
      >
        <div class="flex items-center justify-between gap-2 pb-1 border-b border-[var(--lp-line)]">
          <input
            v-model="searchQuery"
            type="text"
            placeholder="Search FastAPI, React, Spring..."
            class="lp-input text-xs py-1 px-2.5 w-full bg-[var(--lp-panel-2)]/60"
            @click.stop
          >
          <div class="flex items-center gap-1.5 text-[10px] font-medium shrink-0">
            <button
              type="button"
              class="text-[var(--lp-accent)] hover:underline"
              @click="selectAll"
            >
              All
            </button>
            <span class="text-[var(--lp-muted)]">•</span>
            <button
              type="button"
              class="text-[var(--lp-muted)] hover:text-[var(--lp-text)]"
              @click="clearAll"
            >
              Clear
            </button>
          </div>
        </div>

        <div v-if="filteredGroups.length === 0" class="py-4 text-center text-xs text-[var(--lp-muted)]">
          No matching frameworks found.
        </div>

        <div v-for="group in filteredGroups" :key="group.id" class="space-y-1">
          <p class="px-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--lp-muted)]">
            {{ group.title }}
          </p>
          <div class="grid grid-cols-1 gap-0.5">
            <label
              v-for="item in group.items"
              :key="item.id"
              class="flex items-center justify-between rounded-lg px-2 py-1 text-xs transition cursor-pointer hover:bg-[var(--lp-accent)]/10"
              :class="{ 'bg-[var(--lp-accent)]/15 text-[var(--lp-accent)] font-medium': isChecked(item.id) }"
            >
              <span class="truncate">{{ item.label }}</span>
              <input
                type="checkbox"
                :checked="isChecked(item.id)"
                class="h-3.5 w-3.5 rounded accent-[var(--lp-accent)]"
                @change="toggleOption(item.id)"
              >
            </label>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
