let draggedKindId: string | null = null

export function setCatalogDragKind(kindId: string | null): void {
  draggedKindId = kindId
}

export function getCatalogDragKind(): string | null {
  return draggedKindId
}
