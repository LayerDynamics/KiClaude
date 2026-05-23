// Vitest setup — registers @testing-library/jest-dom matchers (toBeInTheDocument,
// toHaveTextContent, …) on Vitest's expect, and clears the DOM between tests.
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})
