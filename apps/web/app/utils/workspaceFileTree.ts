import type { WorkspaceFileNode } from '~/types/provisioning'

export interface WorkspaceTreeNodeModel {
  name: string
  path: string
  type: 'file' | 'directory'
  children?: WorkspaceTreeNodeModel[]
  expanded?: boolean
}

/** Build a nested explorer tree from the flat workspace file listing. */
export function buildWorkspaceFileTree(flat: WorkspaceFileNode[]): WorkspaceTreeNodeModel[] {
  const root: WorkspaceTreeNodeModel[] = []
  const dirMap = new Map<string, WorkspaceTreeNodeModel>()

  const ensureDir = (dirPath: string): WorkspaceTreeNodeModel => {
    const existing = dirMap.get(dirPath)
    if (existing) return existing
    const parts = dirPath.split('/')
    const name = parts[parts.length - 1] || dirPath
    const node: WorkspaceTreeNodeModel = {
      name,
      path: dirPath,
      type: 'directory',
      children: [],
      expanded: dirPath.split('/').length <= 3,
    }
    dirMap.set(dirPath, node)
    if (parts.length === 1) {
      root.push(node)
    } else {
      const parentPath = parts.slice(0, -1).join('/')
      ensureDir(parentPath).children!.push(node)
    }
    return node
  }

  for (const item of flat) {
    if (item.type === 'directory') {
      ensureDir(item.path)
      continue
    }
    const parts = item.path.split('/')
    const name = parts[parts.length - 1]!
    const fileNode: WorkspaceTreeNodeModel = { name, path: item.path, type: 'file' }
    if (parts.length === 1) {
      root.push(fileNode)
    } else {
      const parentPath = parts.slice(0, -1).join('/')
      ensureDir(parentPath).children!.push(fileNode)
    }
  }

  const sortNodes = (list: WorkspaceTreeNodeModel[]) => {
    list.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'directory' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    for (const child of list) {
      if (child.children) sortNodes(child.children)
    }
  }
  sortNodes(root)
  return root
}
