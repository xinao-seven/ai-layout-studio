#!/usr/bin/env node
/**
 * Guard the production asset split.
 *
 * Vite writes the application bundle to `dist/assets/*.js|css`, while the API
 * server owns `/assets/models/*` and `/assets/thumbnails/*`. A broad
 * `location /assets/` proxy in nginx would shadow the bundle and white-screen
 * the Docker image. This check fails CI if the proxy is widened or the build
 * stops referencing a bundle.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const nginx = readFileSync(resolve(root, 'apps/web/nginx.conf'), 'utf8')
const indexHtml = readFileSync(resolve(root, 'apps/web/dist/index.html'), 'utf8')

const failures = []
if (/location\s+\/assets\/\s*\{/.test(nginx)) {
  failures.push('nginx.conf proxies all of /assets/, which shadows the Vite bundle')
}
for (const path of ['/assets/models/', '/assets/thumbnails/', '/assets/library/']) {
  if (!nginx.includes(`location ${path}`)) {
    failures.push(`nginx.conf is missing the ${path} proxy`)
  }
}

const bundleRefs = [...indexHtml.matchAll(/\/assets\/[^"]+\.(?:js|css)/g)].map(
  (match) => match[0],
)
if (bundleRefs.length === 0) {
  failures.push('apps/web/dist/index.html does not reference an /assets/*.js|css bundle')
}

if (failures.length > 0) {
  for (const failure of failures) console.error(`ERROR: ${failure}`)
  process.exit(1)
}
console.log(`web asset split ok (${bundleRefs.length} bundle reference(s))`)
