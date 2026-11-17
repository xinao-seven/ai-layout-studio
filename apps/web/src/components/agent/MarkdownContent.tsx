import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import { marked } from 'marked'
import { useEffect, useMemo, useRef } from 'react'

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

const renderer = new marked.Renderer()

renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
  const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext'
  const highlighted = hljs.highlight(text, { language }).value
  const label = language === 'plaintext' ? 'text' : language
  return [
    '<div class="md-code">',
    '<div class="md-code-head">',
    `<span>${escapeHtml(label)}</span>`,
    '<button type="button" data-copy-code>复制</button>',
    '</div>',
    `<pre><code class="hljs language-${escapeHtml(language)}">${highlighted}</code></pre>`,
    '</div>',
  ].join('')
}

renderer.link = ({ href, title, text }: { href: string; title?: string | null; text: string }) =>
  `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer noopener"${
    title ? ` title="${escapeHtml(title)}"` : ''
  }>${text}</a>`

export function renderMarkdown(content: string): string {
  const html = marked.parse(content, { async: false, breaks: true, gfm: true, renderer })
  return DOMPurify.sanitize(String(html), { ADD_ATTR: ['data-copy-code'] })
}

export function MarkdownContent({ content }: { content: string }) {
  const html = useMemo(() => renderMarkdown(content), [content])
  const ref = useRef<HTMLDivElement | null>(null)

  // Event delegation: one listener for every code block, no per-block handler.
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const onClick = (event: MouseEvent) => {
      const target = (event.target as HTMLElement).closest<HTMLElement>('[data-copy-code]')
      if (!target) return
      const block = target.closest('.md-code')
      const code = block?.querySelector('code')?.textContent ?? ''
      if (!code) return
      void navigator.clipboard
        .writeText(code)
        .then(() => {
          target.textContent = '已复制'
          window.setTimeout(() => {
            target.textContent = '复制'
          }, 1200)
        })
        .catch(() => {
          target.textContent = '复制失败'
        })
    }
    element.addEventListener('click', onClick)
    return () => element.removeEventListener('click', onClick)
  }, [])

  return (
    <div
      className="markdown-content"
      // biome-ignore lint/security/noDangerouslySetInnerHtml: marked output is DOMPurify-sanitized above.
      dangerouslySetInnerHTML={{ __html: html }}
      ref={ref}
    />
  )
}
