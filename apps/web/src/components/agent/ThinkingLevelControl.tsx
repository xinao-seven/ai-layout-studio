import type { ThinkingLevel } from '../../contracts/generated/client/rest'
import { useUiStore } from '../../store/uiStore'

const LEVEL_LABEL: Record<ThinkingLevel, string> = {
  off: '默认',
  low: '低',
  medium: '中',
  high: '高',
}

export function ThinkingLevelControl() {
  const capabilities = useUiStore((state) => state.capabilities)
  const thinkingLevel = useUiStore((state) => state.thinkingLevel)
  const setThinkingLevel = useUiStore((state) => state.setThinkingLevel)

  if (!capabilities?.reasoning) return null
  const levels = capabilities.thinkingLevels ?? ['off']

  return (
    <label
      className="thinking-control"
      title={
        capabilities.granular
          ? '推理强度：越高越慢，但布局判断更稳'
          : '当前端点只区分开/关，低/中/高等效'
      }
    >
      <span>推理</span>
      <select
        onChange={(event) => setThinkingLevel(event.target.value as ThinkingLevel)}
        value={levels.includes(thinkingLevel) ? thinkingLevel : levels[0]}
      >
        {levels.map((level) => (
          <option key={level} value={level}>
            {LEVEL_LABEL[level]}
          </option>
        ))}
      </select>
    </label>
  )
}
