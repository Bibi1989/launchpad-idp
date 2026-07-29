<script setup lang="ts">
export interface TreeNode {
  name: string
  path: string
  type: 'file' | 'directory'
  children?: TreeNode[]
  expanded?: boolean
}

const props = withDefaults(
  defineProps<{
    node: TreeNode
    selectedPath: string | null
    depth: number
    /** `ide` keeps VS Code chrome; `panel` uses Launchpad tokens for the form explorer. */
    tone?: 'ide' | 'panel'
  }>(),
  { tone: 'ide' },
)

const emit = defineEmits<{
  select: [path: string]
  contextmenu: [payload: { event: MouseEvent; path: string; type: 'file' | 'directory' }]
}>()

const expanded = ref(props.node.expanded ?? props.depth < 2)
const isSelected = computed(() => props.selectedPath === props.node.path)
const isPanel = computed(() => props.tone === 'panel')

function onClick() {
  if (props.node.type === 'directory') {
    expanded.value = !expanded.value
    emit('select', props.node.path)
    return
  }
  emit('select', props.node.path)
}

function onContextMenu(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  emit('contextmenu', {
    event,
    path: props.node.path,
    type: props.node.type,
  })
}

function fileIcon(name: string): string {
  const lower = name.toLowerCase()
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return 'data_object'
  if (lower.endsWith('.tf') || lower.endsWith('.tfvars')) return 'deployed_code'
  if (lower.endsWith('.ts') || lower.endsWith('.js')) return 'javascript'
  if (lower.endsWith('.json')) return 'folder_data'
  if (lower.endsWith('.md')) return 'article'
  return 'description'
}
</script>

<template>
  <li>
    <button
      type="button"
      class="group flex w-full items-center gap-0.5 text-left outline-none transition"
      :class="
        isPanel
          ? [
              'rounded-md py-1.5 pr-2 font-mono text-xs leading-5',
              isSelected
                ? 'bg-[var(--lp-accent)]/12 text-[var(--lp-accent)]'
                : 'text-[var(--lp-muted)] hover:bg-[var(--lp-panel)] hover:text-[var(--lp-text)]',
            ]
          : [
              'py-[2px] pr-2 text-[13px] leading-5',
              isSelected ? 'bg-[#37373d] text-[#cccccc]' : 'text-[#cccccc] hover:bg-[#2a2d2e]',
            ]
      "
      :style="{ paddingLeft: `${(isPanel ? 8 : 6) + depth * (isPanel ? 14 : 12)}px` }"
      @click="onClick"
      @contextmenu="onContextMenu"
    >
      <span
        class="inline-flex h-4 w-4 shrink-0 items-center justify-center"
        :class="[
          node.type === 'file' ? 'invisible' : '',
          isPanel ? 'text-[var(--lp-muted)]' : 'text-[#c5c5c5]',
        ]"
      >
        <span class="material-symbols-outlined !text-[14px]">
          {{ expanded ? 'expand_more' : 'chevron_right' }}
        </span>
      </span>
      <span
        class="material-symbols-outlined mr-1.5 shrink-0 !text-[16px]"
        :class="
          node.type === 'directory'
            ? isPanel
              ? 'text-[var(--lp-warn)]'
              : 'text-[#dcb67a]'
            : isPanel
              ? isSelected
                ? 'text-[var(--lp-accent)]'
                : 'text-[var(--lp-ok)]'
              : 'text-[#6a9955]'
        "
      >
        {{
          node.type === 'directory'
            ? expanded
              ? 'folder_open'
              : 'folder'
            : fileIcon(node.name)
        }}
      </span>
      <span class="truncate" :class="isSelected && isPanel ? 'font-semibold' : ''">{{ node.name }}</span>
    </button>
    <ul v-if="node.type === 'directory' && expanded && node.children?.length" class="space-y-0.5">
      <WorkspaceTreeNode
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :selected-path="selectedPath"
        :depth="depth + 1"
        :tone="tone"
        @select="emit('select', $event)"
        @contextmenu="emit('contextmenu', $event)"
      />
    </ul>
  </li>
</template>
