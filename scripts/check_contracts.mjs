#!/usr/bin/env node
/**
 * Fail when regenerating contracts changed or added files.
 *
 * `git status --porcelain` (unlike `git diff`) also catches untracked files,
 * so a newly generated artifact that was never committed fails CI too.
 */

import { execFileSync } from 'node:child_process'

const paths = ['packages/contracts/generated', 'apps/web/src/contracts/generated']

const status = execFileSync('git', ['status', '--porcelain', '--', ...paths], {
  encoding: 'utf8',
})

if (status.trim().length > 0) {
  console.error('Generated contract artifacts are out of date:\n')
  console.error(status.trimEnd())
  console.error('\nRun `pnpm contracts:gen` and commit the result.')
  process.exit(1)
}

console.log('Contract artifacts are up to date.')
