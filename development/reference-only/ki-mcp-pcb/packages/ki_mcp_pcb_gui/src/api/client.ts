// Typed client for the ki-mcp-pcb backend API.
//
// Request/response shapes come from src/api/schema.ts, which is generated
// from the FastAPI OpenAPI schema by `npm run gen:types` — never hand-edit
// schema.ts; change the backend and regenerate.
import type { components } from './schema'

export type CirState = components['schemas']['CirState']
export type ValidationSummary = components['schemas']['ValidationSummary']
export type StageResult = components['schemas']['StageResult']
export type BuildResponse = components['schemas']['BuildResponse']
export type DoctorCheck = components['schemas']['DoctorCheck']
export type Artifact = components['schemas']['Artifact']
// G3: the structured CIR + the result-pane payloads.
export type Board = components['schemas']['Board']
export type Component = components['schemas']['Component']
export type Net = components['schemas']['Net']
export type Stackup = components['schemas']['Stackup']
export type Layer = components['schemas']['Layer']
export type FabTarget = components['schemas']['FabTarget']
export type ValidationIssue = components['schemas']['ValidationIssue']
export type DecouplingCheckResponse =
  components['schemas']['DecouplingCheckResponse']
export type ReturnPathCheckResponse =
  components['schemas']['ReturnPathCheckResponse']
export type DiffResponse = components['schemas']['DiffResponse']
export type ComponentChangeRow = components['schemas']['ComponentChangeRow']
export type NetChangeRow = components['schemas']['NetChangeRow']
export type ImpedanceResponse = components['schemas']['ImpedanceResponse']
export type ImpedanceRow = components['schemas']['ImpedanceRow']
export type BOMRow = components['schemas']['BOMRow']
// G4 types — workspace persistence, intent flow, signoff PATCH.
export type WorkspaceState = components['schemas']['WorkspaceState']
export type ParseIntentResponse = components['schemas']['ParseIntentResponse']
export type Signoff = components['schemas']['Signoff']
export type SignoffPatch = components['schemas']['SignoffPatch']

/** Same-origin in production; Vite dev-proxies `/api` to the backend. */
const API_BASE = '/api'

/** An HTTP-level failure from the backend, carrying its status + detail. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const raw = await response.text()
    let message = raw || response.statusText
    try {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed.detail === 'string') {
        message = parsed.detail
      }
    } catch {
      // The error body wasn't JSON — fall back to the raw text.
    }
    throw new ApiError(response.status, message)
  }
  return (await response.json()) as T
}

/** Fetch the working CIR file and its parsed/validated state. */
export function getCir(): Promise<CirState> {
  return request<CirState>('/cir')
}

/** Replace the working CIR file; rejects (ApiError 400) text that won't parse. */
export function putCir(text: string): Promise<CirState> {
  return request<CirState>('/cir', {
    method: 'PUT',
    body: JSON.stringify({ text }),
  })
}

/**
 * Replace the working CIR from a structured Board (G3 form editor).
 * The backend validates via Pydantic, emits canonical YAML, persists, and
 * returns the same CirState shape `putCir` returns.
 */
export function putCirBoard(board: Board): Promise<CirState> {
  return request<CirState>('/cir/board', {
    method: 'PUT',
    body: JSON.stringify(board),
  })
}

/** Run the CIR030 decoupling-coverage check over the working CIR. */
export function decouplingCheck(): Promise<DecouplingCheckResponse> {
  return request<DecouplingCheckResponse>('/decoupling_check')
}

/** Run the CIR090 return-path check over the working CIR. */
export function returnPathCheck(): Promise<ReturnPathCheckResponse> {
  return request<ReturnPathCheckResponse>('/return_path_check')
}

/** Per-net achievable Zo over the working CIR (G3 result pane). */
export function impedanceCheck(): Promise<ImpedanceResponse> {
  return request<ImpedanceResponse>('/impedance/working')
}

/** Return the working directory the backend resolved (G4). */
export function getWorkspace(): Promise<WorkspaceState> {
  return request<WorkspaceState>('/workspace')
}

/** Persist a new working directory; the path must be absolute. */
export function setWorkspace(path: string): Promise<WorkspaceState> {
  return request<WorkspaceState>('/workspace', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}

/**
 * Turn a natural-language PCB description into a draft CIR (SPEC-1 FR-5).
 * Returns the parsed Board + the YAML draft the GUI previews; 503 when
 * the Anthropic SDK / API key is absent.
 */
export function parseIntent(text: string): Promise<ParseIntentResponse> {
  return request<ParseIntentResponse>('/parse_intent', {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

/**
 * Apply a partial sign-off update to the working CIR (SPEC-1 G4).
 * Only fields the caller sets are written; everything else stays.
 */
export function patchSignoff(patch: SignoffPatch): Promise<CirState> {
  return request<CirState>('/cir/signoff', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

/**
 * Diff an uploaded baseline against the working CIR.
 * Multipart upload — Content-Type is set by the browser from the FormData.
 */
export async function diffAgainstWorking(baseline: File): Promise<DiffResponse> {
  const body = new FormData()
  body.append('baseline', baseline, baseline.name)
  const response = await fetch(`${API_BASE}/diff/working`, {
    method: 'POST',
    body,
  })
  if (!response.ok) {
    const raw = await response.text()
    let message = raw || response.statusText
    try {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed.detail === 'string') {
        message = parsed.detail
      }
    } catch {
      // not JSON — keep the raw text
    }
    throw new ApiError(response.status, message)
  }
  return (await response.json()) as DiffResponse
}

/** Fetch environment health — which pipeline stages can run locally. */
export function getDoctor(): Promise<DoctorCheck[]> {
  return request<DoctorCheck[]>('/doctor')
}

/** List the generated files in the working build directory. */
export function getArtifacts(): Promise<Artifact[]> {
  return request<Artifact[]>('/artifacts')
}

/** Build the download URL for one artifact (by its build-relative path). */
export function artifactUrl(path: string): string {
  return `${API_BASE}/artifacts/${path}`
}

/** Run the full pipeline once (non-streaming). */
export function build(runRoute = false): Promise<BuildResponse> {
  return request<BuildResponse>('/build', {
    method: 'POST',
    body: JSON.stringify({ run_route: runRoute }),
  })
}

export interface BuildStreamHandlers {
  onStage: (stage: StageResult) => void
  onDone: (result: BuildResponse) => void
  onError: (message: string) => void
}

/**
 * Run the pipeline over an SSE stream, delivering each stage as it lands.
 * Returns a function that aborts the stream.
 */
export function streamBuild(
  runRoute: boolean,
  handlers: BuildStreamHandlers,
): () => void {
  const source = new EventSource(
    `${API_BASE}/build/stream?run_route=${String(runRoute)}`,
  )
  let finished = false

  source.addEventListener('stage', (event) => {
    handlers.onStage(JSON.parse(event.data) as StageResult)
  })
  source.addEventListener('done', (event) => {
    finished = true
    handlers.onDone(JSON.parse(event.data) as BuildResponse)
    source.close()
  })
  source.addEventListener('build_error', (event) => {
    finished = true
    const body = JSON.parse(event.data) as { detail?: string }
    handlers.onError(body.detail ?? 'build failed')
    source.close()
  })
  source.addEventListener('error', () => {
    // EventSource's transport-level error. Ignore it once the stream has
    // already delivered a terminal `done`/`build_error` event.
    if (finished) return
    handlers.onError('connection to the build stream was lost')
    source.close()
  })

  return () => source.close()
}
