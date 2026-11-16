#!/usr/bin/env node
/**
 * M0 smoke test: verifies the running server (and optionally the web dev server).
 *
 *   pnpm smoke
 *   SMOKE_WEB=1 pnpm smoke
 *
 * Override targets with SMOKE_SERVER_URL / SMOKE_WEB_URL.
 */

const serverUrl = (process.env.SMOKE_SERVER_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')
const webUrl = (process.env.SMOKE_WEB_URL ?? 'http://127.0.0.1:5173').replace(/\/$/, '')
const checkWeb = process.env.SMOKE_WEB === '1'

const timeoutMs = 5000
let failures = 0

async function request(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
  return { response, text: await response.text() }
}

function pass(name, detail = '') {
  console.log(`PASS  ${name}${detail ? ` — ${detail}` : ''}`)
}

function fail(name, detail) {
  failures += 1
  console.error(`FAIL  ${name} — ${detail}`)
}

try {
  const { response, text } = await request(`${serverUrl}/api/health`)
  if (!response.ok) {
    fail('server /api/health', `HTTP ${response.status}`)
  } else {
    const body = JSON.parse(text)
    if (body.ok === true && typeof body.version === 'string' && 'model' in body) {
      pass('server /api/health', `version ${body.version}`)
    } else {
      fail('server /api/health', `unexpected body: ${text.slice(0, 200)}`)
    }
  }
} catch (error) {
  fail('server /api/health', error instanceof Error ? error.message : String(error))
}

try {
  const { response } = await request(`${serverUrl}/api/docs`)
  if (response.ok) {
    pass('server /api/docs', `HTTP ${response.status}`)
  } else {
    fail('server /api/docs', `HTTP ${response.status}`)
  }
} catch (error) {
  fail('server /api/docs', error instanceof Error ? error.message : String(error))
}

if (checkWeb) {
  try {
    const { response, text } = await request(webUrl)
    if (response.ok && text.includes('root')) {
      pass('web index', `HTTP ${response.status}`)
    } else {
      fail('web index', `HTTP ${response.status}`)
    }
  } catch (error) {
    fail('web index', error instanceof Error ? error.message : String(error))
  }
}

if (failures > 0) {
  console.error(`\n${failures} smoke check(s) failed.`)
  process.exit(1)
}
console.log('\nAll smoke checks passed.')
