/** The knowledge base form of the console: read from a base, written as the body the service takes. */
import type { KnowledgeBase, KnowledgeBaseBody, KnowledgeList } from '@client/core/types'

/** Groups typed as a comma-separated list. */
export const groups = (s: string): string[] => s.split(',').map((x) => x.trim()).filter(Boolean)

export interface Form {
  name: string
  title: string
  description: string
  embeddingModel: string
  dimensions: number
  language: string
  maxChars: number
  overlap: number
  tableMax: number
  topK: number
  minScore: number
  hybrid: boolean
  mmr: boolean
  rerank: boolean
  access: string
  link: string
  retention: string
  urls: string
  connectors: string[]
}

export function formOf(b: KnowledgeBase | null, list: KnowledgeList): Form {
  return {
    name: b?.name ?? '',
    title: b?.title ?? '',
    description: b?.description ?? '',
    embeddingModel: b?.embeddingModel ?? (list.embeddingModels.includes(list.defaultModel) ? list.defaultModel : (list.embeddingModels[0] ?? '')),
    dimensions: b?.dimensions ?? 1024,
    language: b?.language ?? 'simple',
    maxChars: b?.chunking.max_chars ?? 1500,
    overlap: b?.chunking.overlap_chars ?? 200,
    tableMax: b?.chunking.table_max_chars ?? 3000,
    topK: b?.retrieval.top_k ?? 5,
    minScore: b?.retrieval.min_score ?? 0,
    hybrid: b?.retrieval.hybrid ?? true,
    mmr: b?.retrieval.mmr ?? false,
    rerank: b?.retrieval.rerank ?? false,
    access: (b?.access ?? []).join(', '),
    link: b?.linkTemplate ?? '',
    retention: b?.retentionDays != null ? String(b.retentionDays) : '',
    urls: (b?.sources ?? []).filter((s) => s.kind === 'url').map((s) => s.url).join('\n'),
    connectors: (b?.sources ?? []).filter((s) => s.kind === 'connector').map((s) => s.server ?? ''),
  }
}

export function bodyOf(f: Form, base: KnowledgeBase | null): Partial<KnowledgeBaseBody> {
  const urls = f.urls.split('\n').map((x) => x.trim()).filter(Boolean)
  return {
    title: f.title,
    description: f.description,
    embeddingModel: f.embeddingModel || null,
    dimensions: f.dimensions,
    language: f.language,
    chunking: { max_chars: f.maxChars, overlap_chars: f.overlap, table_max_chars: f.tableMax },
    retrieval: { ...(base?.retrieval ?? {}), top_k: f.topK, min_score: f.minScore, hybrid: f.hybrid, mmr: f.mmr, rerank: f.rerank },
    access: groups(f.access),
    linkTemplate: f.link.trim() || null,
    retentionDays: f.retention.trim() ? Number(f.retention) : null,
    sources: [
      ...urls.map((url) => ({ kind: 'url' as const, url })),
      ...f.connectors.map((server) => ({ kind: 'connector' as const, server })),
    ],
  }
}

