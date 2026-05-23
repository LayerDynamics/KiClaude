import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  type Board,
  decouplingCheck,
  diffAgainstWorking,
  getWorkspace,
  parseIntent,
  putCirBoard,
  returnPathCheck,
  setWorkspace,
} from '../client'

/** A minimal-but-valid Board for the form-write tests. */
const A_BOARD: Board = {
  cir_version: '0.4',
  name: 'demo',
  description: null,
  components: [],
  nets: [],
  constraints: [],
}

interface FakeResponse {
  ok: boolean
  status: number
  json: () => Promise<unknown>
  text: () => Promise<string>
}

function jsonResponse(body: unknown, status = 200): FakeResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  }
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Read the call's URL + init from a captured fetch invocation. */
function lastCall(): [string, RequestInit] {
  const calls = fetchMock.mock.calls
  expect(calls.length).toBeGreaterThan(0)
  const [url, init] = calls[calls.length - 1] as [string, RequestInit]
  return [url, init ?? {}]
}

describe('putCirBoard', () => {
  it('PUTs JSON Board to /api/cir/board and returns CirState', async () => {
    const cirState = {
      exists: true,
      text: 'name: demo\n',
      parse_error: null,
      board: null,
      validation: null,
      bom: [],
      sourcing: [],
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(cirState))

    const result = await putCirBoard(A_BOARD)

    const [url, init] = lastCall()
    expect(url).toBe('/api/cir/board')
    expect(init.method).toBe('PUT')
    expect(init.body).toBe(JSON.stringify(A_BOARD))
    expect((init.headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    )
    expect(result).toEqual(cirState)
  })

  it('surfaces a 422 validation failure as ApiError carrying the detail', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'board validation error' }, 422),
    )

    await expect(putCirBoard(A_BOARD)).rejects.toMatchObject({
      name: 'ApiError',
      status: 422,
      message: 'board validation error',
    })
  })
})

describe('decouplingCheck', () => {
  it('GETs /api/decoupling_check and returns the typed response', async () => {
    const body = { ok: true, issues: [], ics_with_decoupling_declared: ['U1'] }
    fetchMock.mockResolvedValueOnce(jsonResponse(body))

    const result = await decouplingCheck()
    const [url, init] = lastCall()

    expect(url).toBe('/api/decoupling_check')
    expect(init.method).toBeUndefined() // default GET
    expect(result).toEqual(body)
  })

  it('surfaces a 400 (no working CIR) as ApiError', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'no working CIR' }, 400),
    )
    await expect(decouplingCheck()).rejects.toBeInstanceOf(ApiError)
  })
})

describe('returnPathCheck', () => {
  it('GETs /api/return_path_check', async () => {
    const body = {
      ok: false,
      issues: [
        { code: 'CIR090', severity: 'warning', message: 'no plane', where: null },
      ],
      high_speed_nets: [
        { net: 'I2S_BCLK', net_class: 'high_speed', reference_plane: null },
      ],
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(body))

    const result = await returnPathCheck()
    expect(lastCall()[0]).toBe('/api/return_path_check')
    expect(result.high_speed_nets[0].net).toBe('I2S_BCLK')
  })
})

describe('diffAgainstWorking', () => {
  it('POSTs multipart form-data with the baseline file', async () => {
    const body = {
      identical: false,
      summary: '1 component added',
      name_changed: null,
      components_added: ['U1'],
      components_removed: [],
      component_changes: [],
      nets_added: [],
      nets_removed: [],
      net_changes: [],
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(body))

    const file = new File(['name: before'], 'baseline.yaml', {
      type: 'application/x-yaml',
    })
    const result = await diffAgainstWorking(file)

    const [url, init] = lastCall()
    expect(url).toBe('/api/diff/working')
    expect(init.method).toBe('POST')
    // FormData is the body; the browser sets Content-Type with the boundary,
    // so we explicitly do NOT pass any Content-Type ourselves.
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.body as FormData).get('baseline')).toBeInstanceOf(File)
    expect(((init.body as FormData).get('baseline') as File).name).toBe(
      'baseline.yaml',
    )
    expect(init.headers).toBeUndefined()
    expect(result.summary).toBe('1 component added')
  })

  it('surfaces a non-JSON error body as ApiError with the raw text', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('not json')
      },
      text: async () => 'upstream went away',
    } satisfies FakeResponse)

    await expect(
      diffAgainstWorking(new File(['x'], 'x.yaml')),
    ).rejects.toMatchObject({ status: 500, message: 'upstream went away' })
  })
})

describe('parseIntent (G4-T3)', () => {
  it('POSTs the prompt as JSON and returns the typed draft', async () => {
    const body = {
      board: {
        cir_version: '0.4',
        name: 'demo',
        components: [],
        nets: [],
        constraints: [],
      },
      draft_yaml: 'cir_version: "0.4"\nname: demo\n',
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(body))

    const result = await parseIntent('a board with one ESP32-S3')
    const [url, init] = lastCall()
    expect(url).toBe('/api/parse_intent')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ text: 'a board with one ESP32-S3' }))
    expect(result.board.name).toBe('demo')
    expect(result.draft_yaml).toContain('name: demo')
  })

  it('surfaces a 503 (no Anthropic key) as ApiError', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'Set ANTHROPIC_API_KEY' }, 503),
    )
    await expect(parseIntent('anything')).rejects.toMatchObject({
      name: 'ApiError',
      status: 503,
      message: 'Set ANTHROPIC_API_KEY',
    })
  })
})

describe('workspace (G4-T3)', () => {
  it('GETs /api/workspace and returns the typed state', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ path: '/work/board', source: 'persisted' }),
    )

    const result = await getWorkspace()
    expect(lastCall()[0]).toBe('/api/workspace')
    expect(result.source).toBe('persisted')
    expect(result.path).toBe('/work/board')
  })

  it('POSTs the new workspace path', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ path: '/work/new', source: 'persisted' }),
    )

    const result = await setWorkspace('/work/new')
    const [url, init] = lastCall()
    expect(url).toBe('/api/workspace')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ path: '/work/new' }))
    expect(result.path).toBe('/work/new')
  })

  it('surfaces a 400 (path not absolute) as ApiError', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: 'workspace path must be absolute' }, 400),
    )
    await expect(setWorkspace('./relative')).rejects.toMatchObject({
      status: 400,
      message: 'workspace path must be absolute',
    })
  })
})
