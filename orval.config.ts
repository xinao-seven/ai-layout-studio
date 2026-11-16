import { defineConfig } from 'orval'

export default defineConfig({
  rest: {
    input: {
      target: './packages/contracts/generated/openapi.json',
    },
    output: {
      target: './apps/web/src/contracts/generated/rest.zod.ts',
      client: 'zod',
      mode: 'single',
      clean: true,
      prettier: false,
    },
  },
})