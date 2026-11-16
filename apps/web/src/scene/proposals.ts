import type { Item, Proposal, SceneDocument } from '../contracts/generated/client/rest'
import { applyPatchToDocument } from './patch'

export function pendingProposalList(proposals: Record<string, Proposal>): Proposal[] {
  return Object.values(proposals)
    .filter((proposal) => proposal.status === 'pending')
    .sort((first, second) => first.createdAt.localeCompare(second.createdAt))
}

export interface PendingOverlay {
  addedItems: Item[]
  updatedItems: Item[]
  removedItemIds: Set<string>
  updatedItemIds: Set<string>
}

/**
 * Apply pending proposals to the confirmed scene for ghost rendering.
 * The server remains authoritative; this is display-only.
 */
export function pendingOverlay(
  scene: SceneDocument,
  proposals: Record<string, Proposal>,
): PendingOverlay {
  const confirmedIds = new Set(scene.items.map((item) => item.id))
  let overlay = scene
  for (const proposal of pendingProposalList(proposals)) {
    try {
      overlay = applyPatchToDocument(overlay, proposal.ops)
    } catch {
      // A malformed proposal is ignored; the server snapshot wins.
    }
  }
  const overlayIds = new Set(overlay.items.map((item) => item.id))
  const addedItems = overlay.items.filter((item) => !confirmedIds.has(item.id))
  const removedItemIds = new Set([...confirmedIds].filter((itemId) => !overlayIds.has(itemId)))
  const updatedItems = overlay.items.filter((item) => {
    if (!confirmedIds.has(item.id)) return false
    const before = scene.items.find((candidate) => candidate.id === item.id)
    return JSON.stringify(before) !== JSON.stringify(item)
  })
  return {
    addedItems,
    updatedItems,
    removedItemIds,
    updatedItemIds: new Set(updatedItems.map((item) => item.id)),
  }
}
