/** One-line context for a collapsed tool card, derived from its arguments. */
export function toolHint(name: string, args: Record<string, unknown> | null | undefined): string {
  if (!args) return ''
  const text = (value: unknown): string =>
    typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''

  switch (name) {
    case 'add_item':
      return [text(args.kindId), text(args.variantId)].filter(Boolean).join(' · ')
    case 'add_room':
      return [text(args.name), text(args.type)].filter(Boolean).join(' · ')
    case 'move_item':
    case 'remove_item':
    case 'update_item':
      return text(args.itemId)
    case 'search_catalog':
      return text(args.query)
    case 'model_item': {
      const spec = args.spec as { family?: unknown; name?: unknown } | undefined
      return text(spec?.family) || text(spec?.name) || text(args.name)
    }
    case 'auto_layout':
    case 'verify_layout':
      return text(args.roomId) || '整屋'
    case 'ask_user':
      return text(args.question)
    case 'query_scene':
      return args.includePending === true ? '含待确认提案' : ''
    default: {
      for (const value of Object.values(args)) {
        const candidate = text(value)
        if (candidate) return candidate
      }
      return ''
    }
  }
}

/** Pretty-print tool arguments/results without exploding on exotic payloads. */
export function prettyPayload(value: unknown, maxLength = 4000): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value.slice(0, maxLength)
  try {
    const json = JSON.stringify(value, null, 2)
    return json.length > maxLength ? `${json.slice(0, maxLength)}\n…` : json
  } catch {
    return String(value).slice(0, maxLength)
  }
}
