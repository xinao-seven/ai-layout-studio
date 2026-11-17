import { Check, X } from 'lucide-react'
import type { Proposal } from '../../contracts/generated/client/rest'
import { polygonArea, type Vec2 } from '../../scene/geometry'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'

function proposalTitle(proposal: Proposal): string {
  const op = proposal.ops[0]
  if (!op) return '空提案'
  if (op.op === 'item.add') return `添加 ${op.item.kindId}`
  if (op.op === 'room.add') return `添加房间 ${op.room.name}`
  if (op.op === 'item.remove') return `移除 ${op.itemId}`
  if (op.op === 'item.update') return `更新 ${op.itemId}`
  return op.op
}

function proposalDetail(proposal: Proposal): string {
  const op = proposal.ops[0]
  if (op?.op === 'item.add') {
    const [x, , z] = op.item.transform.position
    return `${op.item.variantId} · (${x.toFixed(2)}, ${z.toFixed(2)}) · ${Math.round(
      (op.item.transform.rotationY * 180) / Math.PI,
    )}°`
  }
  if (op?.op === 'room.add') {
    const area = polygonArea(op.room.polygon as Vec2[])
    return `${op.room.type} · ${op.room.polygon.length} 个顶点 · ${area.toFixed(1)}㎡`
  }
  return proposal.ops.map((candidate) => candidate.op).join(' · ')
}

export function ProposalCard({
  proposal,
  onConfirm,
  onReject,
}: {
  proposal: Proposal
  onConfirm: () => void
  onReject: () => void
}) {
  return (
    <div className="proposal-card">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12px] font-medium">{proposalTitle(proposal)}</p>
          <p className="proposal-detail">{proposalDetail(proposal)}</p>
        </div>
        <Badge tone="accent">待确认</Badge>
      </div>
      {(proposal.dependsOn ?? []).length > 0 ? (
        <p className="proposal-note">
          依赖 {(proposal.dependsOn ?? []).length} 个前序提案，需按顺序确认
        </p>
      ) : null}
      <div className="mt-2 flex gap-1.5">
        <Button className="h-7 flex-1 gap-1 text-[11px]" onClick={onConfirm} size="sm">
          <Check className="h-3 w-3" />
          确认
        </Button>
        <Button className="h-7 gap-1 text-[11px]" onClick={onReject} size="sm" variant="ghost">
          <X className="h-3 w-3" />
          拒绝
        </Button>
      </div>
    </div>
  )
}
