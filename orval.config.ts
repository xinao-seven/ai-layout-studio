import { defineConfig } from 'orval'

export default defineConfig({
  restZod: {
    input: {
      target: './packages/contracts/generated/openapi.json',
    },
    output: {
      target: './apps/web/src/contracts/generated/zod/rest.zod.ts',
      client: 'zod',
      mode: 'single',
      clean: true,
      prettier: false,
    },
  },
  restClient: {
    input: {
      target: './packages/contracts/generated/openapi.json',
    },
    output: {
      target: './apps/web/src/contracts/generated/client/rest.ts',
      client: 'fetch',
      mode: 'single',
      clean: true,
      prettier: false,
    },
  },
})